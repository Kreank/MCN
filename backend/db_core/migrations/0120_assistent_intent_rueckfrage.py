"""Intent RUECKFRAGE am Gesprächs-Turn (ai.conversation_turn).

Fachlicher Hintergrund (User-Entscheidung 2026-07-20)
-----------------------------------------------------
Der KI-Assistent beantwortete eine offene Frage an ein Objekt („WEG Albrechtstr. 30:
Was ist alles offen?") entweder gar nicht oder müsste dafür die gesamte Objekt-
historie ins Prompt-Fenster laden — beides schlecht. Stattdessen **fragt er jetzt
zurück** und liefert das Menü mit („3 Vorgänge, 2 Aufträge, 1.240 € offen — was
genau?"); die Folgeantwort lädt dann genau eine Kategorie tief.

Diese Rückfrage ist ein eigener Antworttyp, kein AUSKUNFT: Sie beantwortet nichts,
sie stellt eine Gegenfrage. Der `intent` am Turn ist die Provenance dessen, was der
Assistent getan hat — sie als AUSKUNFT zu buchen hieße, die Auswertung („wie oft
musste er nachfragen?") von vornherein unmöglich zu machen.

Der CHECK aus 0117 zählt die erlaubten Intents auf und wird deshalb ausgetauscht.
Reine Constraint-Erweiterung: kein Spaltenwechsel, keine Datenänderung, kein
Rewrite der Tabelle. Bestehende Zeilen erfüllen den neuen CHECK unverändert (die
alte Werteliste ist eine echte Teilmenge der neuen).

Rückwärts
---------
Der Rückweg stellt die alte Werteliste wieder her. Er ist nur so lange gültig, wie
noch **keine** Zeile RUECKFRAGE trägt — sonst schlägt das Hinzufügen des alten
CHECK fehl (gewollt: lieber ein lauter Fehler als stillschweigend umgedeutete
Provenance). Wer wirklich zurück muss, löscht die betroffenen Gespräche.
"""
from django.db import migrations

FORWARD_SQL = """
ALTER TABLE ai.conversation_turn
    DROP CONSTRAINT IF EXISTS conversation_turn_intent_check;

ALTER TABLE ai.conversation_turn
    ADD CONSTRAINT conversation_turn_intent_check
    CHECK (intent IN ('AUSKUNFT', 'KENNZAHL', 'VORSCHLAG', 'RUECKFRAGE'));
"""

REVERSE_SQL = """
ALTER TABLE ai.conversation_turn
    DROP CONSTRAINT IF EXISTS conversation_turn_intent_check;

ALTER TABLE ai.conversation_turn
    ADD CONSTRAINT conversation_turn_intent_check
    CHECK (intent IN ('AUSKUNFT', 'KENNZAHL', 'VORSCHLAG'));
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0119_termin_ort_gebaeude_einheit"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
