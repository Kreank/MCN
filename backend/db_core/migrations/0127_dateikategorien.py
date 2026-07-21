"""Pflegbare Dateikategorien (Befund A4/A5 aus Runde 2).

Sascha: „Dateien: Kategorisieren — Bilder, Videos, Baustellenberichte. Gerne
auch die Möglichkeit einbauen, eigene Kategorien einfügen, bearbeiten und
löschen/deaktivieren zu können."

Bisher war `content.file_link.link_category` **Freitext in der DB** mit einer
hartkodierten Liste im Service (`services/dateien.py`). Wer eine eigene
Kategorie wollte, musste den Code ändern — und nichts hinderte einen anderen
Schreibpfad daran, „foto_vorher" oder „Fotos" zu setzen.

Diese Migration macht daraus eine gepflegte Codeliste mit Fremdschlüssel.

Systemkategorien sind unantastbar
---------------------------------
Vier Codes werden **ausschließlich vom Programm** vergeben und stehen in
partiellen UNIQUE-Indizes, die auf den Literalwert prüfen:

* ``BELEG_PDF``   — `0032_beleg_pdf_einmalig.sql:10,14`
* ``ARTIKELBILD`` — `0042_artikel_hero_paritaet.py:81`
* ``E_RECHNUNG``  — `0059_erechnung_ausfertigung.py:24`
* ``ATTEST``      — vergibt nur `api/dateien.py`, nur am Ziel `absence_id`

Würde einer davon umbenannt oder deaktiviert, liefe der zugehörige Index ins
Leere und die Einmaligkeit wäre still weg. Deshalb tragen sie `is_system` und
ein Trigger verbietet Umbenennen und Deaktivieren. Sie erscheinen auch nicht
in der Auswahl beim Hochladen — sie entstehen nur als Nebenwirkung anderer
Vorgänge.

Warum ein FK auf `code` und nicht auf eine id
---------------------------------------------
`link_category` trägt heute in jeder Zeile den Code als Text, und die drei
Indizes oben vergleichen gegen diesen Text. Ein Umbau auf `category_id` würde
alle drei Indizes und jede Abfrage mitreißen, ohne dass irgendjemand etwas
davon hätte. Der FK auf eine eindeutige `code`-Spalte gibt die Integrität,
ohne den Bestand anzufassen — der Code IST der Schlüssel.

Löschen gibt es nicht
---------------------
Wie überall im Repo: `status = 'INAKTIV'` statt DELETE. Eine gelöschte
Kategorie würde die Historie unlesbar machen — alte Dateien tragen sie noch.
Eine inaktive Kategorie verschwindet aus der Auswahl, bleibt aber lesbar.
"""
from django.db import migrations

