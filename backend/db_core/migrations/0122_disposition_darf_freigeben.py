"""Disposition darf Aufträge freigeben (workflow.FREIGEBEN).

Fachlicher Hintergrund (User-Entscheidung 2026-07-20)
-----------------------------------------------------
Die Startmatrix aus 0026 gibt DISPOSITION ``LESEN/ANLEGEN/AENDERN/VERSENDEN`` auf
``workflow``, aber kein ``FREIGEBEN`` — das lag bei TECHNISCHE_LEITUNG und
darüber. Diese Trennung ist sinnvoll, solange die Freigabe eine *fachliche
Prüfung* ist: Jemand sieht sich an, was da beauftragt wurde, und gibt es frei.

Mit dem Anruf-Durchstich (``POST /api/planung/anruf``) stimmt die Annahme nicht
mehr. Dort nimmt die Disposition die Beauftragung am Telefon entgegen — und wer
eine Beauftragung entgegennimmt, erteilt sie fachlich bereits. Die Freigabe
davon getrennt zu halten hätte keine Kontrollwirkung mehr, sondern nur zur Folge,
dass der Monteur am Termintag vor der Tür steht, bis jemand anderes einen
formalen Haken setzt.

Tragweite — bewusst in Kauf genommen
------------------------------------
Das Recht wirkt **nicht nur** auf telefonisch angelegte Aufträge. Ein
Rechte-Eintrag kennt keine Herkunft; DISPOSITION kann nach dieser Migration jeden
Auftrag freigeben, auch einen, der über den mehrstufigen Weg entstanden ist. Das
ist die Konsequenz der Entscheidung und war beim Beschluss bekannt.

Was **nicht** aufgeweicht wird: Die DB-Tore aus ``recheck_work_order_gates``
bleiben unverändert. Beauftragungsnachweis in Textform, bestätigter
Verantwortungsbereich und ein PRINCIPAL sind für FREIGEGEBEN weiterhin Pflicht —
die Disposition darf das Tor jetzt bedienen, sie kommt nicht daran vorbei. Ebenso
unberührt: ``ABGERECHNET`` verlangt zusätzlich INVOICE_DEBTOR, und Rechte auf
``invoicing``/``billing`` bekommt DISPOSITION hier keine.

Umsetzung
---------
``UPDATE`` statt ``INSERT``: 0026 legt die Matrix als vollständiges Kreuzprodukt
aller Rollen × Module × Aktionen an, die Zeile existiert also bereits mit
``allowed = false``. Ein INSERT liefe in die UNIQUE (role_code, module, action).
``row_scope`` bleibt unangetastet (DISPOSITION hat ohnehin 'ALLE').

Rückwärts: setzt ``allowed`` wieder auf false. Verlustfrei — die Zeile bleibt in
beiden Richtungen bestehen, es ändert sich nur ein Flag. Der Audit-Trigger auf
``security.role_permission`` (0026) protokolliert beide Richtungen.
"""
from django.db import migrations

FORWARD_SQL = r"""
UPDATE security.role_permission
   SET allowed = true
 WHERE role_code = 'DISPOSITION'
   AND module    = 'workflow'
   AND action    = 'FREIGEBEN';
"""

REVERSE_SQL = r"""
UPDATE security.role_permission
   SET allowed = false
 WHERE role_code = 'DISPOSITION'
   AND module    = 'workflow'
   AND action    = 'FREIGEBEN';
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0121_employeetrade"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
