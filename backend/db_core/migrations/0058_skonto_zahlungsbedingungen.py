"""Zahlungsbedingungen und Skonto je Rechnung (invoicing.invoice).

Fachlicher Hintergrund (User-Entscheidung 2026-07-12): Skonto ist **kein
Kundenstandard**, sondern wird **je Rechnung** entschieden. Es gibt deshalb
bewusst kein Zahlungsbedingungs-Profil am Kontakt, sondern drei Felder am Beleg:

- `payment_term_days`  — Zahlungsziel in Tagen (netto).
- `discount_percent`   — Skontosatz.
- `discount_days`      — Skontofrist in Tagen.

`due_date` bleibt die **harte Fälligkeitsspalte**, an der Mahnwesen, offene
Posten und DATEV hängen. `payment_term_days` ersetzt sie nicht, sondern leitet
sie beim Veröffentlichen ab, wenn sie leer geblieben ist. So bleibt der bisherige
Fälligkeitsbegriff unverändert gültig.

Physisch abgesichert:
- Wertebereiche (0..365 Tage; Skonto echt zwischen 0 und 100 %).
- **Paarigkeit**: Skontosatz und Skontofrist gibt es nur gemeinsam — ein Satz
  ohne Frist wäre nicht ausrechenbar, eine Frist ohne Satz bedeutungslos.
- **Frist <= Ziel**: eine Skontofrist nach dem Zahlungsziel ist sinnlos.
- **Kreditbelege (GUTSCHRIFT/STORNO) tragen keine Zahlungsbedingungen**: sie
  fordern kein Geld, es gibt nichts zu skontieren. Sonst stünde auf einer
  Gutschrift ein „Zahlbar mit 2 % Skonto bis …", das niemand einlösen kann.

Rückwärts: rein additiv (drei Spalten, keine Datenmigration) — der Reverse
entfernt sie wieder.
"""
from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE invoicing.invoice
    ADD COLUMN payment_term_days integer      NULL,
    ADD COLUMN discount_percent  numeric(5,2) NULL,
    ADD COLUMN discount_days     integer      NULL;

ALTER TABLE invoicing.invoice
    ADD CONSTRAINT invoice_payment_term_days_range
        CHECK (payment_term_days IS NULL
               OR (payment_term_days >= 0 AND payment_term_days <= 365)),
    ADD CONSTRAINT invoice_discount_percent_range
        CHECK (discount_percent IS NULL
               OR (discount_percent > 0 AND discount_percent < 100)),
    ADD CONSTRAINT invoice_discount_days_range
        CHECK (discount_days IS NULL
               OR (discount_days >= 0 AND discount_days <= 365)),
    -- Skontosatz und Skontofrist nur gemeinsam.
    ADD CONSTRAINT invoice_discount_pair
        CHECK ((discount_percent IS NULL) = (discount_days IS NULL)),
    -- Skontofrist liegt nicht nach dem Zahlungsziel.
    ADD CONSTRAINT invoice_discount_within_term
        CHECK (discount_days IS NULL OR payment_term_days IS NULL
               OR discount_days <= payment_term_days),
    -- Gutschrift/Storno fordern kein Geld: keine Zahlungsbedingungen.
    ADD CONSTRAINT invoice_credit_no_payment_terms
        CHECK (invoice_type NOT IN ('GUTSCHRIFT', 'STORNO')
               OR (payment_term_days IS NULL
                   AND discount_percent IS NULL
                   AND discount_days IS NULL));

COMMENT ON COLUMN invoicing.invoice.payment_term_days IS
    'Zahlungsziel in Tagen ab Belegdatum. Leitet due_date bei der Veröffentlichung ab, wenn diese leer ist; due_date bleibt die maßgebliche Fälligkeit.';
COMMENT ON COLUMN invoicing.invoice.discount_percent IS
    'Skontosatz in Prozent (nur zusammen mit discount_days).';
COMMENT ON COLUMN invoicing.invoice.discount_days IS
    'Skontofrist in Tagen ab Belegdatum (nur zusammen mit discount_percent).';
"""

REVERSE_SQL = r"""
ALTER TABLE invoicing.invoice
    DROP CONSTRAINT invoice_credit_no_payment_terms,
    DROP CONSTRAINT invoice_discount_within_term,
    DROP CONSTRAINT invoice_discount_pair,
    DROP CONSTRAINT invoice_discount_days_range,
    DROP CONSTRAINT invoice_discount_percent_range,
    DROP CONSTRAINT invoice_payment_term_days_range;

ALTER TABLE invoicing.invoice
    DROP COLUMN discount_days,
    DROP COLUMN discount_percent,
    DROP COLUMN payment_term_days;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0057_punchoutsession"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
