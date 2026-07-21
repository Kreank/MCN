"""Aufgaben an Aufträge binden (Befund D2 aus Runde 2).

Sascha: „Aufgaben: Grundsätzlich schon gut. Bitte noch die Möglichkeit
einfügen, Aufgaben an Aufträge zu binden."

`workflow.task` kannte bisher genau zwei Bezüge — Projekt und Kontakt (0005).
Der **Auftrag** fehlte, obwohl er das zentrale Arbeitsobjekt ist: „Beim Kunden
anrufen wegen Ersatzteil" gehört an den Auftrag, nicht an das Projekt darüber
(das es oft gar nicht gibt) und nicht an den Kontakt (der die Aufgabe nicht
erklärt).

Warum KEIN Exklusivitäts-CHECK
------------------------------
Das Repo kennt zwei Muster für optionale Bezüge, und sie meinen Verschiedenes:

* `content.file_link` — `CHECK (num_nonnulls(...) = 1)`: **genau ein** Ziel.
  Eine Datei hängt an einem Objekt, sonst wüsste niemand, wohin sie gehört.
* `workflow.task` — **kein** CHECK: „Projekt **und/oder** Kontakt" (0005:7-8).
  Eine Aufgabe darf gleichzeitig am Projekt und am Kunden hängen.

Der Auftrag folgt dem zweiten Muster. Ein CHECK hier wäre nicht nur
inkonsequent, er wäre falsch: Eine Aufgabe am Auftrag hängt fast immer auch am
Kunden, den man deswegen anruft.

Deshalb entfällt hier auch das Nachziehen eines bestehenden Constraints, das
`content.file_link` bei jeder neuen Zielspalte braucht (vgl.
`0023_kommunikation.sql:69-73`, `0035_projekt_cockpit.sql:108-115`).

Der Index ist partiell wie seine Geschwister (0005:38-43) — die allermeisten
Aufgaben tragen keinen Auftrag, und ein voller Index über lauter NULL wäre
Ballast.
"""
from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE workflow.task
    ADD COLUMN work_order_id uuid NULL REFERENCES workflow.work_order (id);

CREATE INDEX idx_task_work_order ON workflow.task (work_order_id)
    WHERE work_order_id IS NOT NULL;

COMMENT ON COLUMN workflow.task.work_order_id IS
    'Optionaler Auftragsbezug (D2). Kombinierbar mit project_id und party_id — bewusst OHNE num_nonnulls-CHECK, siehe Migrationskopf.';
"""

REVERSE_SQL = r"""
DROP INDEX IF EXISTS workflow.idx_task_work_order;
ALTER TABLE workflow.task DROP COLUMN IF EXISTS work_order_id;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0128_filecategory"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
