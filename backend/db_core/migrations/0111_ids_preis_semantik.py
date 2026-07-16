"""IDS-Connect: NetPrice-Semantik je Anbindung (GC-Quirk).

Anlass (Live-Repro 2026-07-16, produktive Instanz): Der Punchout zu G.U.T.
ONLINE PLUS liefert im Rückgabe-Warenkorb `NetPrice` als **Positionssumme**
(NetPrice = Menge × Einheitspreis), obwohl `PriceBasis=1.0` „je 1 Einheit"
behauptet und kein `AQU`-Element vorliegt. Damit verletzt GC die itek-Spec (ohne
AQU beziehen sich OfferPrice/NetPrice/PriceBasis auf 1 Einheit der Qty) und ist in
sich inkonsistent: `OfferPrice` bleibt je Einheit, `NetPrice` ist die Zeilensumme.
Der spec-treue Umrechner (`ids_warenkorb._unit_price`) übernahm dadurch die
Zeilensumme als Einheits-EK → EK und der daraus abgeleitete VK waren um den
Faktor Menge zu hoch.

Statt zu raten wird die Preis-Semantik **Händler-Konfiguration** (Repo-Doktrin:
deterministisch, nie raten): `net_price_semantics`
  * 'EINHEIT' — itek-Standard, NetPrice/PriceBasis ist bereits je Einheit (Default,
    byte-gleich zum bisherigen Verhalten für alle bestehenden Anbindungen),
  * 'GESAMT'  — GC-Quirk, NetPrice ist die Positionssumme; erst durch die Menge
    teilen (siehe HANDOFF-Absatz „GC-Quirk" und die itek-OrderItem-Spec).

Post-Baseline-DDL lebt als Django-RunSQL (nicht als db/migrations/*.sql), damit die
Baseline-Glob sie nicht doppelt anwendet (Muster wie 0042). Reine ADD-COLUMN-
Erweiterung; die bestehenden Schutz-/Audit-Trigger auf `pricing.supplier_connection`
(0029) decken die neue Spalte mit ab.
"""
from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE pricing.supplier_connection
    ADD COLUMN net_price_semantics text NOT NULL DEFAULT 'EINHEIT'
    CHECK (net_price_semantics IN ('EINHEIT', 'GESAMT'));

COMMENT ON COLUMN pricing.supplier_connection.net_price_semantics IS
    'Interpretation von OrderItem/NetPrice im IDS-Rückgabe-Warenkorb: '
    'EINHEIT = je Einheit (itek-Standard), GESAMT = Positionssumme (GC-Quirk, '
    'NetPrice zusaetzlich durch die Menge teilen). Default EINHEIT.';
"""

REVERSE_SQL = r"""
ALTER TABLE pricing.supplier_connection DROP COLUMN net_price_semantics;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0110_ai_proposal_loeschbar"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
