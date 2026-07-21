"""Vorname wird optional (Befund B1 aus docs/DISPONENT_BEFUNDE.md).

Warum
-----
Der Disponent nimmt am Telefon auf, was er hört. „Frau Özdemir aus der
Ahornstraße 7 meldet einen Wasserschaden" ist eine vollständige, brauchbare
Meldung — der Vorname kommt in diesem Gespräch schlicht nicht vor. Bisher
erzwang die Erfassung ihn auf **vier** Ebenen (Frontend-Validator, API-Schema,
Service-Guard, DB-CHECK), und in der Praxis wurde deshalb „X" oder „." getippt.
Ein erfundener Vorname ist schlechter als gar keiner: Er sieht aus wie ein Wert
und ist keiner, er landet in Anrede und Anschreiben, und er macht jede spätere
Dublettensuche unschärfer.

**Der Nachname bleibt Pflicht** (Befund B3): Ohne ihn ist ein Kontakt nicht
identifizierbar, und `party.display_name` — der Anzeigename in jeder Liste,
jeder Suche und jedem Beleg — leitet sich aus ihm ab.

Was sich ändert
---------------
`identity.person.first_name` wird NULL-fähig. Der CHECK bleibt, nur in der
Form, die auch andernorts im Repo gilt (`room.storey`, `unit.storey`):
**NULL ist erlaubt, ein leerer String nicht.** Damit ist „nicht erhoben" (NULL)
sauber von „erhoben und leer" (unmöglich) getrennt, und es kann kein Datensatz
entstehen, der befüllt aussieht und leer ist.

Rückwärts
---------
`SET NOT NULL` schlägt fehl, sobald eine Person ohne Vornamen existiert — das
ist beabsichtigt und richtig: Das Zurückrollen darf keine Fachdaten erfinden.
Wer zurück muss, entscheidet vorher fachlich, was in diesen Zeilen stehen soll.
"""
from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE identity.person
    ALTER COLUMN first_name DROP NOT NULL;

-- Der alte CHECK verlangte btrim(first_name) <> '' und haette bei NULL
-- zwar UNKNOWN (= zugelassen) ergeben, ist aber missverstaendlich: Er las
-- sich, als sei ein Wert Pflicht. Ersetzt durch die im Repo uebliche Form
-- (vgl. room.storey in 0086, unit.storey in 0124).
ALTER TABLE identity.person
    DROP CONSTRAINT IF EXISTS person_first_name_check;

ALTER TABLE identity.person
    ADD CONSTRAINT person_first_name_nicht_leer
    CHECK (first_name IS NULL OR btrim(first_name) <> '');

COMMENT ON COLUMN identity.person.first_name IS
    'Vorname. NULL = nicht erhoben (der Anrufer nennt ihn oft nicht). Ein gesetzter Wert darf nicht leer sein. Der Nachname bleibt Pflicht (B3).';
"""

REVERSE_SQL = r"""
ALTER TABLE identity.person
    DROP CONSTRAINT IF EXISTS person_first_name_nicht_leer;

-- Scheitert absichtlich, wenn eine Person ohne Vornamen existiert: Das
-- Zurueckrollen darf keine Fachdaten erfinden.
ALTER TABLE identity.person
    ALTER COLUMN first_name SET NOT NULL;

ALTER TABLE identity.person
    ADD CONSTRAINT person_first_name_check
    CHECK (btrim(first_name) <> '');
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0124_unit_storey_und_schutzstandard"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
