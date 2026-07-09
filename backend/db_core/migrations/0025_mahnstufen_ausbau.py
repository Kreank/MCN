"""Mahnstufen-Ausbau 3 -> 6 (Hero-Parität) + Aktivierbarkeit.

`invoicing.dunning_level` (db/-Migration 0025) seedet bislang 3 Stufen. Hero
kennt bis zu 6 (3 Zahlungserinnerungen + 3 Mahnungen), je Stufe aktivierbar.

Änderungen:
  * Neue Spalte `active boolean NOT NULL DEFAULT true` — eine Stufe lässt sich
    deaktivieren, ohne sie zu löschen (No-Delete; die Stufe kann historisch
    bereits in `dunning_notice` referenziert sein).
  * Vollausbau auf 6 Stufen als kohärente Hero-Leiter: Stufen 1–3 sind
    Zahlungserinnerungen, 4–6 Mahnungen. Die bestehenden Stufen 2/3 (vormals
    „Mahnung 1/2") werden dafür umbenannt; das ist reine Konfig-Stammdatenpflege
    (keine GoBD-Belege). Fristen streng aufsteigend.

Bewusst NICHT umgesetzt (laut Aufgabenstellung gemeldet statt erfunden):
  * `fee`/`interest_note` bleiben NULL — Gebühren/Verzugszinsen stehen unter
    STB-/GF-Vorbehalt (B-22). Es werden keine Beträge gesetzt.
  * Eine E-Mail-Template-/Dokumententyp-Referenz je Stufe (Roadmap 09/13) wird
    NICHT angelegt: die Zieltabellen (E-Mail-Template, Mahn-Dokumententyp)
    existieren noch nicht — eine tote FK-Spalte wäre irreführend.

Lücken-Entscheidung (B-22, lückenlose Eskalation):
  Der Trigger `invoicing.check_dunning_notice` erzwingt je Rechnung eine
  lückenlos aufsteigende Stufenfolge (nächste Stufe = max+1). Das `active`-Flag
  ist eine reine KONFIG-Ebene und ändert diesen Trigger NICHT. Damit die
  Konfiguration jederzeit ausführbar bleibt, wird die Konsistenz in der
  Service-Schicht (`services/firma.py::update_dunning_level`) durchgesetzt:
  die aktiven Stufen müssen einen lückenlosen Präfix {1..k} bilden. Eine
  „mittlere" Stufe zu deaktivieren, während eine höhere aktiv bleibt, wird
  damit **verboten** (422) — sonst entstünde eine Konfiguration, die der
  DB-Trigger nie ausführen könnte (Sprung von Stufe 1 auf 3). Zusätzlich lehnt
  `issue_dunning_notice` das Ausstellen einer deaktivierten Stufe ab.
"""
from django.db import migrations

CREATE_SQL = r"""
ALTER TABLE invoicing.dunning_level
    ADD COLUMN active boolean NOT NULL DEFAULT true;

-- Bestehende Stufen auf die Hero-Leiter (3 Zahlungserinnerungen) einordnen.
UPDATE invoicing.dunning_level SET label = '1. Zahlungserinnerung', days_after_due = 7  WHERE level = 1;
UPDATE invoicing.dunning_level SET label = '2. Zahlungserinnerung', days_after_due = 14 WHERE level = 2;
UPDATE invoicing.dunning_level SET label = '3. Zahlungserinnerung', days_after_due = 21 WHERE level = 3;

-- 3 Mahnstufen ergänzen. fee/interest_note bleiben NULL (STB-Vorbehalt B-22).
INSERT INTO invoicing.dunning_level (level, label, days_after_due, active) VALUES
    (4, '1. Mahnung', 35, true),
    (5, '2. Mahnung', 49, true),
    (6, '3. Mahnung', 63, true);
"""

DROP_SQL = r"""
DELETE FROM invoicing.dunning_level WHERE level IN (4, 5, 6);
UPDATE invoicing.dunning_level SET label = 'Zahlungserinnerung', days_after_due = 7  WHERE level = 1;
UPDATE invoicing.dunning_level SET label = 'Mahnung 1',          days_after_due = 21 WHERE level = 2;
UPDATE invoicing.dunning_level SET label = 'Mahnung 2',          days_after_due = 35 WHERE level = 3;
ALTER TABLE invoicing.dunning_level DROP COLUMN active;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0024_rechtematrix_company_modul"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
    ]
