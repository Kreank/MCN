"""Berichtspositionen am Baustellenbericht (workflow.site_report_line).

Der Baustellenbericht führte bisher nur Freitext (`activity_text`,
`materials_note`). Damit ist zwar dokumentiert, DASS gearbeitet wurde — aber nicht
strukturiert, WAS verbraucht wurde. Für den **Soll-Ist-Abgleich** gegen das
Angebot (der Kern dieses Slices) braucht der Bericht Positionen aus dem
Artikel-/Leistungsstamm: Artikel/Leistung, Menge, Einheit.

## INVARIANTE: Der Bericht führt KEINE PREISE.

`site_report_line` spiegelt bewusst die Struktur von `invoicing.quote_line`
(Migration 0018) — **ohne jede Geldspalte**: kein `unit_price`, kein
`net_amount`, kein `discount_percent`, kein `tax_code`. Auch nicht „für später".

Begründung: Der Bericht wird **vom Kunden vor Ort unterschrieben** und danach
versiegelt (`protect_site_report`, 0054; Anhänge 0065). Ein unterschriebener
Bericht mit Preisen wäre eine **Preisvereinbarung** — der Monteur schlösse mit
seiner Unterschriftenmappe Verträge, die die Kalkulation nie gesehen hat. Der
Preis entsteht ausschließlich in der Rechnung, aus dem Artikelstamm bzw. über
`pricing`/`aufschlagsmatrix.vk_vorschlag`. Der Bericht liefert die **Menge**, das
Belegwesen den **Preis** — dieselbe Grenze wie beim Aufmaß-Rechner (Welle 3).

Aus demselben Grund summiert der Bericht nichts: es gibt **kein ZWISCHENSUMME**
in der Positionsarten-Codeliste (im Gegensatz zu `quote_line`). Was soll er auch
summieren — Mengen verschiedener Einheiten?

Der Bericht bleibt damit ein `workflow`-Nachweis und wird **kein**
`invoicing`-Beleg (keine Belegnummer, kein Snapshot, keine GoBD-Festschreibung).

## planned_quantity: das eingefrorene Soll

`planned_quantity` ist die **Sollmenge aus dem Angebot**, eingefroren beim
Vorbelegen (`source_quote_line_id` nennt die Herkunft). NULL bedeutet: war nicht
angeboten — also eine **Zusatzleistung**. Wie bei der Belegposition gilt: die
Position ist eine **Kopie, kein Verweis** (`description`/`unit` werden aus dem
Stamm kopiert). Ein später geänderter Artikeltext verfälscht keinen bereits vom
Kunden unterschriebenen Nachweis.

`planned_quantity` ist **ohne Herkunft verboten** (CHECK
`site_report_line_soll_nur_mit_herkunft`): ein Soll ohne Angebotsposition wäre eine
frei behauptete Zahl auf einem unterschriebenen Kundendokument. Der Service leitet
den Wert deshalb *immer* aus `source_quote_line_id` ab und verwirft einen
mitgeschickten Client-Wert — die DB setzt dieselbe Regel physisch durch.

Die eingefrorene `planned_quantity` ist eine **Anzeigehilfe für den Monteur**
(„angeboten waren 12 m"). Der Soll-Ist-Abgleich rechnet sie NICHT nach — er zieht
das Soll direkt aus den Angebotspositionen, sonst fehlte der Fall „angeboten, aber
nie eingebaut" (ENTFALLEN) vollständig, denn dafür gibt es gar keine
Berichtsposition, die ein `planned_quantity` tragen könnte.

## Schutzstandard — mit einer dokumentierten Ausnahme

Standard: `set_updated_at` / `audit_row_update` / No-Truncate + REVOKE TRUNCATE.

**KEIN No-Delete-Trigger.** Das ist die bewusste Ausnahme — dieselbe wie bei
`invoicing.quote_line` (0018/0020): Eine Position ist kein historisierter
Datensatz, sondern der **Inhalt** eines Belegs, der als Ganzes geschützt ist. Der
Editor ersetzt beim Speichern immer den kompletten Positionssatz (Delete+Insert),
weil ein Teil-Update bei umsortierten Positionsnummern nicht eindeutig ist. Ein
DELETE-Verbot machte das Streichen einer irrtümlich erfassten Zeile unmöglich.

Der Schutz sitzt stattdessen **eine Ebene höher**, dort wo er hingehört:

    Bericht UNTERZEICHNET  ⇒  kein INSERT, kein UPDATE, kein DELETE
                              einer site_report_line dieses Berichts.

`workflow.protect_site_report_lines` (Vorbild: `content.protect_signed_site_report_links`
aus 0065) prüft **NEW und OLD getrennt** — ein UPDATE, das eine Position von einem
unterzeichneten Bericht **weg**bewegte, wäre sonst so wirksam wie ein DELETE. Das
`FOR SHARE` auf die Berichtszeile serialisiert gegen ein gleichzeitig laufendes
`sign_report` (das die Zeile per UPDATE sperrt): entweder die Position ist vor der
Unterschrift geschrieben, oder sie wird abgewiesen — nie „dazwischen".

Positionen sind damit **nur im ENTWURF** änderbar. TRUNCATE bleibt verboten
(es umginge jeden Row-Trigger); DELETE bleibt erlaubt.
"""
from django.db import migrations

