"""DATEV-Export-Konfiguration am Firmenprofil (company.company_profile).

Hand-SQL nach db/README.md: Fachschema-Änderung als Django-Migration mit RunSQL,
kein ORM-DDL. Ergänzt das Singleton-Firmenprofil (0023) um die Parameter, die ein
DATEV-EXTF-Buchungsstapel („Buchungsstapel") braucht, damit der Steuerberater die
Ausgangsrechnungen importieren kann.

Fachquelle: DATEV-Formatbeschreibung „EXTF Buchungsstapel". Ein Buchungsstapel
trägt im Kopf u. a. Berater-/Mandantennummer, Wirtschaftsjahresbeginn und die
Sachkontenlänge; die Buchungssätze verweisen auf Sach-/Personenkonten des beim
Berater geführten Kontenrahmens (SKR03/SKR04). Diese Werte sind mandantenspezifisch
und kommen NICHT aus dem MCN-Fachschema — sie werden hier am Firmenprofil gepflegt.

Grundsatzentscheidungen:

1. **Am company_profile, kein eigenes Singleton.** Das Firmenprofil ist ohnehin die
   „eine Wahrheit" über das ausstellende Unternehmen und wird bereits als Singleton
   gepflegt (Service firma.py, Einstellungen → Firmenprofil). Die DATEV-Parameter
   sind Teil dieser Selbstbeschreibung; ein separates Config-Objekt bloß für den
   Export brächte eine zweite Service-/API-/Formularschicht ohne Mehrwert.

2. **Konten sind optionale Overrides (NULL = SKR-Standard aus dem Code).** Die
   Erlös-/Debitorenkonten haben in SKR03/SKR04 wohlbekannte Standardnummern
   (SKR03: 8400/8300 Erlöse 19/7 %, 1400 Forderungen aLuL; SKR04: 4400/4300,
   1200). Der Service leitet sie aus dem gewählten Kontenrahmen ab; nur wer davon
   abweicht, trägt hier eine eigene Kontonummer ein. So bleibt das Formular für den
   Regelfall leer und dennoch anpassbar.

3. **Nur additive, nullbare Spalten.** Keine Datenmigration, kein Umbau; ein
   bestehendes Profil bleibt gültig (Export ist dann bis zur Pflege gesperrt — der
   Service verlangt Berater-/Mandantennummer und Kontenrahmen und meldet sonst 422).

Reverse entfernt die Spalten wieder (keine Fachdaten entstehen dadurch).
"""
from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE company.company_profile
    -- DATEV-Identität des Mandanten beim Steuerberater
    ADD COLUMN datev_consultant_number   text     NULL,   -- Beraternummer (1001–9999999)
    ADD COLUMN datev_client_number        text     NULL,   -- Mandantennummer (1–99999)
    ADD COLUMN datev_chart_of_accounts    text     NULL,   -- 'SKR03' | 'SKR04'
    ADD COLUMN datev_account_length       smallint NULL,   -- Sachkontenlänge (4–8)
    ADD COLUMN datev_fiscal_year_start_month smallint NULL, -- Wirtschaftsjahresbeginn (Monat 1–12)
    -- Optionale Konto-Overrides (NULL = SKR-Standard aus dem Service)
    ADD COLUMN datev_debtor_account          text  NULL,   -- Sammeldebitor (Forderungen aLuL)
    ADD COLUMN datev_revenue_account_full     text NULL,   -- Erlöse voller Steuersatz (19 %)
    ADD COLUMN datev_revenue_account_reduced  text NULL,   -- Erlöse ermäßigt (7 %)
    ADD COLUMN datev_revenue_account_free      text NULL,  -- Erlöse steuerfrei (0 %)
    ADD COLUMN datev_revenue_account_reverse   text NULL,  -- Erlöse §13b (Reverse-Charge)
    ADD CONSTRAINT company_profile_datev_skr_check
        CHECK (datev_chart_of_accounts IS NULL
               OR datev_chart_of_accounts IN ('SKR03', 'SKR04')),
    ADD CONSTRAINT company_profile_datev_account_length_check
        CHECK (datev_account_length IS NULL
               OR datev_account_length BETWEEN 4 AND 8),
    ADD CONSTRAINT company_profile_datev_fy_month_check
        CHECK (datev_fiscal_year_start_month IS NULL
               OR datev_fiscal_year_start_month BETWEEN 1 AND 12);
"""

REVERSE_SQL = r"""
ALTER TABLE company.company_profile
    DROP CONSTRAINT company_profile_datev_fy_month_check,
    DROP CONSTRAINT company_profile_datev_account_length_check,
    DROP CONSTRAINT company_profile_datev_skr_check,
    DROP COLUMN datev_revenue_account_reverse,
    DROP COLUMN datev_revenue_account_free,
    DROP COLUMN datev_revenue_account_reduced,
    DROP COLUMN datev_revenue_account_full,
    DROP COLUMN datev_debtor_account,
    DROP COLUMN datev_fiscal_year_start_month,
    DROP COLUMN datev_account_length,
    DROP COLUMN datev_chart_of_accounts,
    DROP COLUMN datev_client_number,
    DROP COLUMN datev_consultant_number;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0050_acquisitionsource"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
