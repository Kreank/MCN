"""Die Forderungsgrenze im Mahn-Trigger: gemahnt wird nur eine OFFENE FORDERUNG.

`invoicing.check_dunning_notice` (Migration 0025, B-22) prüfte bisher nur drei
Dinge: Rechnung veröffentlicht, zum `issued_at` fällig, Stufe lückenlos (max+1).
Was sie **nicht** prüfte: ob der Kunde überhaupt noch etwas schuldet.

Damit ließ sich eine Mahnstufe ausstellen auf
  * eine durch einen veröffentlichten **STORNO aufgehobene** Rechnung
    (offen 0,00 €) — reproduziert: `POST /invoices/{id}/dunning` → **201 Created**,
    anschließend versendete `send-email` den Text „… ist die Rechnung RE-… weiterhin
    offen";
  * eine **voll bezahlte** Rechnung (offen 0,00 €) — ebenfalls 201;
  * einen **Kreditbeleg** selbst (STORNO/GUTSCHRIFT fordern nichts; sie kamen bis
    hierher nur deshalb nicht durch, weil ihr Fälligkeitsdatum zufällig in der
    Zukunft lag — eine Grenze aus Versehen ist keine Grenze).

Liste, Filter, Mahnlauf und der UI-Knopf halten die Forderungsgrenze seit dem
Konsolidierungs-Slice sauber — **der einzige Pfad, der tatsächlich mahnt, hielt sie
nicht.** Projektlehre: *Was im Service sitzt, ist umgehbar; erst was im Trigger
sitzt, hält.* Der Service-Guard (`buchhaltung.mahnsperre`) liefert den benannten
Grund als 422; diese Migration macht die Grenze **physisch** — auch für jeden
künftigen Schreibpfad (KI-Agent, Skript, Import).

**Die Rechnung des offenen Betrags ist hier bewusst SQL-seitig gespiegelt**
(Brutto + veröffentlichte Kreditbelege − vorzeichenbehaftete Zahlungssumme). Sie ist
keine zweite *fachliche* Wahrheit, sondern dieselbe Formel in der Schicht, die sie
erzwingen kann: `buchhaltung.PAYMENT_SIGN` (+1 ZAHLUNG/TEILZAHLUNG/UEBERZAHLUNG,
−1 RUECKERSTATTUNG/STORNO_BUCHUNG) und `beleg.CREDIT_TYPES` (GUTSCHRIFT/STORNO).
Ein Drift-Test (`api/tests/test_mahnung_schreibpfad.py`) hält Trigger und
Zahlungsspiegel in Deckung: was `zahlungsspiegel()['mahnbar']` verneint, weist die
DB ab — und umgekehrt.

**Grenze der Trigger-Prüfung (ehrlich):** Sie liest den Zahlungsstand ohne Sperre
auf `invoicing.payment`. Eine Zahlung, die parallel in einer noch offenen
Transaktion gebucht wird, sieht sie unter READ COMMITTED nicht — dann entsteht im
Extremfall eine Mahnstufe auf eine im selben Moment beglichene Rechnung. Das ist
dieselbe Nebenläufigkeitsgrenze, die die bestehenden B-22-Prüfungen schon haben, und
sie ist konservativ: Der Fehler wäre eine Mahnung zu viel, nie eine Forderung zu
wenig. Eine Sperre auf allen Zahlungen einer Rechnung hätte die Zahlungserfassung
gegen den Mahnlauf serialisiert — das ist der teurere Tausch.

Reines DDL (Funktionsrumpf) → `makemigrations --check` bleibt unberührt.
"""
from django.db import migrations

FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION invoicing.check_dunning_notice() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_invoice invoicing.invoice%ROWTYPE;
    v_max     integer;
    v_credit  numeric(15,2);
    v_paid    numeric(15,2);
    v_open    numeric(15,2);
