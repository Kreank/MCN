"""Neue Rolle AZUBI — Monteur-Arbeitsbereich, aber dauerhaft ohne Abschlussrechte.

Fachlicher Anlass (Sascha, 2026-07-31): Azubis sollen im Betrieb eine eigene
Gruppe sein, nicht als Monteur mitlaufen.

Fachliche Festlegung (Begründung):
  * Der Azubi arbeitet im **selben Bereich wie der Monteur**: Einsätze
    dokumentieren (`workflow` ANLEGEN/AENDERN/LESEN), Fotos und Notizen anlegen
    (`content`), Objektdaten am Einsatzort pflegen (`property`), eigene Zeiten
    und Abwesenheiten erfassen (`hr`, row_scope EIGENE).
  * Er hat **keine Abschluss- und Aussenwirkung**: FREIGEBEN, VERSENDEN,
    STORNIEREN und LOESCHEN sind in ALLEN Modulen hart auf false.
  * Kaufmännisches bleibt zu: `accounting`, `billing`, `invoicing` (ausser
    LESEN), `pricing` und `security` sind gesperrt — Einkaufspreise und Margen
    gehen den Azubi so wenig an wie die Benutzerverwaltung.

Warum eine eigene Rolle, obwohl MONTEUR heute dieselben Rechte hat?
  Der MONTEUR hat FREIGEBEN/VERSENDEN/STORNIEREN aktuell ebenfalls nirgends —
  die beiden Matrizen sind im Moment deckungsgleich. Der Unterschied ist
  **zeitlicher Natur**: wertet man den Monteur später auf (z. B. Einsatzbericht
  selbst versenden), bleibt der Azubi ausgeschlossen, ohne dass jemand daran
  denken muss. Ausserdem ist die Gruppe auswertbar und über
  `/einstellungen/rechte` einzeln nachjustierbar, ohne alle Monteure zu treffen.

Rueckwaerts: `security.role` ist append-only (`trg_role_no_delete`), die Rolle
selbst bleibt also stehen. Die Rueckwaertsmigration raeumt nur die
Rechtematrix-Zeilen ab — mehr laesst der Schutzstandard bewusst nicht zu.

Muster: 0032_rechtematrix_accounting_modul.py (Matrix-Zeilen je Rolle).
"""
from django.db import migrations

CREATE_SQL = r"""
INSERT INTO security.role (code, label)
VALUES ('AZUBI', 'Azubi')
ON CONFLICT (code) DO NOTHING;

-- Vollstaendige Matrix: 15 Module x 8 Aktionen = 120 Zeilen.
-- Die Werte stehen bewusst explizit hier und werden NICHT aus MONTEUR kopiert,
-- damit die Migration reproduzierbar ist, auch wenn jemand die Monteur-Rechte
-- zwischenzeitlich ueber die Oberflaeche veraendert hat.
INSERT INTO security.role_permission (role_code, module, action, allowed, row_scope)
SELECT 'AZUBI', m.module, a.action,
       CASE
           -- Harte Sperre: keine Abschluss- oder Aussenwirkung, in keinem Modul.
           WHEN a.action IN ('FREIGEBEN', 'VERSENDEN', 'STORNIEREN', 'LOESCHEN') THEN false

           -- Arbeitsbereich: dokumentieren und pflegen.
           WHEN m.module = 'workflow'  AND a.action IN ('LESEN', 'ANLEGEN', 'AENDERN') THEN true
           WHEN m.module = 'property'  AND a.action IN ('LESEN', 'ANLEGEN', 'AENDERN') THEN true
           WHEN m.module = 'content'   AND a.action IN ('LESEN', 'ANLEGEN')            THEN true
           WHEN m.module = 'hr'        AND a.action IN ('LESEN', 'ANLEGEN', 'AENDERN') THEN true

           -- Einsicht, damit er weiss, wo er hinfaehrt und was gilt.
           WHEN m.module IN ('identity', 'management', 'tenure', 'maintenance', 'invoicing')
                AND a.action = 'LESEN' THEN true
           WHEN m.module = 'company' AND a.action = 'LESEN' THEN true

           -- accounting, ai, billing, pricing, security: zu.
           ELSE false
       END,
       CASE
           -- Firmenstammdaten gelten fuer alle; hr-Auswertungen sind ohnehin gesperrt.
           WHEN m.module = 'company' THEN 'ALLE'
           WHEN m.module = 'accounting' THEN 'ALLE'
           WHEN m.module = 'hr' AND a.action IN ('EXPORTIEREN', 'FREIGEBEN', 'STORNIEREN',
                                                 'VERSENDEN', 'LOESCHEN') THEN 'ALLE'
           WHEN m.module = 'maintenance' AND a.action <> 'LESEN' THEN 'ALLE'
           ELSE 'EIGENE'
       END
FROM (VALUES ('identity'), ('property'), ('management'), ('tenure'), ('billing'),
             ('workflow'), ('invoicing'), ('pricing'), ('content'), ('security'),
             ('ai'), ('hr'), ('company'), ('accounting'), ('maintenance')) AS m(module)
CROSS JOIN (VALUES ('LESEN'), ('ANLEGEN'), ('AENDERN'), ('FREIGEBEN'), ('VERSENDEN'),
                   ('STORNIEREN'), ('EXPORTIEREN'), ('LOESCHEN')) AS a(action)
ON CONFLICT (role_code, module, action) DO NOTHING;
"""

DROP_SQL = r"""
-- security.role ist append-only (trg_role_no_delete): die Rolle selbst bleibt
-- stehen. Zuweisungen in security.user_role bleiben ebenfalls unberuehrt
-- (trg_user_role_no_delete) — sie laufen fachlich ueber valid_until aus.
DELETE FROM security.role_permission WHERE role_code = 'AZUBI';
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0138_notification_taskcomment"),
    ]

    operations = [
        migrations.RunSQL(CREATE_SQL, DROP_SQL),
    ]
