"""Rechtematrix um das Modul 'accounting' erweitern (TEIL C).

`security.role_permission.module` (0026 in db/, per 0021 um 'hr', per 0024 um
'company' erweitert) kennt das neue Fachschema `accounting` (0030/0031) noch
nicht. Ohne diesen Eintrag findet die app-seitige Durchsetzung (api/permissions.py)
keine Regel für Buchungskonten, Kostenstellen und Eingangsbelege.

Fachliche Festlegung (Begründung):
  * ADMINISTRATION / GESCHAEFTSFUEHRUNG: voll (wie überall).
  * BUCHHALTUNG: voll außer LOESCHEN — die Belegerfassung, Prüfung, FREIGABE,
    Buchung, Storno-Kennzeichnung und der DATEV-Export (EXPORTIEREN) sind ihr
    Kerngeschäft. LOESCHEN bleibt aus (GoBD/Historienschutz; physisch ohnehin
    per Trigger gesperrt).
  * NUR_LESEN / TECHNISCHE_LEITUNG / DISPOSITION: nur LESEN (Einsicht in die
    Kontierung/Belege, kein Schreibrecht).
  * MONTEUR: kein Zugriff — Eingangsbelege gehören nicht in seinen Arbeitsbereich.
Das Tor FREIGEBEN (Freigabe eines Eingangsbelegs) ist damit ausschließlich
ADMINISTRATION, GESCHAEFTSFUEHRUNG und BUCHHALTUNG vorbehalten.

Muster: 0024_rechtematrix_company_modul.py (CHECK erweitern + Matrix-Zeilen).
"""
from django.db import migrations

CREATE_SQL = r"""
ALTER TABLE security.role_permission DROP CONSTRAINT role_permission_module_check;
ALTER TABLE security.role_permission ADD CONSTRAINT role_permission_module_check
    CHECK (module IN ('identity', 'property', 'management', 'tenure', 'billing',
                      'workflow', 'invoicing', 'pricing', 'content', 'security',
                      'ai', 'hr', 'company', 'accounting'));

INSERT INTO security.role_permission (role_code, module, action, allowed, row_scope)
SELECT r.code, 'accounting', a.action,
       CASE
           WHEN r.code IN ('ADMINISTRATION', 'GESCHAEFTSFUEHRUNG') THEN true
           WHEN a.action = 'LOESCHEN' THEN false            -- GoBD: niemand löscht
           WHEN r.code = 'BUCHHALTUNG' THEN true            -- voll außer LOESCHEN
           WHEN a.action = 'LESEN'
                AND r.code IN ('NUR_LESEN', 'TECHNISCHE_LEITUNG', 'DISPOSITION') THEN true
           ELSE false                                       -- MONTEUR: kein Zugriff
       END,
       'ALLE'
FROM security.role r
CROSS JOIN (VALUES ('LESEN'), ('ANLEGEN'), ('AENDERN'), ('FREIGEBEN'), ('VERSENDEN'),
                   ('STORNIEREN'), ('EXPORTIEREN'), ('LOESCHEN')) AS a(action);
"""

DROP_SQL = r"""
DELETE FROM security.role_permission WHERE module = 'accounting';
ALTER TABLE security.role_permission DROP CONSTRAINT role_permission_module_check;
ALTER TABLE security.role_permission ADD CONSTRAINT role_permission_module_check
    CHECK (module IN ('identity', 'property', 'management', 'tenure', 'billing',
                      'workflow', 'invoicing', 'pricing', 'content', 'security',
                      'ai', 'hr', 'company'));
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0031_accounting_beleg"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
    ]