FORWARD_SQL = r"""
CREATE TABLE workflow.site_report_line (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    site_report_id       uuid NOT NULL REFERENCES workflow.site_report (id),
    position_number      integer NOT NULL CHECK (position_number > 0),
    -- Codeliste wie quote_line — ABER OHNE 'ZWISCHENSUMME': der Bericht summiert
    -- nichts (er führt keine Beträge, und Mengen verschiedener Einheiten sind
    -- nicht summierbar).
    line_type            text NOT NULL CHECK (line_type IN
                         ('MATERIAL', 'ARBEITSZEIT', 'PAUSCHALE', 'FREMDLEISTUNG',
                          'FAHRT', 'ZUSCHLAG', 'TEXT')),
    -- Eingefrorene Kopie der Bezeichnung aus dem Stamm (kein Verweis!).
    description          text NOT NULL CHECK (btrim(description) <> ''),
    quantity             numeric(15, 3) NULL
                         CHECK (quantity IS NULL OR quantity >= 0),
    unit                 text NULL,
    -- Herkunft aus dem Stamm (nie beides).
    source_article_id    uuid NULL REFERENCES pricing.article (id),
    source_assembly_id   uuid NULL REFERENCES pricing.assembly (id),
    -- Das eingefrorene SOLL aus dem Angebot. NULL = war nicht angeboten
    -- (= Zusatzleistung).
    planned_quantity     numeric(15, 3) NULL
                         CHECK (planned_quantity IS NULL OR planned_quantity >= 0),
    source_quote_line_id uuid NULL REFERENCES invoicing.quote_line (id),
    note                 text NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (site_report_id, position_number),
    -- Textzeilen tragen keine Menge, Mengenzeilen tragen eine Einheit
    -- (analog zum TEXT/ZWISCHENSUMME-CHECK in invoicing.quote_line).
    CONSTRAINT site_report_line_text_ohne_menge CHECK (
        (line_type = 'TEXT') = (quantity IS NULL AND unit IS NULL)
    ),
    -- Dreiwertige Logik: `btrim(NULL) <> ''` ist NULL — und ein CHECK, der NULL
    -- ergibt, HÄLT. Die NOT-NULL-Bedingung muss deshalb ausdrücklich dastehen,
    -- sonst ließe der CHECK eine Mengenzeile ohne Einheit durch (der Service
    -- verlangt sie beim Speichern → der Bericht wäre nicht mehr speicherbar).
    CONSTRAINT site_report_line_einheit CHECK (
        line_type = 'TEXT' OR (unit IS NOT NULL AND btrim(unit) <> '')
    ),
    CONSTRAINT site_report_line_eine_quelle CHECK (
        num_nonnulls(source_article_id, source_assembly_id) <= 1
    ),
    -- Ein SOLL gibt es nur MIT Herkunft. Ohne Angebotsposition ist jede Sollmenge
    -- frei erfunden — und stünde am Ende auf einem unterschriebenen, versiegelten
    -- Kundendokument. Der Service leitet `planned_quantity` deshalb immer aus der
    -- Herkunft ab; die DB macht das Gegenteil physisch unmöglich.
    CONSTRAINT site_report_line_soll_nur_mit_herkunft CHECK (
        planned_quantity IS NULL OR source_quote_line_id IS NOT NULL
    )
);

CREATE INDEX idx_site_report_line_report ON workflow.site_report_line (site_report_id);
CREATE INDEX idx_site_report_line_article ON workflow.site_report_line (source_article_id);
CREATE INDEX idx_site_report_line_assembly ON workflow.site_report_line (source_assembly_id);
CREATE INDEX idx_site_report_line_quote_line
    ON workflow.site_report_line (source_quote_line_id);

CREATE TRIGGER trg_site_report_line_updated_at
    BEFORE UPDATE ON workflow.site_report_line
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_site_report_line_audit
    AFTER UPDATE ON workflow.site_report_line
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
-- Kein No-Delete-Trigger (dokumentierte Ausnahme, siehe Modulkopf) — TRUNCATE
-- bleibt aber verboten, es umginge jeden Row-Trigger.
CREATE TRIGGER trg_site_report_line_no_truncate
    BEFORE TRUNCATE ON workflow.site_report_line
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE TRUNCATE ON workflow.site_report_line FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- Der unterzeichnete Bericht versiegelt auch seine Positionen.
-- Vorbild: content.protect_signed_site_report_links (0065).
-- ---------------------------------------------------------------------------
CREATE FUNCTION workflow.protect_site_report_lines() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_status text;
BEGIN
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        SELECT status INTO v_status
        FROM workflow.site_report WHERE id = NEW.site_report_id FOR SHARE;
        IF v_status = 'UNTERZEICHNET' THEN
            RAISE EXCEPTION
                'site_report %: Der Bericht ist unterzeichnet — seine Positionen können nicht mehr angelegt oder geändert werden.',
                NEW.site_report_id;
        END IF;
    END IF;

    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        SELECT status INTO v_status
        FROM workflow.site_report WHERE id = OLD.site_report_id FOR SHARE;
        IF v_status = 'UNTERZEICHNET' THEN
            RAISE EXCEPTION
                'site_report %: Der Bericht ist unterzeichnet — seine Positionen können nicht mehr entfernt werden.',
                OLD.site_report_id;
        END IF;
    END IF;

    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;

CREATE TRIGGER trg_site_report_line_protect
    BEFORE INSERT OR UPDATE OR DELETE ON workflow.site_report_line
    FOR EACH ROW EXECUTE FUNCTION workflow.protect_site_report_lines();
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS trg_site_report_line_protect ON workflow.site_report_line;
DROP FUNCTION IF EXISTS workflow.protect_site_report_lines();
DROP TABLE IF EXISTS workflow.site_report_line;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0079_appointmentcategoryqualification_assignmenttemplate_and_more"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
