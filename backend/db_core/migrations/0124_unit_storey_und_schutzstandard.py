"""Etage an der Einheit + Schutzstandard für property.building und property.unit.

Fachlicher Hintergrund (Disponenten-Test, `docs/DISPONENT_BEFUNDE.md` I12/I2)
----------------------------------------------------------------------------
**I12 — die Einheit hatte kein Etagenfeld.** „Wohnung 12 liegt im 3. OG" war
nicht abbildbar: `storey` hing bisher ausschließlich am *Raum*
(`property.room`, 0086). Das ist fachlich verkehrt herum — die Wohnung liegt
auf der Etage, die Räume liegen in der Wohnung. Wer die Etage wissen wollte,
musste über einen beliebigen Raum der Einheit gehen; eine Einheit ohne erfasste
Räume (der Normalfall) trug die Angabe gar nicht.

`storey` ist **Freitext**, exakt wie `room.storey` (0086:141-144) und aus
demselben Grund: Souterrain, Hochparterre, Zwischengeschoss, Spitzboden,
Galerie, „EG links" — der Bestand ist erfinderischer als jede Codeliste. Die
einzige Regel ist, dass ein gesetzter Wert nicht leer sein darf; NULL heißt
„nicht erfasst" und ist ausdrücklich erlaubt (Bestandsdaten).

Schutzstandard nachgezogen (I2)
-------------------------------
`property.building` und `property.unit` stammen aus 0004 und haben den
Schutzstandard aus `CLAUDE.md` **nie** erhalten. Vorhanden waren nur:

* `trg_building_updated_at` / `trg_unit_updated_at` (0004:70,97) — reine
  Zeitstempel-Mechanik, kein Nachweis;
* `trg_unit_type_conflicts` (0009:34) — eine fachliche Sperre gegen das
  Umtypisieren einer Einheit mit widersprechenden Daten, kein Schutz.

Es fehlten Änderungs-Audit, No-Delete und No-Truncate. Das fiel bisher nicht
auf, weil es **keinen einzigen Schreibpfad außer INSERT** gab: `api/property.py`
kannte nur POST. Genau das ändert das Arbeitspaket AP1 (`PATCH /buildings/{id}`,
`PATCH /units/{id}`) — und die Querschnittsregel des Befunddokuments lautet:
*Audit-Trigger für jede Tabelle, die einen neuen Schreibpfad bekommt.* Ohne
diese Migration entstünde mit dem ersten PATCH eine Nachweislücke: Wer eine
Einheit umbenennt oder umtypisiert, hinterließe keine Spur.

Gemustert nach `property.room` (0086:200-212) — der nächste Verwandte: ebenfalls
in `property`, ebenfalls **änderbar** und trotzdem geschützt. Deshalb wird hier
wie dort **nur TRUNCATE** entzogen, nicht UPDATE: Korrigierbarkeit ist der Zweck
der Übung, das Löschen und das spurlose Leeren sind es nicht.

Warum kein REVOKE DELETE
------------------------
Wie bei `room`: Der No-Delete-*Trigger* sperrt fachlich und mit verständlicher
Meldung; ein zusätzliches `REVOKE DELETE` brächte nur eine Rechte-Fehlermeldung
aus der Tiefe. TRUNCATE dagegen umginge jeden Row-Trigger und muss deshalb auf
Rechteebene weg.

Nebenwirkung auf die Testsuite (bewusst)
----------------------------------------
Die No-Truncate-Trigger sind der Grund, warum Tests mit
`django_db(transaction=True)` beim Aufräumen an Djangos `flush` scheitern
(19 bekannte Teardown-Fehler). Diese Migration **erhöht die Zahl nicht**: ein
`flush` scheitert ohnehin schon an der ersten geschützten Tabelle, und welche
Tests betroffen sind, hängt allein daran, ob sie `transaction=True` benutzen.

Rückwärts
---------
Trigger fallen, die Spalte fällt — Letzteres nur sinnvoll, solange keine Etage
erfasst ist (sonst gingen Fachdaten verloren). Das `REVOKE` wird bewusst
**nicht** zurückgenommen: TRUNCATE liegt bei PUBLIC ohnehin nicht an, ein
`GRANT` beim Zurückrollen vergäbe ein Recht, das vorher niemand hatte.
"""
from django.db import migrations

FORWARD_SQL = r"""
-- ---------------------------------------------------------------------------
-- I12 — Etage an der Einheit. Freitext wie room.storey (0086), keine Codeliste.
-- ---------------------------------------------------------------------------
ALTER TABLE property.unit
    ADD COLUMN storey text NULL
        CONSTRAINT unit_storey_nicht_leer
        CHECK (storey IS NULL OR btrim(storey) <> '');

COMMENT ON COLUMN property.unit.storey IS
    'Geschoss der Einheit als freier Text (EG, 1. OG, Souterrain, Hochparterre …). NULL = nicht erfasst. Codeliste bewusst vermieden, siehe room.storey (0086).';

-- ---------------------------------------------------------------------------
-- I2 — Schutzstandard für building und unit, gemustert nach property.room
-- (0086:200-212). Beide Tabellen bekommen mit AP1 erstmals einen UPDATE-Pfad.
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_building_audit
    AFTER UPDATE ON property.building
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_building_no_delete
    BEFORE DELETE ON property.building
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_building_no_truncate
    BEFORE TRUNCATE ON property.building
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE TRUNCATE ON property.building FROM PUBLIC;

CREATE TRIGGER trg_unit_audit
    AFTER UPDATE ON property.unit
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_unit_no_delete
    BEFORE DELETE ON property.unit
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_unit_no_truncate
    BEFORE TRUNCATE ON property.unit
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE TRUNCATE ON property.unit FROM PUBLIC;
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS trg_unit_no_truncate ON property.unit;
DROP TRIGGER IF EXISTS trg_unit_no_delete ON property.unit;
DROP TRIGGER IF EXISTS trg_unit_audit ON property.unit;

DROP TRIGGER IF EXISTS trg_building_no_truncate ON property.building;
DROP TRIGGER IF EXISTS trg_building_no_delete ON property.building;
DROP TRIGGER IF EXISTS trg_building_audit ON property.building;

ALTER TABLE property.unit DROP COLUMN IF EXISTS storey;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0123_merge_gewerk_und_assistent"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
