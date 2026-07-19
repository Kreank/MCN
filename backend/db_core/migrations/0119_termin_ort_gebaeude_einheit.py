"""Gebäude und Einheit am Einsatz/Termin (workflow.service_job).

Fachlicher Hintergrund (User-Entscheidung 2026-07-19)
-----------------------------------------------------
Eine Liegenschaft ist oft ein **Zusammenschluss mehrerer Adressen**: die WEG
„Albrechtstraße 22" kann die Gebäude *Albrechtstraße 22* und *Steglitzer Damm 12*
umfassen, jedes mit eigener Anschrift und eigenen Wohnungen. Ruft Mieter Müller
an („Klo kaputt, Albrechtstraße 22, 3. OG rechts"), muss der Termin genau dieses
Gebäude und diese Einheit treffen — sonst weiß der Monteur nicht, *wohin*.

`workflow.service_case` (Vorgang) und `workflow.work_order` (Auftrag) tragen
`building_id`/`unit_id` bereits (0012/0013). Der Einsatz (`service_job`) kannte
bisher nur `property_id` (0062, freier Termin) und löste die Adresse deshalb
ausschließlich auf Liegenschaftsebene auf. Diese Migration schließt die Lücke:
der **freie Termin** (Begehung ohne Auftrag) kann jetzt selbst ein Gebäude/eine
Einheit benennen; der auftragsgebundene Einsatz erbt den Ort weiterhin vom
Auftrag (Anzeige-Auflösung in api/planung.py: Einsatz → Auftrag → Liegenschaft).

Standortkonsistenz — dasselbe deklarative Muster wie 0012/0013
-------------------------------------------------------------
Zusammengesetzte Fremdschlüssel erzwingen die Hierarchie ohne einen einzigen
Trigger:

* ``(building_id, property_id) → property.building (id, property_id)`` — das
  Gebäude muss zu der am Einsatz genannten Liegenschaft gehören.
* ``(unit_id, building_id) → property.unit (id, building_id)`` — die Einheit muss
  im genannten Gebäude liegen.
* ``CHECK (unit_id IS NULL OR building_id IS NOT NULL)`` — eine Einheit ohne
  Gebäude ist sinnlos (wie bei work_order/service_case).

**Der entscheidende Unterschied zu 0012/0013:** dort ist ``property_id`` NOT
NULL, hier (freier Termin) ist es NULL-fähig. PostgreSQL prüft einen
mehrspaltigen FK per MATCH SIMPLE nur, wenn **alle** Spalten belegt sind — bei
``property_id IS NULL`` wäre der (building_id, property_id)-FK also stumm, und ein
Gebäude einer *fremden* Liegenschaft ließe sich anhängen. Deshalb hier zusätzlich

* ``CHECK (building_id IS NULL OR property_id IS NOT NULL)``

— ein Gebäude/eine Einheit setzt immer eine Liegenschaft am Einsatz voraus, womit
der zusammengesetzte FK garantiert scharf ist. (work_order/service_case brauchen
diesen CHECK nicht, weil property_id dort ohnehin NOT NULL ist.)

Zusammenspiel mit dem Auftrags-FK aus 0062
-------------------------------------------
Für den auftragsgebundenen Einsatz erzwingt ``service_job_property_matches_order``
(0062) bereits: ist ``property_id`` gesetzt, muss es die Liegenschaft des Auftrags
sein. Zusammen mit dem neuen building_needs_property-CHECK heißt das: wer am
gebundenen Einsatz ein Gebäude setzen will, setzt property_id = Auftrags-
Liegenschaft (FK erzwingt Gleichheit), und das Gebäude muss zu genau dieser
gehören. Alle drei Constraints greifen widerspruchsfrei ineinander.

Keine Trigger-Anpassung nötig
-----------------------------
Gebäude/Einheit berühren keinen Statusautomaten und kein fachliches Tor (anders
als 0062, wo der Auftragsbezug an den Ausführungs-Toren hing). Änderungs-Audit,
No-Delete und No-Truncate hängen an der Tabelle (0009/0015) und erfassen die neuen
Spalten automatisch mit. Gebäude/Einheit bleiben **änderbar** — sie sind eine
Ortsangabe, kein Tor; ein vertippter Ort muss korrigierbar sein.

Rückwärts: nur solange kein Einsatz ein Gebäude/eine Einheit trägt (sonst verlöre
``DROP COLUMN`` Fachdaten).
"""
from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE workflow.service_job
    ADD COLUMN building_id uuid NULL,
    ADD COLUMN unit_id     uuid NULL;

-- Standortkonsistenz deklarativ (Muster aus 0012/0013).
ALTER TABLE workflow.service_job
    ADD CONSTRAINT service_job_building_belongs_to_property
        FOREIGN KEY (building_id, property_id)
        REFERENCES property.building (id, property_id),
    ADD CONSTRAINT service_job_unit_belongs_to_building
        FOREIGN KEY (unit_id, building_id)
        REFERENCES property.unit (id, building_id),
    ADD CONSTRAINT service_job_unit_needs_building
        CHECK (unit_id IS NULL OR building_id IS NOT NULL),
    -- property_id ist am Einsatz NULL-fähig (freier Termin). Ohne diesen CHECK
    -- wäre der (building_id, property_id)-FK per MATCH SIMPLE stumm, sobald
    -- property_id NULL ist — ein Gebäude einer fremden Liegenschaft ließe sich
    -- anhängen. Der CHECK erzwingt property_id und macht den FK garantiert scharf.
    ADD CONSTRAINT service_job_building_needs_property
        CHECK (building_id IS NULL OR property_id IS NOT NULL);

CREATE INDEX idx_service_job_building ON workflow.service_job (building_id)
    WHERE building_id IS NOT NULL;
CREATE INDEX idx_service_job_unit ON workflow.service_job (unit_id)
    WHERE unit_id IS NOT NULL;

COMMENT ON COLUMN workflow.service_job.building_id IS
    'Gebäude des Einsatzes (freier Termin) bzw. präzisierter Ort. Muss zur Liegenschaft (property_id) gehören; setzt property_id voraus.';
COMMENT ON COLUMN workflow.service_job.unit_id IS
    'Einheit/Wohnung des Einsatzes. Muss im Gebäude (building_id) liegen; setzt building_id voraus.';
"""

REVERSE_SQL = r"""
DROP INDEX IF EXISTS workflow.idx_service_job_unit;
DROP INDEX IF EXISTS workflow.idx_service_job_building;

ALTER TABLE workflow.service_job
    DROP CONSTRAINT IF EXISTS service_job_building_needs_property,
    DROP CONSTRAINT IF EXISTS service_job_unit_needs_building,
    DROP CONSTRAINT IF EXISTS service_job_unit_belongs_to_building,
    DROP CONSTRAINT IF EXISTS service_job_building_belongs_to_property;

ALTER TABLE workflow.service_job
    DROP COLUMN IF EXISTS unit_id,
    DROP COLUMN IF EXISTS building_id;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0118_conversation_conversationturn"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
