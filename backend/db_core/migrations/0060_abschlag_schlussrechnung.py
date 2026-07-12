"""Abschlags-/Teil-/Schlussrechnung: Verkettung, Anrechnung und Tore.

Fachlicher Hintergrund (VOB/BGB-Praxis, Kernprozess Handwerk)
-------------------------------------------------------------
- **Abschlagsrechnung (AR)**: Rechnung über einen Teil der Leistung **während**
  der Ausführung. Sie ist eine echte, zahlbare Rechnung (offener Posten,
  Mahnwesen, Skonto) und hängt am Auftrag.
- **Teilrechnung (TR)**: rechnet einen abgeschlossenen Leistungsteil endgültig
  ab. Technisch identisch zur AR (gleiche Tore, gleiche Verkettung); der Typ
  bleibt getrennt, weil er fachlich etwas anderes aussagt.
- **Schlussrechnung (SR)**: rechnet die Gesamtleistung ab und **rechnet die
  bereits gestellten AR/TR desselben Auftrags an**. Der Zahlbetrag der SR ist
  die Differenz (§ 14 Abs. 5 UStG: die bereits berechneten Teilentgelte **und
  die darauf entfallenden Steuerbeträge** sind abzusetzen).

Die drei Belegarten standen bereits in der `invoice_type`-Codeliste (0019), aber
ohne jede Fachlogik. Diese Migration liefert sie nach.

Modellierung der Anrechnung: **negative Positionen je Steuersatz** (Weg a)
--------------------------------------------------------------------------
Die Anrechnung entsteht als **echte Position** auf der SR: je angerechneter AR
und je Steuersatz eine Zeile mit negativem `net_amount`
(`quantity = 1`, `unit_price = −Netto der Steuergruppe`; die DB verlangt
`quantity > 0`, der Einzelpreis darf negativ sein).

Warum nicht Kopf-/Anrechnungsfelder mit angepasster Summenprüfung (Weg b)?
- Die Summenkette `assert_invoice_totals` (B-19) trägt negative Positionen
  **ohne Änderung** — GoBD-Invariante bleibt unangetastet.
- Der offene Posten ist abgeleitet als `gross_total − Zahlungen`. Mit Weg (a)
  ist `gross_total` **der Zahlbetrag**; Mahnwesen, Zahlungsspiegel, DATEV und
  Auswertungen bleiben unverändert korrekt. Mit Weg (b) stünde die SR mit dem
  vollen Leistungsbetrag als Forderung im Mahnwesen — die Abschläge wären
  doppelt gefordert, solange nicht jede dieser Ableitungen umgebaut wird.
- **Je Steuersatz** wird abgezogen, nicht als Brutto-Klumpen: sonst stimmte die
  USt-Aufteilung der SR nicht (und das EN16931-XML wäre in sich unstimmig).

Explizite Verkettung: `invoicing.invoice_advance`
--------------------------------------------------
Die Positionen allein wären keine belastbare Verkettung (ein Positionstext ist
kein Fremdschlüssel). Die Link-Tabelle friert je (SR, AR, Steuercode) den
angerechneten Netto-/Steuer-/Bruttobetrag ein und macht die Kette physisch
prüfbar:

- eine AR **zweimal in derselben SR** → UNIQUE (final, advance, tax_code),
- eine AR in **zwei veröffentlichten SR** → Trigger + Veröffentlichungstor
  (mit Zeilensperre auf der AR, damit zwei parallele Veröffentlichungen sich
  nicht gegenseitig überholen),
- Anrechnung einer **nicht veröffentlichten** oder **stornierten/gutgeschriebenen**
  AR → Trigger + Tor,
- AR und SR an **verschiedenen Aufträgen/Liegenschaften** → Trigger.

Zusätzlich trägt jede Anrechnungsposition die Spalte
`invoice_line.advance_invoice_id`. Das Veröffentlichungstor verlangt, dass
Link-Tabelle und Positionen **deckungsgleich** sind (je AR und Steuercode
derselbe Betrag). Damit kann die Anrechnung nicht auseinanderlaufen: Wer die
Positionen der SR im Editor ersetzt und dabei die Anrechnung verliert, bekommt
beim Veröffentlichen einen harten Fehler — statt still den vollen Betrag ein
zweites Mal zu fordern.

Auftrags-Tor (B-08) wird belegartabhängig
------------------------------------------
Bisher verlangte JEDE Nicht-Kreditrechnung einen `KAUFMAENNISCH_GEPRUEFT`en
Auftrag. Eine Abschlagsrechnung wird aber **während** der Ausführung gestellt —
mit dem alten Tor wäre sie unmöglich gewesen. Neu:

- AR/TR: Auftrag ab **FREIGEGEBEN** (FREIGEGEBEN, IN_PLANUNG, IN_AUSFUEHRUNG,
  TECHNISCH_ABGESCHLOSSEN, KAUFMAENNISCH_GEPRUEFT, ABGERECHNET). Ein Auftrag im
  ENTWURF/FREIGABE_AUSSTEHEND/STORNIERT trägt keine Rechnung — ohne Freigabe gibt
  es keine beauftragte Leistung, für die man einen Abschlag verlangen könnte.
- RECHNUNG/SCHLUSSRECHNUNG: **unverändert** KAUFMAENNISCH_GEPRUEFT/ABGERECHNET.

Storno einer angerechneten Abschlagsrechnung
---------------------------------------------
**Verboten, solange die anrechnende SR veröffentlicht ist** (Tor im
Veröffentlichungspfad des Kreditbelegs). Sonst entstünde eine veröffentlichte,
unveränderliche SR, die einen Beleg anrechnet, den es nicht mehr gibt — der
Kunde bekäme die Abschlagssumme geschenkt. Gilt für STORNO **und** GUTSCHRIFT:
auch eine Teilgutschrift zur AR verschöbe den eingefrorenen Anrechnungsbetrag.

Der Weg zurück ist **allein das STORNO der Schlussrechnung**: es dreht die
Anrechnung vollständig mit um und gibt den Abschlag wieder frei
(`advance_blocking_final` ignoriert stornierte SR). Eine **Gutschrift** zur SR
löst die Bindung NICHT (die SR bleibt bestehen) — deshalb ist eine Teilgutschrift
auf einer SR mit Anrechnung im Service ausdrücklich verboten.

Zwei physische Riegel dazu (Review-Befunde):
- **Ein Kreditbeleg darf kein positives ``gross_total`` haben.** Sonst ließe sich
  ausgerechnet die (negative) Anrechnungsposition „gutschreiben": die Invertierung
  macht daraus eine Forderung, und es entstünde eine GUTSCHRIFT über +Abschlag —
  der Betrag stünde erneut als offener Posten und würde gemahnt.
- **Eine Schlussrechnung darf keinen anrechenbaren Abschlag übergehen.** Wer die
  Abschläge abwählt, forderte die volle Leistung ein zweites Mal — auf einem
  danach unveränderlichen Beleg.

Rückwärts: nur solange keine Schlussrechnung mit Anrechnung veröffentlicht ist
(danach wäre der Rückbau eine Änderung an festgeschriebenen Belegen). Der
Reverse setzt die alte Fassung des Veröffentlichungstors wieder ein.
"""
from django.db import migrations

