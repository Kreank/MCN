"""Rechtematrix um das Modul 'company' erweitern.

`security.role_permission.module` (0026 in db/, per 0021 um 'hr' erweitert)
kennt das neue Fachschema `company` noch nicht. Ohne diesen Eintrag findet die
app-seitige Durchsetzung (api/permissions.py) keine Regel für die
Firmeneinstellungen.

Fachliche Festlegung (docs/roadmap/13): Das Firmenprofil steht auf **jedem
Beleg** — deshalb darf **jede Rolle** es LESEN (inkl. NUR_LESEN, MONTEUR …).
Ändern (ANLEGEN/AENDERN/… und alle übrigen Aktionen) dürfen ausschließlich
ADMINISTRATION und GESCHAEFTSFUEHRUNG. Das gilt gleichermaßen für Niederlassungen
und den Gewerk-Katalog.

Muster: 0021_rechtematrix_hr_modul.py (CHECK erweitern + Matrix-Zeilen).
"""
from django.db import migrations

CREATE_SQL = r"""
ALTER TABLE security.role_permission DROP CONSTRAINT role_permission_module_check;
ALTER TABLE security.role_permission ADD CONSTRAINT role_permission_module_check
    CHECK (module IN ('identity', 'property', 'management', 'tenure', 'billing',
                      'workflow', 'invoicing', 'pricing', 'content', 'security',
                      'ai', 'hr', 'company'));

INSERT INTO security.role_permission (role_code, module, action, allowed, row_scope)
SELECT r.code, 'company', a.action,
       -- LESEN für alle; jede andere Aktion nur für ADMINISTRATION/GESCHAEFTSFUEHRUNG.
       (a.action = 'LESEN')
        OR r.code IN ('ADMINISTRATION', 'GESCHAEFTSFUEHRUNG'),
       'ALLE'
FROM security.role r
CROSS JOIN (VALUES ('LESEN'), ('ANLEGEN'), ('AENDERN'), ('FREIGEBEN'), ('VERSENDEN'),
                   ('STORNIEREN'), ('EXPORTIEREN'), ('LOESCHEN')) AS a(action);
"""

DROP_SQL = r"""
DELETE FROM security.role_permission WHERE module = 'company';
ALTER TABLE security.role_permission DROP CONSTRAINT role_permission_module_check;
ALTER TABLE security.role_permission ADD CONSTRAINT role_permission_module_check
    CHECK (module IN ('identity', 'property', 'management', 'tenure', 'billing',
                      'workflow', 'invoicing', 'pricing', 'content', 'security',
                      'ai', 'hr'));
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0023_company_firmenprofil"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
    ]
