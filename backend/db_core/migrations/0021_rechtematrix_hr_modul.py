"""Rechtematrix um das Modul 'hr' erweitern.

`security.role_permission.module` stammt aus Migration 0026 (db/), das
`hr`-Fachschema aus 0019 (db_core/) ist jünger — die Modul-Codeliste kennt es
noch nicht. Ohne diesen Eintrag könnte die App-seitige Durchsetzung für
Personaldaten keine Regel finden.

Fachliche Festlegung: Personaldaten (Verträge, Abwesenheiten inkl.
Krankheitszeiten, Urlaubskonten) sehen und pflegen ausschließlich
ADMINISTRATION und GESCHAEFTSFUEHRUNG. Alle übrigen Rollen — auch NUR_LESEN,
das sonst überall lesen darf — haben auf `hr` kein einziges Recht. Das ist
strenger als die Startmatrix von 0026 und folgt aus DSGVO Art. 9
(Gesundheitsdaten) und Art. 5 (Datenminimierung).

Die Selbstsicht („ich sehe meine eigenen Abwesenheiten") ist damit bewusst noch
nicht abgebildet; sie käme über row_scope='EIGENE' und braucht die Auflösung
app_user → hr.employee im Rechte-Service. Siehe docs/roadmap/14.

Muster für die CHECK-Erweiterung: 0016 (number_range_prefix_check).
"""
from django.db import migrations

CREATE_SQL = r"""
ALTER TABLE security.role_permission DROP CONSTRAINT role_permission_module_check;
ALTER TABLE security.role_permission ADD CONSTRAINT role_permission_module_check
    CHECK (module IN ('identity', 'property', 'management', 'tenure', 'billing',
                      'workflow', 'invoicing', 'pricing', 'content', 'security',
                      'ai', 'hr'));

INSERT INTO security.role_permission (role_code, module, action, allowed, row_scope)
SELECT r.code, 'hr', a.action,
       r.code IN ('ADMINISTRATION', 'GESCHAEFTSFUEHRUNG'),
       'ALLE'
FROM security.role r
CROSS JOIN (VALUES ('LESEN'), ('ANLEGEN'), ('AENDERN'), ('FREIGEBEN'), ('VERSENDEN'),
                   ('STORNIEREN'), ('EXPORTIEREN'), ('LOESCHEN')) AS a(action);
"""

DROP_SQL = r"""
DELETE FROM security.role_permission WHERE module = 'hr';
ALTER TABLE security.role_permission DROP CONSTRAINT role_permission_module_check;
ALTER TABLE security.role_permission ADD CONSTRAINT role_permission_module_check
    CHECK (module IN ('identity', 'property', 'management', 'tenure', 'billing',
                      'workflow', 'invoicing', 'pricing', 'content', 'security', 'ai'));
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0020_absence_employee_employmentcontract_vacationbudget"),
    ]

    operations = [
        # reverse_sql zulässig, solange keine Fachdaten entstanden sind
        # (Dev-Politik aus db/README.md). role_permission ist Stammdatenpflege,
        # das DELETE trifft nur die hier eingefügten Zeilen.
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
    ]
