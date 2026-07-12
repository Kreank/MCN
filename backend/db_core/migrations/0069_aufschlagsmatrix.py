"""EK→VK-Aufschlagsmatrix (pricing.markup_rule + markup_rule_tier).

Hand-SQL nach db/README.md: neue Fachtabellen als RunSQL mit Schutzstandard
(updated_at/Audit/No-Delete/No-Truncate/REVOKE). Muster: 0016/0019/0054.

AUSGANGSLAGE. Der Verkaufspreis ist im Bestand KEINE Zahl, sondern eine Formel
(Migration 0033): `pricing.sale_price_group` trägt Basis (EK|LISTENPREIS) und
Auf-/Abschlag, `pricing.article_sale_price` verbindet ARTIKEL ↔ Gruppe (bzw.
trägt einen Festpreis). Das ist die Kalkulation **je Artikel** — sie verlangt für
JEDEN Artikel eine eigene Zeile. Bei DATANORM-Katalogen (zehntausende Artikel)
ist das nicht pflegbar: dort braucht es eine Regel je **Warengruppe**.

WAS DIESE MIGRATION HINZUFÜGT — und was sie bewusst NICHT tut:

1. **`pricing.markup_rule` ist die Regel-Ebene UNTER der Artikelkalkulation**,
   keine zweite Wahrheit daneben. Sie greift genau dort, wo ein Artikel keine
   eigene VK-Zeile hat (der Normalfall nach einem Katalogimport). Eine bestehende
   `article_sale_price`-Zeile (Festpreis oder zugewiesene VK-Gruppe) gewinnt
   weiterhin — Handpflege schlägt Regel.

2. **Geltungsbereich als Fallback-Kaskade** über nullbare Selektoren:
   `article_id` (Einzelfall) > `product_group` + `supplier_party_id` >
   `product_group` > `supplier_party_id` > alles NULL (**Standardregel**).
   Der partielle Unique-Index (NULLS NOT DISTINCT, PG15+) macht die Auflösung
   physisch eindeutig: je Geltungsbereich höchstens EINE aktive Regel — auch die
   Standardregel gibt es nur einmal.
   `product_group` ist bewusst der Textwert aus `pricing.article.product_group`
   (den der DATANORM-Import aus dem B-Satz/Warengruppe füllt) und kein FK: einen
   Warengruppen-Katalog gibt es im Schema nicht, und der Import kennt beliebige
   Händler-Gruppen. Verglichen wird case-insensitiv (lower()).

3. **`markup_percent` ist ein VORZEICHENBEHAFTETER Aufschlag** (negativ =
   Abschlag) — anders als `sale_price_group` (operator + nicht-negativer Wert).
   Eine Matrixregel wird in Massen gepflegt; zwei Felder für ein Vorzeichen sind
   dort eine Fehlerquelle. `> -100` schließt einen VK ≤ 0 aus.

4. **Rabattstaffel** (`markup_rule_tier`): ab Menge X gilt Aufschlag Y. Gilt die
   höchste Stufe mit `min_quantity <= Menge`.

5. **Mindestmarge** (`min_margin_percent`) ist eine **Handelsspanne auf den VK**:
   (VK − EK) / VK ≥ m/100, also VK ≥ EK / (1 − m/100). Sie ist eine Untergrenze,
   die auch eine Staffel nicht unterschreiten darf, und wirkt nur bei bekanntem
   EK. `< 100` ist zwingend (bei 100 wäre die Untergrenze unendlich).

6. **`article_sale_price.price_origin`** trennt „von Hand gesetzt" von „aus der
   Matrix gerechnet". Die Massenpflege schreibt nur MATRIX-Zeilen fort und rührt
   MANUELL gesetzte Preise NIE an — sonst überschriebe ein Katalogimport die
   Entscheidung eines Kalkulators. Bestandszeilen sind MANUELL (Default).

7. **Kein Automatismus.** Die Matrix rechnet einen VK, sie schreibt von sich aus
   nichts: weder in `pricing.article` noch in eine Belegposition. Eine
   Belegposition bleibt eine eingefrorene Kopie (HANDOFF-Invariante) — die Matrix
   liefert nur den VORSCHLAG beim Anlegen der Position.

Der Geltungsbereich einer Regel ist nach dem INSERT unveränderlich (Trigger):
ein nachträglich umgehängter Selektor würde eine Regel still auf andere Artikel
zeigen lassen. Umzielen = neue Regel, alte auf INAKTIV.
"""
from django.db import migrations

