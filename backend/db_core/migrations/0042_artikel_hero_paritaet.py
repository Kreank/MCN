"""Artikelstamm-Parität zum Hero-Dialog: Informations- und Kalkulationsfelder.

Hero führt im Artikeldialog einen breiteren Feldsatz, als der lokale Stamm
bisher trug. Diese Migration ergänzt die fehlenden Spalten auf `pricing.article`,
verankert das Artikelbild in `content.file_link` und öffnet
`pricing.article_sale_price` für die je-Gruppe-überschreibbare VK-Tabelle.

Warum Hand-SQL als **Django-RunSQL** und NICHT als db/migrations/0044.sql:
`db_core/migrations/0001_baseline.py` liest zur Laufzeit ALLE `db/migrations/*.sql`
per glob und führt sie aus. Eine neue 0044.sql würde damit auf einer frischen
(Test-)DB doppelt angewandt (einmal durch die Baseline, einmal durch diese
Migration) und bräche den Aufbau. Post-Baseline-DDL lebt deshalb — wie schon die
Nachkommastellen-Migrationen 0038/0039 — ausschließlich hier als RunSQL. Auf der
gefakten Dev-DB wird nur diese Migration real gefahren; auf einer frischen DB
baut die Baseline 0001–0043 und diese Migration setzt sauber darauf auf.

--- price_unit (Hero „Preiseinheit") — die Fehlerquelle ---------------------
`list_price` (am Artikel) und der Einkaufspreis (am Lieferantenbezug) gelten
je `price_unit` Mengeneinheiten. Default 1 heißt: unverändert zu heute — alle
DATANORM-Artikel behalten ihre Bedeutung (der Import rechnet bereits auf je
Stück um und schreibt weiterhin price_unit = 1). Wo aus einem Artikel ein Preis
JE STÜCK abgeleitet wird (VK-Kalkulation), wird durch `price_unit` geteilt; das
erledigt der Kalkulations-Service. Die gespeicherte VK-Überschreibung
(`article_sale_price.fixed_price`) und Belegpositions-Preise sind bereits je
Stück und werden NICHT umgerechnet.

Schutzstandard: reine ADD-COLUMN-Erweiterungen; die Audit-/No-Delete-/
No-Truncate-Trigger der Tabellen (0028 article, 0024 file_link, 0033
article_sale_price) bestehen bereits und decken die neuen Spalten mit ab.
"""
from django.db import migrations

