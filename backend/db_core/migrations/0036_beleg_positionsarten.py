"""Alternativ- und Bedarfspositionen auf Angebots-/Rechnungszeilen.

Fachlicher Hintergrund (Hero: „Wie kann ich Alternative Positionen oder
Bedarfspositionen (Eventualposition) kenntlich machen"): Eine Position kann als
**Alternative** (Ausweichvariante) oder als **Bedarfsposition/Eventualposition**
(nur bei Bedarf zu erbringen) gekennzeichnet werden. Beide tragen einen Betrag,
werden im PDF in Klammern ausgewiesen und zählen **nicht** zur Gesamtsumme.

Umsetzung als eigene Spalte `line_kind`, nicht als weiterer `line_type`-Wert:
`line_type` sagt, WAS die Position ist (Material, Arbeitszeit, Text …) und steuert,
ob sie Beträge tragen darf. `line_kind` sagt, OB sie in die Summe zählt. Beides ist
orthogonal — eine Alternative kann Material oder Arbeitszeit sein.

Damit müssen die Summenprüfungen mitgezogen werden: `assert_quote_totals` und
`assert_invoice_totals` (Beschluss B-19) vergleichen die Kopfsummen mit der Summe
der Positionen. Zählten sie Alternativen mit, wäre jeder Beleg mit einer
Alternativposition beim Versand/der Veröffentlichung als „Summen inkonsistent"
abgewiesen worden. Die Funktionen werden deshalb hier ersetzt und filtern
zusätzlich auf `line_kind = 'NORMAL'`.

Die Steuersatz-Prüfung (P3-05) bleibt bewusst auf ALLEN Betragspositionen scharf,
auch auf Alternativen: ein ungültiger Steuercode ist auch dann ein Fehler, wenn die
Position nicht in die Summe zählt — der Kunde liest den Betrag trotzdem.

Mindestens eine Betragsposition (v_lines) meint jetzt eine *summenwirksame*
Position: ein Beleg, der ausschließlich aus Alternativen besteht, hat einen
Gesamtbetrag von 0 und ist kein gültiger Beleg.
"""
from django.db import migrations

