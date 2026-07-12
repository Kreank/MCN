"""DATEV: Abschlags-/Teilrechnungen wahlweise auf ein Anzahlungskonto buchen.

Fachlicher Hintergrund
----------------------
Eine Abschlagsrechnung ist umsatzsteuerlich **nicht zwingend ein Erlös**. Zwei
Sichtweisen sind in der Praxis üblich, und welche richtig ist, hängt am Vertrag:

- **Teilleistung** (§ 13 Abs. 1 Nr. 1 Buchst. a Satz 2 UStG): ein wirtschaftlich
  abgrenzbarer, abgenommener Leistungsteil wird endgültig abgerechnet. Der Betrag
  IST Erlös und gehört auf ein Erlöskonto. So bucht MCN bisher — und so bleibt es
  im Modus `ERLOES`.
- **Anzahlung/Vorauszahlung** (§ 13 Abs. 1 Nr. 1 Buchst. a Satz 4 UStG): es wird
  vor Erbringung der Leistung Geld verlangt. Die Umsatzsteuer entsteht bereits mit
  der Vereinnahmung, der **Ertrag** aber erst mit der Schlussrechnung. Der Betrag
  gehört deshalb als **Verbindlichkeit** auf ein Konto „Erhaltene, versteuerte
  Anzahlungen" und wird mit der Schlussrechnung auf Erlös umgebucht. Das ist der
  Modus `ANZAHLUNG`.

Der Unterschied ist keine Formsache: im Modus ERLOES weist die Gewinn- und
Verlustrechnung den Umsatz bereits im Monat der Abschlagsrechnung aus, im Modus
ANZAHLUNG erst mit der Schlussrechnung. Welche Buchung für den Betrieb richtig
ist, entscheidet der **Steuerberater** — deshalb ist es ein Schalter und keine
Annahme.

Warum bleibt der Default ERLOES?
--------------------------------
Bestandsdaten dürfen sich nicht still ändern. Wer heute exportiert, bekommt nach
dieser Migration exakt dieselbe Datei wie vorher. Der Modus wirkt erst, wenn er
bewusst umgestellt wird.

Modellierung
------------
Am Firmenprofil (0051 hat die übrige DATEV-Konfiguration bereits dort verankert:
ein Singleton, ein Formular, eine Service-Schicht). Neu:

- `datev_advance_mode`  — 'ERLOES' | 'ANZAHLUNG', NOT NULL DEFAULT 'ERLOES'.
- `datev_advance_account_{full,reduced,free,reverse}` — optionale Overrides der
  Anzahlungskonten je Steuersatz, exakt analog zu den Erlöskonten (NULL =
  SKR-Standard aus dem Service, siehe `services/datev._SKR_DEFAULTS`).

Rückwärts: Spalten entfallen wieder (der Modus ist reine Exportkonfiguration —
es entstehen keine Fachdaten, die davon abhingen; die erzeugten CSV-Dateien
liegen ohnehin außerhalb der Datenbank).
"""
from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE company.company_profile
    -- Wie werden Abschlags-/Teilrechnungen gebucht?
    --   ERLOES    = wie bisher gegen das Erlöskonto (Teilleistung).
    --   ANZAHLUNG = gegen das Konto „Erhaltene, versteuerte Anzahlungen";
    --               die Schlussrechnung löst sie wieder auf.
    ADD COLUMN datev_advance_mode text NOT NULL DEFAULT 'ERLOES',
    -- Optionale Overrides der Anzahlungskonten (NULL = SKR-Standard im Service)
    ADD COLUMN datev_advance_account_full    text NULL,  -- Anzahlungen 19 % USt
    ADD COLUMN datev_advance_account_reduced text NULL,  -- Anzahlungen  7 % USt
    ADD COLUMN datev_advance_account_free    text NULL,  -- Anzahlungen steuerfrei
    ADD COLUMN datev_advance_account_reverse text NULL,  -- Anzahlungen §13b
    ADD CONSTRAINT company_profile_datev_advance_mode_check
        CHECK (datev_advance_mode IN ('ERLOES', 'ANZAHLUNG'));

COMMENT ON COLUMN company.company_profile.datev_advance_mode IS
    'DATEV-Export: Abschlags-/Teilrechnungen als Erlös (Teilleistung) oder auf ein Anzahlungskonto (Vorauszahlung) buchen. Die Schlussrechnung löst Anzahlungen auf.';
"""

REVERSE_SQL = r"""
ALTER TABLE company.company_profile
    DROP CONSTRAINT company_profile_datev_advance_mode_check,
    DROP COLUMN datev_advance_account_reverse,
    DROP COLUMN datev_advance_account_free,
    DROP COLUMN datev_advance_account_reduced,
    DROP COLUMN datev_advance_account_full,
    DROP COLUMN datev_advance_mode;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0062_freier_termin"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