FORWARD_SQL = r"""
-- ---------------------------------------------------------------------------
-- 1. Anrechnungsposition kennt ihren Abschlag
-- ---------------------------------------------------------------------------
ALTER TABLE invoicing.invoice_line
    ADD COLUMN advance_invoice_id uuid NULL REFERENCES invoicing.invoice (id);

-- Eine Anrechnungsposition ist immer ein summenwirksamer NEGATIVER Betrag.
ALTER TABLE invoicing.invoice_line
    ADD CONSTRAINT invoice_line_advance_is_deduction CHECK (
        advance_invoice_id IS NULL
        OR (line_kind = 'NORMAL'
            AND line_type NOT IN ('TEXT', 'ZWISCHENSUMME')
            AND net_amount IS NOT NULL
            AND net_amount < 0));

-- Je Beleg, Abschlag und Steuercode höchstens eine Anrechnungsposition — sonst
-- wäre der Abgleich mit der Link-Tabelle nicht eindeutig.
CREATE UNIQUE INDEX uq_invoice_line_advance
    ON invoicing.invoice_line (invoice_id, advance_invoice_id, tax_code)
    WHERE advance_invoice_id IS NOT NULL;

COMMENT ON COLUMN invoicing.invoice_line.advance_invoice_id IS
    'Nur auf Schlussrechnungen: die Position rechnet diese Abschlags-/Teilrechnung an (negativer Betrag, je Steuersatz eine Position).';

-- ---------------------------------------------------------------------------
-- 2. Link-Tabelle: welche Abschlagsrechnung wurde mit welchem Betrag angerechnet
-- ---------------------------------------------------------------------------
CREATE TABLE invoicing.invoice_advance (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    final_invoice_id    uuid NOT NULL REFERENCES invoicing.invoice (id),
    advance_invoice_id  uuid NOT NULL REFERENCES invoicing.invoice (id),
    tax_code            text NOT NULL REFERENCES invoicing.tax_code (code),
    tax_rate_percent    numeric(5, 2)  NOT NULL,
    -- Beträge POSITIV: sie sagen, WAS angerechnet wurde. Das Vorzeichen des
    -- Abzugs trägt die Position (net_amount < 0). Zwei Vorzeichenkonventionen an
    -- derselben Zahl wären eine Fehlerquelle ohne Nutzen.
    net_amount          numeric(15, 2) NOT NULL CHECK (net_amount > 0),
    tax_amount          numeric(15, 2) NOT NULL CHECK (tax_amount >= 0),
    gross_amount        numeric(15, 2) NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CHECK (final_invoice_id <> advance_invoice_id),
    CHECK (gross_amount = net_amount + tax_amount),
    UNIQUE (final_invoice_id, advance_invoice_id, tax_code)
);

CREATE INDEX idx_invoice_advance_advance
    ON invoicing.invoice_advance (advance_invoice_id);

COMMENT ON TABLE invoicing.invoice_advance IS
    'Verkettung Schlussrechnung → angerechnete Abschlags-/Teilrechnung, je Steuersatz eingefroren (GoBD-Nachvollziehbarkeit, Doppelanrechnungs-Sperre).';

CREATE TRIGGER trg_invoice_advance_updated_at
    BEFORE UPDATE ON invoicing.invoice_advance
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

-- ---------------------------------------------------------------------------
-- 3. Fachliche Prüfung der Verkettung (bei jedem Schreiben)
-- ---------------------------------------------------------------------------
-- Die eine Stelle, die sagt: „dieser Abschlag ist gebunden". Gebunden ist er,
-- wenn ihn eine VERÖFFENTLICHTE und NICHT STORNIERTE Schlussrechnung anrechnet.
-- Die Storno-Ausnahme ist wesentlich: wird eine Schlussrechnung storniert, muss
-- ihre Anrechnung wieder frei werden — sonst ließe sich der Auftrag nach einem
-- Storno nie wieder schlussrechnen, und der Abschlag bliebe für immer gebunden.
CREATE FUNCTION invoicing.advance_blocking_final(p_advance uuid, p_exclude uuid DEFAULT NULL)
RETURNS uuid LANGUAGE sql STABLE AS $$
    SELECT ia.final_invoice_id
      FROM invoicing.invoice_advance ia
      JOIN invoicing.invoice f ON f.id = ia.final_invoice_id
     WHERE ia.advance_invoice_id = p_advance
       AND (p_exclude IS NULL OR ia.final_invoice_id <> p_exclude)
       AND f.status = 'VEROEFFENTLICHT'
       AND NOT EXISTS (
           SELECT 1 FROM invoicing.invoice s
            WHERE s.reference_invoice_id = f.id
              AND s.invoice_type = 'STORNO'
              AND s.status = 'VEROEFFENTLICHT')
     LIMIT 1;
$$;

CREATE FUNCTION invoicing.check_invoice_advance() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_final    invoicing.invoice%ROWTYPE;
    v_advance  invoicing.invoice%ROWTYPE;
BEGIN
    SELECT * INTO v_final
    FROM invoicing.invoice WHERE id = NEW.final_invoice_id FOR SHARE;
    SELECT * INTO v_advance
    FROM invoicing.invoice WHERE id = NEW.advance_invoice_id FOR SHARE;

    IF v_final.invoice_type <> 'SCHLUSSRECHNUNG' THEN
        RAISE EXCEPTION
            'Anrechnung: nur eine Schlussrechnung kann Abschläge anrechnen (Beleg % ist %)',
            v_final.id, v_final.invoice_type;
    END IF;
    IF v_advance.invoice_type NOT IN ('ABSCHLAGSRECHNUNG', 'TEILRECHNUNG') THEN
        RAISE EXCEPTION
            'Anrechnung: Beleg % ist keine Abschlags-/Teilrechnung (%)',
            v_advance.id, v_advance.invoice_type;
    END IF;
    IF v_advance.status <> 'VEROEFFENTLICHT' THEN
        RAISE EXCEPTION
            'Anrechnung: die Abschlagsrechnung % ist nicht veröffentlicht (%)',
            v_advance.id, v_advance.status;
    END IF;
    IF v_final.work_order_id IS NULL
       OR v_final.work_order_id IS DISTINCT FROM v_advance.work_order_id THEN
        RAISE EXCEPTION
            'Anrechnung: Schlussrechnung und Abschlagsrechnung % gehören nicht zum selben Auftrag',
            v_advance.id;
    END IF;
    IF v_final.property_id IS DISTINCT FROM v_advance.property_id THEN
        RAISE EXCEPTION
            'Anrechnung: Schlussrechnung und Abschlagsrechnung % gehören nicht zur selben Liegenschaft',
            v_advance.id;
    END IF;
    -- Stornierte/gutgeschriebene Abschläge sind nicht anrechenbar: ihr Betrag
    -- steht nicht mehr (bzw. nicht mehr vollständig) in Rechnung.
    IF EXISTS (
        SELECT 1 FROM invoicing.invoice c
        WHERE c.reference_invoice_id = NEW.advance_invoice_id
          AND c.invoice_type IN ('STORNO', 'GUTSCHRIFT')
          AND c.status = 'VEROEFFENTLICHT'
    ) THEN
        RAISE EXCEPTION
            'Anrechnung: die Abschlagsrechnung % ist storniert oder gutgeschrieben',
            v_advance.id;
    END IF;
    -- Dieselbe Abschlagsrechnung darf nicht in zwei Schlussrechnungen stecken,
    -- sobald eine davon veröffentlicht ist (der endgültige Abgleich läuft im
    -- Veröffentlichungstor, das die Abschlagszeile exklusiv sperrt).
    IF invoicing.advance_blocking_final(NEW.advance_invoice_id, NEW.final_invoice_id)
       IS NOT NULL THEN
        RAISE EXCEPTION
            'Anrechnung: die Abschlagsrechnung % ist bereits in einer veröffentlichten Schlussrechnung angerechnet',
            v_advance.id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_invoice_advance_check
    BEFORE INSERT OR UPDATE ON invoicing.invoice_advance
    FOR EACH ROW EXECUTE FUNCTION invoicing.check_invoice_advance();

-- Die Verkettung ist — wie Positionen und Beteiligte — nur im Entwurf änderbar.
CREATE FUNCTION invoicing.protect_invoice_advance() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_status text;
    v_final  uuid := CASE WHEN TG_OP = 'DELETE'
                          THEN OLD.final_invoice_id ELSE NEW.final_invoice_id END;
BEGIN
    -- FOR SHARE serialisiert gegen eine laufende Veröffentlichung (wie P3-02).
    SELECT status INTO v_status FROM invoicing.invoice WHERE id = v_final FOR SHARE;
    IF v_status <> 'ENTWURF' THEN
        RAISE EXCEPTION
            'Schlussrechnung %: die Anrechnung ist nach Veröffentlichung unveränderlich (B-21)',
            v_final;
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;

CREATE TRIGGER trg_invoice_advance_protect
    BEFORE INSERT OR UPDATE OR DELETE ON invoicing.invoice_advance
    FOR EACH ROW EXECUTE FUNCTION invoicing.protect_invoice_advance();

-- Schutzstandard (No-Truncate/Audit; DELETE bleibt im Entwurf zulässig — exakt
-- wie bei invoice_line — und wird auditiert).
CREATE TRIGGER trg_invoice_advance_audit AFTER UPDATE ON invoicing.invoice_advance
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_invoice_advance_delete_audit AFTER DELETE ON invoicing.invoice_advance
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_delete();
CREATE TRIGGER trg_invoice_advance_no_truncate BEFORE TRUNCATE ON invoicing.invoice_advance
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE TRUNCATE ON invoicing.invoice_advance FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- 4. Veröffentlichungstor: belegartabhängiges Auftrags-Tor + Anrechnungsprüfung
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION invoicing.check_invoice_publish() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_debtors            integer;
    v_debtors_no_basis   integer;
    v_primary_recipients integer;
    v_order_status       text;
    v_bad_debtors        integer;
    v_ref_status         text;
    v_adv                record;
    v_adv_status         text;
    v_final_number       text;
    v_offene_abschlaege  integer;
BEGIN
    IF NEW.status <> 'VEROEFFENTLICHT' THEN
        RETURN NULL;
    END IF;

    PERFORM invoicing.assert_invoice_totals(NEW.id);

    SELECT count(*) FILTER (WHERE role = 'INVOICE_DEBTOR'),
           count(*) FILTER (WHERE role = 'INVOICE_DEBTOR' AND liability_basis IS NULL),
           count(*) FILTER (WHERE role = 'INVOICE_RECIPIENT' AND is_primary)
    INTO v_debtors, v_debtors_no_basis, v_primary_recipients
    FROM invoicing.invoice_party WHERE invoice_id = NEW.id;

    IF v_debtors < 1 THEN
        RAISE EXCEPTION
            'Rechnung %: Veröffentlichung ohne Rechnungsschuldner ist unzulässig (A-27)', NEW.id;
    END IF;
    IF v_debtors > 1 AND v_debtors_no_basis > 0 THEN
        RAISE EXCEPTION
            'Rechnung %: Mehrere Schuldner erfordern eine dokumentierte Grundlage je Schuldner (A-29); Standard sind getrennte Rechnungen (A-24)',
            NEW.id;
    END IF;
    IF v_primary_recipients <> 1 THEN
        RAISE EXCEPTION
            'Rechnung %: Genau ein primärer Rechnungsempfänger ist erforderlich (A-28)', NEW.id;
    END IF;

    -- Anrechnungspositionen und Verkettung gibt es NUR auf der Schlussrechnung.
    IF NEW.invoice_type <> 'SCHLUSSRECHNUNG' THEN
        IF EXISTS (SELECT 1 FROM invoicing.invoice_line
                   WHERE invoice_id = NEW.id AND advance_invoice_id IS NOT NULL) THEN
            RAISE EXCEPTION
                'Rechnung %: Anrechnungspositionen gibt es nur auf einer Schlussrechnung', NEW.id;
        END IF;
        IF EXISTS (SELECT 1 FROM invoicing.invoice_advance WHERE final_invoice_id = NEW.id) THEN
            RAISE EXCEPTION
                'Rechnung %: Abschläge kann nur eine Schlussrechnung anrechnen', NEW.id;
        END IF;
    END IF;

    IF NEW.invoice_type IN ('GUTSCHRIFT', 'STORNO') THEN
        -- Ein Kreditbeleg gibt Geld zurück; er darf niemals eine Forderung sein.
        -- Ohne diesen Riegel ließe sich die (negative) Anrechnungsposition einer
        -- Schlussrechnung „korrigieren": invertiert wird daraus ein POSITIVER
        -- Gutschriftbetrag, der den Abschlag ein zweites Mal einfordert.
        IF NEW.gross_total IS NULL OR NEW.gross_total > 0 THEN
            RAISE EXCEPTION
                'Rechnung %: Gutschrift/Storno darf keinen positiven Betrag ausweisen (ist %)',
                NEW.id, NEW.gross_total;
        END IF;
        -- FOR UPDATE (statt nur lesen): serialisiert gegen eine parallel laufende
        -- Veröffentlichung einer Schlussrechnung, die genau diesen Beleg anrechnet.
        SELECT status INTO v_ref_status
        FROM invoicing.invoice WHERE id = NEW.reference_invoice_id FOR UPDATE;
        IF v_ref_status IS DISTINCT FROM 'VEROEFFENTLICHT' THEN
            RAISE EXCEPTION
                'Rechnung %: Gutschrift/Storno erfordert einen veröffentlichten Ursprungsbeleg (B-21)', NEW.id;
        END IF;
        -- Ein angerechneter Abschlag darf nicht storniert/gutgeschrieben werden,
        -- solange die anrechnende Schlussrechnung veröffentlicht ist: sonst stünde
        -- ein unveränderlicher Beleg da, der einen Abzug für eine Rechnung
        -- ausweist, die es nicht mehr gibt. Korrektur läuft über eine Gutschrift
        -- ZUR SCHLUSSRECHNUNG.
        SELECT f.invoice_number INTO v_final_number
        FROM invoicing.invoice f
        WHERE f.id = invoicing.advance_blocking_final(NEW.reference_invoice_id);
        IF v_final_number IS NOT NULL THEN
            RAISE EXCEPTION
                'Rechnung %: der Ursprungsbeleg ist in der Schlussrechnung % angerechnet und kann nicht storniert/gutgeschrieben werden; korrigieren Sie die Schlussrechnung',
                NEW.id, v_final_number;
        END IF;
        SELECT count(*) INTO v_bad_debtors
        FROM invoicing.invoice_party ip
        WHERE ip.invoice_id = NEW.id AND ip.role = 'INVOICE_DEBTOR'
          AND NOT EXISTS (
              SELECT 1 FROM invoicing.invoice_party rp
              WHERE rp.invoice_id = NEW.reference_invoice_id
                AND rp.role = 'INVOICE_DEBTOR' AND rp.party_id = ip.party_id);
        IF v_bad_debtors > 0 THEN
            RAISE EXCEPTION
                'Rechnung %: % Schuldner sind keine Schuldner des Ursprungsbelegs (A-27/B-21/P3-06)',
                NEW.id, v_bad_debtors;
        END IF;
    ELSE
        -- B-08: Rechnungen entstehen aus dem Auftrag.
        IF NEW.work_order_id IS NULL THEN
            RAISE EXCEPTION
                'Rechnung %: Rechnungsarten außer Gutschrift/Storno erfordern einen Auftrag (B-08)', NEW.id;
        END IF;
        SELECT status INTO v_order_status
        FROM workflow.work_order WHERE id = NEW.work_order_id FOR SHARE;

        IF NEW.invoice_type IN ('ABSCHLAGSRECHNUNG', 'TEILRECHNUNG') THEN
            -- Abschlag/Teilrechnung entstehen WÄHREND der Ausführung — ein
            -- kaufmännisch geprüfter Auftrag wäre hier ein Widerspruch. Aber ohne
            -- Freigabe gibt es keine beauftragte Leistung, für die man abschlagen
            -- könnte.
            IF v_order_status NOT IN ('FREIGEGEBEN', 'IN_PLANUNG', 'IN_AUSFUEHRUNG',
                                      'TECHNISCH_ABGESCHLOSSEN', 'KAUFMAENNISCH_GEPRUEFT',
                                      'ABGERECHNET') THEN
                RAISE EXCEPTION
                    'Rechnung %: Abschlags-/Teilrechnung erfordert einen freigegebenen Auftrag (B-08), ist %',
                    NEW.id, v_order_status;
            END IF;
        ELSIF v_order_status NOT IN ('KAUFMAENNISCH_GEPRUEFT', 'ABGERECHNET') THEN
            RAISE EXCEPTION
                'Rechnung %: Auftrag muss kaufmännisch geprüft sein (B-08), ist %', NEW.id, v_order_status;
        END IF;

        -- A-27: Rechnungsschuldner müssen als INVOICE_DEBTOR am Auftrag bestätigt sein
        SELECT count(*) INTO v_bad_debtors
        FROM invoicing.invoice_party ip
        WHERE ip.invoice_id = NEW.id AND ip.role = 'INVOICE_DEBTOR'
          AND NOT EXISTS (
              SELECT 1 FROM workflow.work_order_party wp
              WHERE wp.work_order_id = NEW.work_order_id
                AND wp.role = 'INVOICE_DEBTOR' AND wp.party_id = ip.party_id);
        IF v_bad_debtors > 0 THEN
            RAISE EXCEPTION
                'Rechnung %: % Schuldner sind nicht als Rechnungsschuldner des Auftrags bestätigt (A-27)',
                NEW.id, v_bad_debtors;
        END IF;

        IF NEW.invoice_type = 'SCHLUSSRECHNUNG' THEN
            -- Jeden angerechneten Abschlag EXKLUSIV sperren (deterministische
            -- Reihenfolge gegen Deadlocks) und seinen Zustand endgültig prüfen.
            -- Erst diese Sperre macht die Doppelanrechnung physisch unmöglich:
            -- zwei parallele Veröffentlichungen serialisieren hier, die zweite
            -- sieht die veröffentlichte Schlussrechnung der ersten.
            FOR v_adv IN
                SELECT DISTINCT advance_invoice_id
                FROM invoicing.invoice_advance
                WHERE final_invoice_id = NEW.id
                ORDER BY advance_invoice_id
            LOOP
                SELECT status INTO v_adv_status
                FROM invoicing.invoice WHERE id = v_adv.advance_invoice_id FOR UPDATE;
                IF v_adv_status IS DISTINCT FROM 'VEROEFFENTLICHT' THEN
                    RAISE EXCEPTION
                        'Schlussrechnung %: der angerechnete Abschlag % ist nicht veröffentlicht',
                        NEW.id, v_adv.advance_invoice_id;
                END IF;
                IF EXISTS (
                    SELECT 1 FROM invoicing.invoice c
                    WHERE c.reference_invoice_id = v_adv.advance_invoice_id
                      AND c.invoice_type IN ('STORNO', 'GUTSCHRIFT')
                      AND c.status = 'VEROEFFENTLICHT'
                ) THEN
                    RAISE EXCEPTION
                        'Schlussrechnung %: der angerechnete Abschlag % ist storniert oder gutgeschrieben',
                        NEW.id, v_adv.advance_invoice_id;
                END IF;
                IF invoicing.advance_blocking_final(v_adv.advance_invoice_id, NEW.id)
                   IS NOT NULL THEN
                    RAISE EXCEPTION
                        'Schlussrechnung %: der Abschlag % ist bereits in einer anderen veröffentlichten Schlussrechnung angerechnet',
                        NEW.id, v_adv.advance_invoice_id;
                END IF;
            END LOOP;

            -- Verkettung und Positionen müssen deckungsgleich sein (je Abschlag
            -- und Steuersatz derselbe Betrag). Ohne diese Prüfung könnte eine SR
            -- eine Anrechnung führen, die im Beleg gar nicht abgezogen ist (oder
            -- umgekehrt) — der Kunde zahlte doppelt oder gar nicht.
            IF EXISTS (
                (SELECT advance_invoice_id, tax_code, tax_rate_percent, -sum(net_amount)
                   FROM invoicing.invoice_line
                  WHERE invoice_id = NEW.id AND advance_invoice_id IS NOT NULL
                  GROUP BY 1, 2, 3
                 EXCEPT
                 SELECT advance_invoice_id, tax_code, tax_rate_percent, net_amount
                   FROM invoicing.invoice_advance WHERE final_invoice_id = NEW.id)
                UNION ALL
                (SELECT advance_invoice_id, tax_code, tax_rate_percent, net_amount
                   FROM invoicing.invoice_advance WHERE final_invoice_id = NEW.id
                 EXCEPT
                 SELECT advance_invoice_id, tax_code, tax_rate_percent, -sum(net_amount)
                   FROM invoicing.invoice_line
                  WHERE invoice_id = NEW.id AND advance_invoice_id IS NOT NULL
                  GROUP BY 1, 2, 3)
            ) THEN
                RAISE EXCEPTION
                    'Schlussrechnung %: die Anrechnungspositionen stimmen nicht mit der Anrechnung überein',
                    NEW.id;
            END IF;

            -- Keine VERGESSENE Anrechnung: gibt es zu diesem Auftrag noch
            -- veröffentlichte, nicht stornierte/gutgeschriebene und nicht
            -- anderweitig gebundene Abschläge, muss die Schlussrechnung sie
            -- anrechnen. Sonst fordert sie die volle Leistung ein zweites Mal —
            -- auf einem danach unveränderlichen Beleg (der teuerste Bedienfehler
            -- der Domäne).
            SELECT count(*) INTO v_offene_abschlaege
            FROM invoicing.invoice a
            WHERE a.work_order_id = NEW.work_order_id
              AND a.invoice_type IN ('ABSCHLAGSRECHNUNG', 'TEILRECHNUNG')
              AND a.status = 'VEROEFFENTLICHT'
              -- Ein Abschlag über 0,00 EUR trägt nichts zum Anrechnen bei (jede
              -- Anrechnungszeile braucht einen positiven Betrag, CHECK
              -- net_amount > 0). Er darf deshalb auch nicht blockieren — sonst
              -- wäre die Schlussrechnung in einer Sackgasse: weder anrechenbar
              -- noch übergehbar. Dieselbe Bedingung filtert der Service
              -- (`anrechenbare_abschlaege`), damit beide dieselbe Menge meinen.
              AND a.gross_total > 0
              AND NOT EXISTS (
                  SELECT 1 FROM invoicing.invoice c
                  WHERE c.reference_invoice_id = a.id
                    AND c.invoice_type IN ('STORNO', 'GUTSCHRIFT')
                    AND c.status = 'VEROEFFENTLICHT')
              AND NOT EXISTS (
                  SELECT 1 FROM invoicing.invoice_advance ia
                  WHERE ia.final_invoice_id = NEW.id
                    AND ia.advance_invoice_id = a.id)
              AND invoicing.advance_blocking_final(a.id, NEW.id) IS NULL;
            IF v_offene_abschlaege > 0 THEN
                RAISE EXCEPTION
                    'Schlussrechnung %: % anrechenbare Abschlags-/Teilrechnung(en) dieses Auftrags sind nicht angerechnet',
                    NEW.id, v_offene_abschlaege;
            END IF;

            -- Eine Schlussrechnung fordert Geld. Übersteigt die Anrechnung die
            -- Leistung, ist das keine Rechnung, sondern eine Erstattung — dafür
            -- gibt es die Gutschrift.
            IF NEW.gross_total < 0 THEN
                RAISE EXCEPTION
                    'Schlussrechnung %: die Anrechnung übersteigt die Leistung (Zahlbetrag %); dafür ist eine Gutschrift zu stellen',
                    NEW.id, NEW.gross_total;
            END IF;
        END IF;
    END IF;

    RETURN NULL;
END;
$$;
"""