FORWARD_SQL = r"""
CREATE TABLE pricing.markup_rule (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name                text NOT NULL CHECK (btrim(name) <> ''),

    -- Geltungsbereich (alle NULL = Standardregel/Fallback)
    article_id          uuid NULL REFERENCES pricing.article (id),
    product_group       text NULL CHECK (product_group IS NULL OR btrim(product_group) <> ''),
    supplier_party_id   uuid NULL REFERENCES identity.party (id),
    -- Der Einzelfall ist vollständig spezifisch: eine Artikelregel trägt keinen
    -- weiteren Selektor (sonst wäre die Rangfolge nicht mehr eindeutig).
    CONSTRAINT markup_rule_article_exclusive CHECK (
        article_id IS NULL OR (product_group IS NULL AND supplier_party_id IS NULL)
    ),

    calc_basis          text NOT NULL DEFAULT 'EK'
                        CHECK (calc_basis IN ('EK', 'LISTENPREIS')),
    -- Aufschlag in Prozent auf die Basis; negativ = Abschlag.
    markup_percent      numeric(9,3) NOT NULL CHECK (markup_percent > -100),
    -- Mindestmarge (Handelsspanne auf den VK) in Prozent; NULL = keine Untergrenze.
    min_margin_percent  numeric(9,3) NULL
                        CHECK (min_margin_percent IS NULL
                               OR (min_margin_percent >= 0 AND min_margin_percent < 100)),

    status              text NOT NULL DEFAULT 'AKTIV'
                        CHECK (status IN ('AKTIV', 'INAKTIV')),
    version             integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

-- Je Geltungsbereich höchstens EINE aktive Regel. NULLS NOT DISTINCT (PG15+)
-- lässt auch die Standardregel (alle Selektoren NULL) nur einmal zu; ohne das
-- wäre die Auflösung mehrdeutig und der VK nicht mehr reproduzierbar.
CREATE UNIQUE INDEX uq_markup_rule_scope
    ON pricing.markup_rule (article_id, lower(product_group), supplier_party_id)
    NULLS NOT DISTINCT
    WHERE status = 'AKTIV';

CREATE INDEX idx_markup_rule_article
    ON pricing.markup_rule (article_id)
    WHERE article_id IS NOT NULL;
CREATE INDEX idx_markup_rule_gruppe
    ON pricing.markup_rule (lower(product_group))
    WHERE product_group IS NOT NULL;

CREATE TRIGGER trg_markup_rule_updated_at
    BEFORE UPDATE ON pricing.markup_rule
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_markup_rule_audit
    AFTER UPDATE ON pricing.markup_rule
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_markup_rule_no_delete
    BEFORE DELETE ON pricing.markup_rule
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_markup_rule_no_truncate
    BEFORE TRUNCATE ON pricing.markup_rule
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON pricing.markup_rule FROM PUBLIC;

-- Der Geltungsbereich ist nach dem INSERT unveränderlich: eine Regel, die
-- nachträglich auf eine andere Warengruppe/einen anderen Artikel zeigt, hätte
-- rückwirkend eine andere Bedeutung, ohne dass es jemandem auffällt.
CREATE FUNCTION pricing.protect_markup_rule_scope() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.article_id IS DISTINCT FROM OLD.article_id
       OR NEW.product_group IS DISTINCT FROM OLD.product_group
       OR NEW.supplier_party_id IS DISTINCT FROM OLD.supplier_party_id THEN
        RAISE EXCEPTION
            'markup_rule %: der Geltungsbereich ist unveraenderlich '
            '(Regel deaktivieren und neu anlegen)', OLD.id;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_markup_rule_protect_scope
    BEFORE UPDATE ON pricing.markup_rule
    FOR EACH ROW EXECUTE FUNCTION pricing.protect_markup_rule_scope();

-- ---------------------------------------------------------------------------
-- Rabattstaffel: ab Menge X gilt Aufschlag Y (Regel bleibt die Untergrenze der
-- Mindestmarge unterworfen). Kein Loeschen -> Stufen werden deaktiviert.
-- ---------------------------------------------------------------------------
CREATE TABLE pricing.markup_rule_tier (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    markup_rule_id  uuid NOT NULL REFERENCES pricing.markup_rule (id),
    min_quantity    numeric(15,3) NOT NULL CHECK (min_quantity > 0),
    markup_percent  numeric(9,3) NOT NULL CHECK (markup_percent > -100),
    status          text NOT NULL DEFAULT 'AKTIV'
                    CHECK (status IN ('AKTIV', 'INAKTIV')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Je Regel hoechstens eine AKTIVE Stufe je Mengenschwelle.
CREATE UNIQUE INDEX uq_markup_rule_tier_menge
    ON pricing.markup_rule_tier (markup_rule_id, min_quantity)
    WHERE status = 'AKTIV';
CREATE INDEX idx_markup_rule_tier_rule
    ON pricing.markup_rule_tier (markup_rule_id);

CREATE TRIGGER trg_markup_rule_tier_updated_at
    BEFORE UPDATE ON pricing.markup_rule_tier
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_markup_rule_tier_audit
    AFTER UPDATE ON pricing.markup_rule_tier
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_markup_rule_tier_no_delete
    BEFORE DELETE ON pricing.markup_rule_tier
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_markup_rule_tier_no_truncate
    BEFORE TRUNCATE ON pricing.markup_rule_tier
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON pricing.markup_rule_tier FROM PUBLIC;

-- Die Regelzugehoerigkeit und die Mengenschwelle einer Stufe sind fix; geaendert
-- werden Aufschlag und Status.
CREATE FUNCTION pricing.protect_markup_rule_tier() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.markup_rule_id IS DISTINCT FROM OLD.markup_rule_id
       OR NEW.min_quantity IS DISTINCT FROM OLD.min_quantity THEN
        RAISE EXCEPTION
            'markup_rule_tier %: Regel und Mengenschwelle sind unveraenderlich',
            OLD.id;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_markup_rule_tier_protect
    BEFORE UPDATE ON pricing.markup_rule_tier
    FOR EACH ROW EXECUTE FUNCTION pricing.protect_markup_rule_tier();

-- ---------------------------------------------------------------------------
-- Herkunft eines gespeicherten VK: von Hand gesetzt oder aus der Matrix
-- gerechnet. Die Massenpflege fasst MANUELL gesetzte Preise NIE an.
-- ---------------------------------------------------------------------------
ALTER TABLE pricing.article_sale_price
    ADD COLUMN price_origin text NOT NULL DEFAULT 'MANUELL'
    CHECK (price_origin IN ('MANUELL', 'MATRIX'));
"""

REVERSE_SQL = r"""
ALTER TABLE pricing.article_sale_price DROP COLUMN price_origin;
DROP FUNCTION IF EXISTS pricing.protect_markup_rule_tier() CASCADE;
DROP TABLE IF EXISTS pricing.markup_rule_tier;
DROP FUNCTION IF EXISTS pricing.protect_markup_rule_scope() CASCADE;
DROP TABLE IF EXISTS pricing.markup_rule;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0068_pausenregel_feiertage"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