FORWARD_SQL = r"""
-- ---------------------------------------------------------------------------
-- pricing.article — Hero-Feldsatz (Reiter Informationen + Preiseinheit)
-- ---------------------------------------------------------------------------
ALTER TABLE pricing.article
    ADD COLUMN matchcode           text,
    ADD COLUMN manufacturer_type   text,
    ADD COLUMN min_order_quantity  numeric(15,3) CHECK (min_order_quantity IS NULL OR min_order_quantity > 0),
    ADD COLUMN quantity_step       numeric(15,3) CHECK (quantity_step IS NULL OR quantity_step > 0),
    ADD COLUMN delivery_time_days  smallint      CHECK (delivery_time_days IS NULL OR delivery_time_days >= 0),
    -- Steuercode-Vorschlag für neue Belegpositionen; identische Codeliste wie
    -- quote_line/invoice_line (FK auf invoicing.tax_code). Nullable.
    ADD COLUMN tax_code            text REFERENCES invoicing.tax_code (code),
    ADD COLUMN cost_center_id      uuid REFERENCES accounting.cost_center (id) ON DELETE RESTRICT,
    -- Preiseinheit: list_price/EK gelten je price_unit Einheiten. Default 1.
    ADD COLUMN price_unit          smallint NOT NULL DEFAULT 1 CHECK (price_unit IN (1, 10, 100, 1000));

COMMENT ON COLUMN pricing.article.price_unit IS
    'Hero-Preiseinheit: list_price und Einkaufspreis gelten je price_unit '
    'Einheiten (1/10/100/1000). Der je-Stück-Preis ergibt sich durch Division. '
    'DATANORM-Import rechnet bereits auf je Stück um und schreibt price_unit = 1.';

-- Kurzsuchbegriff: Suche geht über icontains; ein einfacher Index stützt
-- Präfix-/Gleichheitszugriffe. Nur wo gesetzt (der Bestand ist gross).
CREATE INDEX idx_article_matchcode ON pricing.article (matchcode)
    WHERE matchcode IS NOT NULL;
CREATE INDEX idx_article_cost_center ON pricing.article (cost_center_id)
    WHERE cost_center_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- content.file_link — Artikelbild als weiteres (genau-ein) Ziel
-- ---------------------------------------------------------------------------
ALTER TABLE content.file_link
    ADD COLUMN article_id uuid REFERENCES pricing.article (id);

-- „Genau ein Ziel" konsistent um article_id erweitern (bestehende zehn Ziele
-- unverändert übernehmen — sonst liesse sich keine Datei mehr anlegen).
ALTER TABLE content.file_link DROP CONSTRAINT file_link_check;
ALTER TABLE content.file_link ADD CONSTRAINT file_link_check CHECK (
    num_nonnulls(service_case_id, work_order_id, service_job_id, property_id,
                 unit_id, asset_id, quote_id, invoice_id, party_id,
                 communication_id, project_id, article_id) = 1);

CREATE INDEX idx_file_link_article ON content.file_link (article_id)
    WHERE article_id IS NOT NULL;
-- Höchstens EIN Artikelbild je Artikel.
CREATE UNIQUE INDEX uq_file_link_artikelbild
    ON content.file_link (article_id)
    WHERE article_id IS NOT NULL AND link_category = 'ARTIKELBILD';

-- ---------------------------------------------------------------------------
-- pricing.article_sale_price — je-Gruppe überschreibbare VK-Tabelle
-- Bisher galt XOR (Gruppe ODER Festpreis). Der Hero-Dialog überschreibt den
-- errechneten VK EINER Gruppe mit einem Festpreis — dazu muss eine Zeile Gruppe
-- UND fixed_price tragen dürfen. Neue Regel: mindestens eines ist gesetzt.
--   * nur Gruppe            -> Formel-VK
--   * Gruppe + fixed_price  -> manuelle Überschreibung dieser Gruppe (neu)
--   * nur fixed_price       -> freistehender Festpreis (z. B. „aus Beleg")
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    v_conname text;
BEGIN
    SELECT conname INTO v_conname
    FROM pg_constraint
    WHERE conrelid = 'pricing.article_sale_price'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) ILIKE '%<>%';
    IF v_conname IS NOT NULL THEN
        EXECUTE format('ALTER TABLE pricing.article_sale_price DROP CONSTRAINT %I', v_conname);
    END IF;
END $$;

ALTER TABLE pricing.article_sale_price
    ADD CONSTRAINT article_sale_price_group_or_fixed
    CHECK (num_nonnulls(sale_price_group_id, fixed_price) >= 1);

-- Höchstens eine Zeile je (Artikel, VK-Gruppe) — macht die „ganze Tabelle
-- speichern"-Übernahme idempotent (Upsert je Gruppe).
CREATE UNIQUE INDEX uq_article_sale_price_group
    ON pricing.article_sale_price (article_id, sale_price_group_id)
    WHERE sale_price_group_id IS NOT NULL;
"""

REVERSE_SQL = r"""
DROP INDEX IF EXISTS pricing.uq_article_sale_price_group;
ALTER TABLE pricing.article_sale_price DROP CONSTRAINT IF EXISTS article_sale_price_group_or_fixed;
ALTER TABLE pricing.article_sale_price
    ADD CONSTRAINT article_sale_price_check
    CHECK ((sale_price_group_id IS NULL) <> (fixed_price IS NULL));

DROP INDEX IF EXISTS content.uq_file_link_artikelbild;
DROP INDEX IF EXISTS content.idx_file_link_article;
ALTER TABLE content.file_link DROP CONSTRAINT file_link_check;
ALTER TABLE content.file_link ADD CONSTRAINT file_link_check CHECK (
    num_nonnulls(service_case_id, work_order_id, service_job_id, property_id,
                 unit_id, asset_id, quote_id, invoice_id, party_id,
                 communication_id, project_id) = 1);
ALTER TABLE content.file_link DROP COLUMN article_id;

DROP INDEX IF EXISTS pricing.idx_article_cost_center;
DROP INDEX IF EXISTS pricing.idx_article_matchcode;
ALTER TABLE pricing.article
    DROP COLUMN price_unit,
    DROP COLUMN cost_center_id,
    DROP COLUMN tax_code,
    DROP COLUMN delivery_time_days,
    DROP COLUMN quantity_step,
    DROP COLUMN min_order_quantity,
    DROP COLUMN manufacturer_type,
    DROP COLUMN matchcode;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0041_supplierconnection"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