FORWARD_SQL = r"""
CREATE TABLE content.file_category (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Der Code IST der Schluessel: file_link.link_category zeigt darauf, und
    -- drei partielle UNIQUE-Indizes vergleichen gegen den Literalwert.
    code        text NOT NULL UNIQUE CHECK (btrim(code) <> ''),
    label       text NOT NULL CHECK (btrim(label) <> ''),
    -- Vom Programm vergeben; nicht umbenennbar, nicht deaktivierbar, nicht
    -- in der Auswahl beim Hochladen (siehe Modulkopf).
    is_system   boolean NOT NULL DEFAULT false,
    status      text NOT NULL DEFAULT 'AKTIV'
                CHECK (status IN ('AKTIV', 'INAKTIV')),
    sort_order  integer NOT NULL DEFAULT 100,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE content.file_category IS
    'Gepflegte Codeliste der Dateikategorien. is_system = vom Programm vergeben und unantastbar.';

-- Bestand uebernehmen. Reihenfolge bestimmt die Anzeige.
INSERT INTO content.file_category (code, label, is_system, sort_order) VALUES
    ('DOKUMENT',       'Dokument',            false, 10),
    ('FOTO_VORHER',    'Foto (vorher)',       false, 20),
    ('FOTO_NACHHER',   'Foto (nachher)',      false, 30),
    ('VIDEO_BEGEHUNG', 'Video (Begehung)',    false, 40),
    ('SCAN',           'Scan',                false, 50),
    ('PLAN',           'Plan',                false, 60),
    ('VERTRAG',        'Vertrag',             false, 70),
    ('SONSTIGES',      'Sonstiges',           false, 900),
    ('ARTIKELBILD',    'Artikelbild',         true,  910),
    ('ATTEST',         'Attest',              true,  920),
    ('BELEG_PDF',      'Beleg-PDF',           true,  930),
    ('E_RECHNUNG',     'E-Rechnung',          true,  940);

-- Alles, was im Bestand steht, aber nicht in der Liste, als inaktive Kategorie
-- nachziehen — sonst schluege der FK gleich beim Anlegen fehl. In einer
-- frischen Datenbank passiert hier nichts.
INSERT INTO content.file_category (code, label, is_system, status, sort_order)
SELECT DISTINCT fl.link_category, fl.link_category, false, 'INAKTIV', 990
FROM content.file_link fl
WHERE fl.link_category IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM content.file_category c WHERE c.code = fl.link_category
  );

ALTER TABLE content.file_link
    ADD CONSTRAINT file_link_category_fk
    FOREIGN KEY (link_category) REFERENCES content.file_category (code);

-- ---------------------------------------------------------------------------
-- Systemkategorien schuetzen: Code unveraenderlich, kein Deaktivieren.
-- ---------------------------------------------------------------------------
CREATE FUNCTION content.protect_file_category() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.is_system THEN
        IF NEW.code <> OLD.code THEN
            RAISE EXCEPTION
                'Kategorie %: Der Code einer Systemkategorie ist unveraenderlich — drei partielle UNIQUE-Indizes vergleichen gegen ihn (0032/0042/0059).',
                OLD.code;
        END IF;
        IF NEW.status <> 'AKTIV' THEN
            RAISE EXCEPTION
                'Kategorie %: Eine Systemkategorie laesst sich nicht deaktivieren — sie wird vom Programm vergeben.',
                OLD.code;
        END IF;
        IF NOT NEW.is_system THEN
            RAISE EXCEPTION
                'Kategorie %: is_system laesst sich nicht zuruecknehmen.',
                OLD.code;
        END IF;
    ELSIF NEW.is_system THEN
        RAISE EXCEPTION
            'Kategorie %: is_system wird nicht nachtraeglich gesetzt — Systemkategorien entstehen mit ihrer Migration.',
            OLD.code;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_file_category_protect
    BEFORE UPDATE ON content.file_category
    FOR EACH ROW EXECUTE FUNCTION content.protect_file_category();

-- Schutzstandard (CLAUDE.md).
CREATE TRIGGER trg_file_category_updated_at
    BEFORE UPDATE ON content.file_category
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_file_category_audit
    AFTER UPDATE ON content.file_category
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_file_category_no_delete
    BEFORE DELETE ON content.file_category
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_file_category_no_truncate
    BEFORE TRUNCATE ON content.file_category
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE TRUNCATE ON content.file_category FROM PUBLIC;
"""

REVERSE_SQL = r"""
ALTER TABLE content.file_link DROP CONSTRAINT IF EXISTS file_link_category_fk;
DROP TRIGGER IF EXISTS trg_file_category_no_truncate ON content.file_category;
DROP TRIGGER IF EXISTS trg_file_category_no_delete ON content.file_category;
DROP TRIGGER IF EXISTS trg_file_category_audit ON content.file_category;
DROP TRIGGER IF EXISTS trg_file_category_updated_at ON content.file_category;
DROP TRIGGER IF EXISTS trg_file_category_protect ON content.file_category;
DROP FUNCTION IF EXISTS content.protect_file_category();
DROP TABLE IF EXISTS content.file_category;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0126_schutzstandard_identity_property"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