REVERSE_SQL = r"""
DROP TRIGGER trg_invoice_advance_protect ON invoicing.invoice_advance;
DROP TRIGGER trg_invoice_advance_check ON invoicing.invoice_advance;
DROP FUNCTION invoicing.protect_invoice_advance();
DROP FUNCTION invoicing.check_invoice_advance();
DROP TABLE invoicing.invoice_advance;
DROP FUNCTION invoicing.advance_blocking_final(uuid, uuid);

DROP INDEX invoicing.uq_invoice_line_advance;
ALTER TABLE invoicing.invoice_line
    DROP CONSTRAINT invoice_line_advance_is_deduction,
    DROP COLUMN advance_invoice_id;

-- Veröffentlichungstor auf den Stand vor dieser Migration (0019) zurücksetzen.
CREATE OR REPLACE FUNCTION invoicing.check_invoice_publish() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_debtors            integer;
    v_debtors_no_basis   integer;
    v_primary_recipients integer;
    v_order_status       text;
    v_bad_debtors        integer;
    v_ref_status         text;
BEGIN
    IF NEW.status <> 'VEROEFFENTLICHT' THEN
        RETURN NULL;
    END IF;

    PERFORM invoicing.assert_invoice_totals(NEW.id);

    SELECT count(*) FILTER (WHERE role = 'INVOICE_DEBTOR'),
           count(*) FILTER (WHERE role = 'INVOICE_DEBTOR' AND liability_basis IS NULL),
           count(*) FILTER (WHERE role = 'INVOICE_RECIPIENT' AND is_primary)
    INTO v_debtors, v_debtors_no_basis, v_primary_recipients
    FROM invoicing.invoice_party WHERE invoice_id = NEW.id;

    IF v_debtors < 1 THEN
        RAISE EXCEPTION
            'Rechnung %: Veröffentlichung ohne Rechnungsschuldner ist unzulässig (A-27)', NEW.id;
    END IF;
    IF v_debtors > 1 AND v_debtors_no_basis > 0 THEN
        RAISE EXCEPTION
            'Rechnung %: Mehrere Schuldner erfordern eine dokumentierte Grundlage je Schuldner (A-29); Standard sind getrennte Rechnungen (A-24)',
            NEW.id;
    END IF;
    IF v_primary_recipients <> 1 THEN
        RAISE EXCEPTION
            'Rechnung %: Genau ein primärer Rechnungsempfänger ist erforderlich (A-28)', NEW.id;
    END IF;

    IF NEW.invoice_type IN ('GUTSCHRIFT', 'STORNO') THEN
        SELECT status INTO v_ref_status
        FROM invoicing.invoice WHERE id = NEW.reference_invoice_id;
        IF v_ref_status IS DISTINCT FROM 'VEROEFFENTLICHT' THEN
            RAISE EXCEPTION
                'Rechnung %: Gutschrift/Storno erfordert einen veröffentlichten Ursprungsbeleg (B-21)', NEW.id;
        END IF;
        SELECT count(*) INTO v_bad_debtors
        FROM invoicing.invoice_party ip
        WHERE ip.invoice_id = NEW.id AND ip.role = 'INVOICE_DEBTOR'
          AND NOT EXISTS (
              SELECT 1 FROM invoicing.invoice_party rp
              WHERE rp.invoice_id = NEW.reference_invoice_id
                AND rp.role = 'INVOICE_DEBTOR' AND rp.party_id = ip.party_id);
        IF v_bad_debtors > 0 THEN
            RAISE EXCEPTION
                'Rechnung %: % Schuldner sind keine Schuldner des Ursprungsbelegs (A-27/B-21/P3-06)',
                NEW.id, v_bad_debtors;
        END IF;
    ELSE
        IF NEW.work_order_id IS NULL THEN
            RAISE EXCEPTION
                'Rechnung %: Rechnungsarten außer Gutschrift/Storno erfordern einen Auftrag (B-08)', NEW.id;
        END IF;
        SELECT status INTO v_order_status
        FROM workflow.work_order WHERE id = NEW.work_order_id FOR SHARE;
        IF v_order_status NOT IN ('KAUFMAENNISCH_GEPRUEFT', 'ABGERECHNET') THEN
            RAISE EXCEPTION
                'Rechnung %: Auftrag muss kaufmännisch geprüft sein (B-08), ist %', NEW.id, v_order_status;
        END IF;
        SELECT count(*) INTO v_bad_debtors
        FROM invoicing.invoice_party ip
        WHERE ip.invoice_id = NEW.id AND ip.role = 'INVOICE_DEBTOR'
          AND NOT EXISTS (
              SELECT 1 FROM workflow.work_order_party wp
              WHERE wp.work_order_id = NEW.work_order_id
                AND wp.role = 'INVOICE_DEBTOR' AND wp.party_id = ip.party_id);
        IF v_bad_debtors > 0 THEN
            RAISE EXCEPTION
                'Rechnung %: % Schuldner sind nicht als Rechnungsschuldner des Auftrags bestätigt (A-27)',
                NEW.id, v_bad_debtors;
        END IF;
    END IF;

    RETURN NULL;
END;
$$;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0059_erechnung_ausfertigung"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