BEGIN
    SELECT * INTO v_invoice FROM invoicing.invoice WHERE id = NEW.invoice_id FOR SHARE;
    IF v_invoice.status IS DISTINCT FROM 'VEROEFFENTLICHT' THEN
        RAISE EXCEPTION 'Mahnung: Rechnung % ist nicht veröffentlicht (B-22)', NEW.invoice_id;
    END IF;

    -- Ein Kreditbeleg fordert nichts: STORNO/GUTSCHRIFT tragen negative Summen und
    -- haben kein Zahlungsziel gegen den Kunden. Vor der Fälligkeitsprüfung, damit
    -- der Grund benannt wird (statt „nicht fällig" — das war die zufällige Grenze).
    IF v_invoice.invoice_type IN ('GUTSCHRIFT', 'STORNO') THEN
        RAISE EXCEPTION
            'Mahnung: Beleg % ist ein Kreditbeleg (%) und fordert kein Geld vom Kunden (B-22)',
            NEW.invoice_id, v_invoice.invoice_type;
    END IF;

    IF v_invoice.due_date IS NULL OR NEW.issued_at <= v_invoice.due_date THEN
        RAISE EXCEPTION 'Mahnung: Rechnung % ist zum % nicht fällig (B-22)', NEW.invoice_id, NEW.issued_at;
    END IF;

    -- Der veröffentlichte STORNO hebt die Rechnung auf — sie fordert nichts mehr.
    IF EXISTS (
        SELECT 1 FROM invoicing.invoice c
        WHERE c.reference_invoice_id = NEW.invoice_id
          AND c.invoice_type = 'STORNO'
          AND c.status = 'VEROEFFENTLICHT'
    ) THEN
        RAISE EXCEPTION
            'Mahnung: Rechnung % ist storniert und fordert nichts mehr (B-22)',
            NEW.invoice_id;
    END IF;

    -- Offener Betrag = Brutto + veröffentlichte Kreditbelege (≤ 0) − Zahlungen
    -- (vorzeichenbehaftet, PAYMENT_SIGN). Gemahnt wird nur, was > 0 ist.
    SELECT coalesce(sum(c.gross_total), 0) INTO v_credit
    FROM invoicing.invoice c
    WHERE c.reference_invoice_id = NEW.invoice_id
      AND c.invoice_type IN ('GUTSCHRIFT', 'STORNO')
      AND c.status = 'VEROEFFENTLICHT';

    SELECT coalesce(sum(
        CASE p.payment_type
            WHEN 'ZAHLUNG'         THEN  p.amount
            WHEN 'TEILZAHLUNG'     THEN  p.amount
            WHEN 'UEBERZAHLUNG'    THEN  p.amount
            WHEN 'RUECKERSTATTUNG' THEN -p.amount
            WHEN 'STORNO_BUCHUNG'  THEN -p.amount
            ELSE 0
        END
    ), 0) INTO v_paid
    FROM invoicing.payment p
    WHERE p.invoice_id = NEW.invoice_id;

    v_open := coalesce(v_invoice.gross_total, 0) + v_credit - v_paid;
    IF v_open <= 0 THEN
        RAISE EXCEPTION
            'Mahnung: Rechnung % hat keine offene Forderung mehr (offen: % EUR) — gemahnt wird nur, was der Kunde noch schuldet (B-22)',
            NEW.invoice_id, v_open;
    END IF;

    SELECT coalesce(max(level), 0) INTO v_max
    FROM invoicing.dunning_notice WHERE invoice_id = NEW.invoice_id;
    IF NEW.level <> v_max + 1 THEN
        RAISE EXCEPTION
            'Mahnung: Stufe % ist nicht die nächste Stufe (erwartet %) — B-22', NEW.level, v_max + 1;
    END IF;
    RETURN NEW;
END;
$$;
"""

# Rückwärts: der Rumpf aus Migration 0025 (nur veröffentlicht / fällig / lückenlos).
REVERSE_SQL = r"""
CREATE OR REPLACE FUNCTION invoicing.check_dunning_notice() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_invoice invoicing.invoice%ROWTYPE;
    v_max     integer;
BEGIN
    SELECT * INTO v_invoice FROM invoicing.invoice WHERE id = NEW.invoice_id FOR SHARE;
    IF v_invoice.status IS DISTINCT FROM 'VEROEFFENTLICHT' THEN
        RAISE EXCEPTION 'Mahnung: Rechnung % ist nicht veröffentlicht (B-22)', NEW.invoice_id;
    END IF;
    IF v_invoice.due_date IS NULL OR NEW.issued_at <= v_invoice.due_date THEN
        RAISE EXCEPTION 'Mahnung: Rechnung % ist zum % nicht fällig (B-22)', NEW.invoice_id, NEW.issued_at;
    END IF;
    SELECT coalesce(max(level), 0) INTO v_max
    FROM invoicing.dunning_notice WHERE invoice_id = NEW.invoice_id;
    IF NEW.level <> v_max + 1 THEN
        RAISE EXCEPTION
            'Mahnung: Stufe % ist nicht die nächste Stufe (erwartet %) — B-22', NEW.level, v_max + 1;
    END IF;
    RETURN NEW;
END;
$$;
"""


class Migration(migrations.Migration):

    dependencies = [("db_core", "0096_index_referenzbeleg")]

    operations = [migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL)]