FORWARD_SQL = r"""
-- ---------------------------------------------------------------------------
-- 1. line_kind auf beiden Positionstabellen
-- ---------------------------------------------------------------------------
ALTER TABLE invoicing.quote_line
    ADD COLUMN line_kind text NOT NULL DEFAULT 'NORMAL'
        CHECK (line_kind IN ('NORMAL', 'ALTERNATIV', 'BEDARF'));
ALTER TABLE invoicing.invoice_line
    ADD COLUMN line_kind text NOT NULL DEFAULT 'NORMAL'
        CHECK (line_kind IN ('NORMAL', 'ALTERNATIV', 'BEDARF'));

-- Strukturzeilen (TEXT/ZWISCHENSUMME) tragen keinen Betrag und können deshalb
-- weder Alternative noch Bedarfsposition sein.
ALTER TABLE invoicing.quote_line
    ADD CONSTRAINT quote_line_kind_needs_amount
    CHECK (line_kind = 'NORMAL' OR line_type NOT IN ('TEXT', 'ZWISCHENSUMME'));
ALTER TABLE invoicing.invoice_line
    ADD CONSTRAINT invoice_line_kind_needs_amount
    CHECK (line_kind = 'NORMAL' OR line_type NOT IN ('TEXT', 'ZWISCHENSUMME'));

-- ---------------------------------------------------------------------------
-- 2. Summenprüfung Angebot (B-19) — Alternativen/Bedarf zählen nicht mit
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION invoicing.assert_quote_totals(p_quote_id uuid) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_quote     invoicing.quote%ROWTYPE;
    v_net       numeric;
    v_tax       numeric;
    v_lines     integer;
BEGIN
    SELECT * INTO v_quote FROM invoicing.quote WHERE id = p_quote_id FOR UPDATE;
    IF NOT FOUND THEN RETURN; END IF;

    SELECT count(*) FILTER (WHERE line_type NOT IN ('TEXT', 'ZWISCHENSUMME')
                              AND line_kind = 'NORMAL'),
           coalesce(sum(net_amount) FILTER (WHERE line_type NOT IN ('TEXT', 'ZWISCHENSUMME')
                                              AND line_kind = 'NORMAL'), 0)
    INTO v_lines, v_net
    FROM invoicing.quote_line WHERE quote_id = p_quote_id;

    SELECT coalesce(sum(group_tax), 0) INTO v_tax
    FROM (
        SELECT round(sum(net_amount) * tax_rate_percent / 100, 2) AS group_tax
        FROM invoicing.quote_line
        WHERE quote_id = p_quote_id AND line_type NOT IN ('TEXT', 'ZWISCHENSUMME')
          AND line_kind = 'NORMAL'
        GROUP BY tax_code, tax_rate_percent
    ) g;

    IF v_lines = 0 THEN
        RAISE EXCEPTION 'Angebot %: mindestens eine summenwirksame Betragsposition erforderlich', p_quote_id;
    END IF;

    -- P3-05: gilt für ALLE Betragspositionen, auch Alternativen/Bedarf.
    PERFORM 1
    FROM invoicing.quote_line l
    JOIN invoicing.tax_code t ON t.code = l.tax_code
    WHERE l.quote_id = p_quote_id AND l.line_type NOT IN ('TEXT', 'ZWISCHENSUMME')
      AND (l.tax_rate_percent <> t.rate_percent
           OR coalesce(v_quote.quote_date, (now() AT TIME ZONE 'UTC')::date) < t.valid_from
           OR (t.valid_until IS NOT NULL
               AND coalesce(v_quote.quote_date, (now() AT TIME ZONE 'UTC')::date) >= t.valid_until))
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION
            'Angebot %: Positions-Steuersatz weicht vom gültigen Steuercode ab (B-18/B-19/P3-05)', p_quote_id;
    END IF;
    IF v_quote.net_total IS DISTINCT FROM v_net
       OR v_quote.tax_total IS DISTINCT FROM v_tax
       OR v_quote.gross_total IS DISTINCT FROM (v_net + v_tax) THEN
        RAISE EXCEPTION
            'Angebot %: Summen inkonsistent (erwartet Netto %, Steuer %, Brutto %) — B-19',
            p_quote_id, v_net, v_tax, v_net + v_tax;
    END IF;
END;
$$;

-- ---------------------------------------------------------------------------
-- 3. Summenprüfung Rechnung (B-19) — analog
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION invoicing.assert_invoice_totals(p_invoice_id uuid) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_invoice invoicing.invoice%ROWTYPE;
    v_net     numeric;
    v_tax     numeric;
    v_lines   integer;
BEGIN
    SELECT * INTO v_invoice FROM invoicing.invoice WHERE id = p_invoice_id FOR UPDATE;
    IF NOT FOUND THEN RETURN; END IF;

    SELECT count(*) FILTER (WHERE line_type NOT IN ('TEXT', 'ZWISCHENSUMME')
                              AND line_kind = 'NORMAL'),
           coalesce(sum(net_amount) FILTER (WHERE line_type NOT IN ('TEXT', 'ZWISCHENSUMME')
                                              AND line_kind = 'NORMAL'), 0)
    INTO v_lines, v_net
    FROM invoicing.invoice_line WHERE invoice_id = p_invoice_id;

    SELECT coalesce(sum(group_tax), 0) INTO v_tax
    FROM (
        SELECT round(sum(net_amount) * tax_rate_percent / 100, 2) AS group_tax
        FROM invoicing.invoice_line
        WHERE invoice_id = p_invoice_id AND line_type NOT IN ('TEXT', 'ZWISCHENSUMME')
          AND line_kind = 'NORMAL'
        GROUP BY tax_code, tax_rate_percent
    ) g;

    IF v_lines = 0 THEN
        RAISE EXCEPTION 'Rechnung %: mindestens eine summenwirksame Betragsposition erforderlich', p_invoice_id;
    END IF;

    PERFORM 1
    FROM invoicing.invoice_line l
    JOIN invoicing.tax_code t ON t.code = l.tax_code
    WHERE l.invoice_id = p_invoice_id AND l.line_type NOT IN ('TEXT', 'ZWISCHENSUMME')
      AND (l.tax_rate_percent <> t.rate_percent
           OR coalesce(v_invoice.invoice_date, (now() AT TIME ZONE 'UTC')::date) < t.valid_from
           OR (t.valid_until IS NOT NULL
               AND coalesce(v_invoice.invoice_date, (now() AT TIME ZONE 'UTC')::date) >= t.valid_until))
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION
            'Rechnung %: Positions-Steuersatz weicht vom gültigen Steuercode ab (B-18/B-19/P3-05)', p_invoice_id;
    END IF;
    IF v_invoice.net_total IS DISTINCT FROM v_net
       OR v_invoice.tax_total IS DISTINCT FROM v_tax
       OR v_invoice.gross_total IS DISTINCT FROM (v_net + v_tax) THEN
        RAISE EXCEPTION
            'Rechnung %: Summen inkonsistent (erwartet Netto %, Steuer %, Brutto %) — B-19',
            p_invoice_id, v_net, v_tax, v_net + v_tax;
    END IF;
END;
$$;
"""

class Migration(migrations.Migration):
    """Nicht rückwärts fahrbar.

    Ein Reverse müsste die Spalte `line_kind` entfernen UND beide Summenfunktionen
    auf ihre Altfassung zurücksetzen — sonst referenzieren sie eine Spalte, die es
    nicht mehr gibt, und jeder Versand/jede Veröffentlichung schlägt fehl. Auf einem
    GoBD-Schema mit bereits festgeschriebenen Belegen ist ein Rückbau ohnehin keine
    zulässige Operation; ein halbgarer Reverse-Pfad wäre gefährlicher als keiner.
    """

    dependencies = [
        ("db_core", "0035_receipt_line_audit"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=migrations.RunSQL.noop),
    ]
