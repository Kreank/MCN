"""Der Mitarbeiter darf eigene Abwesenheiten beantragen (Befund E6, Runde 2).

Sascha: „Unter diesem Reiter sollten wir auch die Möglichkeit einbauen:
Urlaubsanträge, Krankheit-Anträge, Überstundenausgleich beantragen usw."

Warum das bisher nicht ging — und zwar dreifach
------------------------------------------------
Es fehlte **nicht** die Oberfläche. Der MONTEUR lief an drei Toren auf:

1. `POST /hr/employees/{id}/absences` verlangt `hr/ANLEGEN`; für ihn stand
   das seit der Grundzuweisung (0021) auf `allowed = false`.
2. Die Aktionen `submit`/`withdraw` laufen über `require(...)` mit `AENDERN` —
   das weist row_scope EIGENE hart mit 403 ab, obwohl er das Recht trägt.
3. `approve`/`reject` verlangen `hr/FREIGEBEN`, das er nicht hat.

Punkt 3 ist richtig so und bleibt: **Genehmigen ist Sache des Betriebs**, nicht
des Antragstellers. Diese Migration räumt nur Punkt 1 weg; Punkt 2 ist eine
Sache der Endpunkte (`require_scoped` + Eigentumsprüfung statt `require`).

Was NICHT dazugehört
--------------------
`hr/ANLEGEN` mit Scope EIGENE erlaubt dem Monteur, einen Personalsatz oder
einen Arbeitsvertrag anzulegen? **Nein** — das verhindern die Endpunkte, nicht
das Recht: `POST /hr/employees` und die Vertragsanlage nutzen weiterhin
`require(...)`, das Scope EIGENE ausnahmslos abweist. Das Recht allein öffnet
nichts; es ist die Voraussetzung, die der Abwesenheits-Endpunkt gezielt nutzt.

Der Genehmigungsweg steht bereits
---------------------------------
Statuskette (`ENTWURF → EINGEREICHT → GENEHMIGT|ABGELEHNT|ZURUECKGEZOGEN`),
Begründungspflicht bei Ablehnung und der Überlappungsschutz sind seit 0019
gebaut und per Trigger durchgesetzt. Es fehlte allein der Zugang für den, der
den Antrag stellt.
"""
from django.db import migrations

FORWARD_SQL = r"""
-- Der MONTEUR trägt hr/LESEN und hr/AENDERN bereits mit Scope EIGENE (0068).
-- ANLEGEN kommt dazu, damit er einen eigenen Abwesenheitsantrag erzeugen kann.
-- Die Objektgrenze zieht der Endpunkt (eigener Personalsatz), nicht dieses
-- Recht — es ist die Voraussetzung, nicht die Erlaubnis.
--
-- Der ROW_COUNT-Riegel ist kein Zierrat: Träfe das UPDATE keine Zeile (weil die
-- Rechtematrix umgebaut wurde), liefe die Migration still durch und ließe eine
-- Funktion zurück, die im Betrieb einfach nicht geht — ein Fehler, der erst
-- beim Anwender auffällt. Lieber hier laut scheitern.
DO $$
DECLARE
    v_rows integer;
BEGIN
    UPDATE security.role_permission
    SET allowed = true, row_scope = 'EIGENE'
    WHERE role_code = 'MONTEUR' AND module = 'hr' AND action = 'ANLEGEN';

    GET DIAGNOSTICS v_rows = ROW_COUNT;
    IF v_rows <> 1 THEN
        RAISE EXCEPTION
            'Erwartet: genau eine Rechtezeile MONTEUR/hr/ANLEGEN, gefunden: %',
            v_rows;
    END IF;
END $$;
"""

REVERSE_SQL = r"""
-- Derselbe Riegel wie vorwärts: Eine Rücknahme, die keine Zeile trifft, meldete
-- sonst Erfolg und ließe das Recht stehen — die Migration gälte als
-- zurückgenommen, ohne es zu sein.
DO $$
DECLARE
    v_rows integer;
BEGIN
    UPDATE security.role_permission
    SET allowed = false, row_scope = 'ALLE'
    WHERE role_code = 'MONTEUR' AND module = 'hr' AND action = 'ANLEGEN';

    GET DIAGNOSTICS v_rows = ROW_COUNT;
    IF v_rows <> 1 THEN
        RAISE EXCEPTION
            'Erwartet: genau eine Rechtezeile MONTEUR/hr/ANLEGEN, gefunden: %',
            v_rows;
    END IF;
END $$;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0129_aufgabe_am_auftrag"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
