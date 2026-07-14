"""Abrechnungsbindung (invoicing.billing_link) + Abrechnungsart am Auftrag.

Aus **Angebot** und/oder **Baustellenbericht + erfassten Zeiten** entsteht eine
Rechnung. Die eine Frage, die dabei physisch beantwortet sein muss:

    Kann dieselbe Leistung ein zweites Mal abgerechnet werden?

Antwort: **nein** — und zwar nicht, weil der Service aufpasst, sondern weil die
Datenbank es nicht zulässt. Dieselbe Haltung wie bei den Fälligkeiten
(Migration 0071): *Idempotenz ist eine physische Eigenschaft, keine Absprache.*

## invoicing.billing_link — die Bindung

`billing_link` ist **kein Beleg**. Sie ist eine interne Verknüpfung mit genau
einer Aussage:

    „Diese Berichtsposition / diese Zeitbuchung / diese Angebotsposition
     ist in DIESER Rechnungsposition abgerechnet."

Genau **eine** Quellspalte ist gesetzt (`num_nonnulls(...) = 1`), und sie passt
zur `source_kind` (CHECK) — sonst stünde „ZEITBUCHUNG" an einer Berichtsposition.

## Die Doppelabrechnungssperre: drei partielle UNIQUE-Indizes

    (site_report_line_id) WHERE released_at IS NULL
    (time_entry_id)       WHERE released_at IS NULL
    (quote_line_id)       WHERE released_at IS NULL

**Warum nicht einfach ein UNIQUE über die Rechnungsposition?** Weil ein
**Storno** dann in eine Sackgasse führte: Nach dem Storno müssen dieselben
Stunden wieder abrechenbar sein — die Rechnungsposition ist aber nach dem
Veröffentlichen unveränderlich (`invoicing.protect_invoice_children`, B-21) und
lässt sich nicht nachträglich entwerten. Es gibt keinen Weg, die Sperre am Beleg
selbst zu lösen.

Die Bindung ist die Lösung: Sie liegt **neben** dem Beleg. Der Storno **löst
sie** (`released_at`, Trigger `invoicing.release_billing_links_on_cancel`), die
Quelle wird wieder frei, und der partielle Index greift nur auf die **aktiven**
Bindungen. Der Beleg bleibt dabei unangetastet — GoBD ist gewahrt.

`released_reason` ist Pflicht, sobald `released_at` gesetzt ist (CHECK): eine
gelöste Bindung ohne Grund wäre ein stilles Wegwischen der Sperre.

## Die Grenze: STORNO löst, GUTSCHRIFT nicht

Nur der **Vollstorno** (`invoice_type = 'STORNO'`) löst die Bindungen. Eine
**GUTSCHRIFT ist eine Teilkorrektur** (`beleg.create_correction` verlangt
ausdrücklich mindestens eine Position) — die Ursprungsrechnung bleibt bestehen
und fordert weiterhin Geld. Ihre Leistung ist damit weiterhin abgerechnet.

Das ist **exakt die Grenze, die das Repo an derselben Stelle schon zieht**: Ein
angerechneter Abschlag wird ausschließlich durch das **Storno** der
Schlussrechnung wieder frei (`beleg._gebundene_abschlaege` filtert auf
`_stornierte_belege()`, nicht auf `_korrigierte_belege()`; die DB-Regel dazu ist
`invoicing.advance_blocking_final`). Eine zweite, abweichende Auffassung von
„aufgehoben" im selben Modul wäre eine Fehlerquelle.

Auch eine Gutschrift, die zufällig **alle** Positionen umfasst, löst nichts: Sie
ist kein Storno, sie hebt den Beleg nicht auf, und die Summe mehrerer
Teilgutschriften ist keine Aufhebung. Wer die Leistung wieder abrechenbar machen
will, storniert.

## Der Positionssatz einer gebundenen Rechnung ist fest

`invoicing.protect_billed_invoice_lines`: Solange eine Rechnung **aktive**
Bindungen trägt, sind ihre Positionen gesperrt (INSERT/UPDATE/DELETE).

Ohne diesen Trigger hätte der Beleg-Editor (`beleg.update_invoice` ersetzt den
Positionssatz per Delete+Insert) zwei Wege, beide falsch:

* Mit `ON DELETE RESTRICT` liefe er in eine Fremdschlüsselverletzung — ein 500.
* Mit `ON DELETE CASCADE` verschwänden die Bindungen **stillschweigend**: Ein
  Klick auf „Speichern" hätte die Doppelabrechnungssperre gelöscht, und niemand
  hätte es gemerkt. Genau das darf nicht passieren.

Also: klarer Fachfehler (422). Der Weg aus einem verunglückten Entwurf ist
`abrechnung.bindungen_loesen` — es löst die Bindungen **und entfernt die
gebundenen Positionen aus dem Entwurf** (Reihenfolge im Service: erst lösen,
dann löschen). Die Quellen werden dadurch wieder frei, ohne dass der Entwurf sie
weiter in Rechnung stellte — die Sperre bleibt also lückenlos.

Deshalb ist `invoice_line_id` **nullable**: Eine **aktive** Bindung nennt immer
ihre Position (CHECK `billing_link_aktive_bindung_hat_position`); eine gelöste
darf sie verloren haben (Entwurf verworfen). Beim Storno bleibt sie stehen — die
veröffentlichte Position existiert ja weiter, und die Kette bleibt nachvollziehbar.

## Schutzstandard — mit begründeter Wahl

`updated_at`, `audit_row_update`, **no_delete**, no_truncate, REVOKE.

Das No-Delete ist hier die **richtige** Wahl (anders als bei `quote_line` /
`site_report_line`, wo der Positionssatz als Ganzes ersetzt wird): Die Bindung
ist der Nachweis, dass eine Leistung abgerechnet wurde. Ein DELETE machte die
Sperre spurlos rückgängig — genau das, wovor sie schützt. Aufgehoben wird sie
ausschließlich über `released_at` (mit Grund, mit Zeitstempel, im Audit).

## workflow.work_order.billing_mode — PAUSCHAL (Default) | REGIE

* **PAUSCHAL**: Die Rechnung ist die **Angebotskopie**. Erfasste Zeiten und
  Berichtspositionen sind **Nachweis**, kein Rechnungsposten — das Angebot
  enthält die Leistung bereits. Würde man beides fakturieren, kassierte man
  doppelt. Das Soll-Ist (0080) bleibt die **interne Nachkalkulation**.
* **REGIE**: Die Rechnung entsteht aus **Bericht + Zeiten** (dem Ist).

Default PAUSCHAL (Entscheidung des Users) — und der sichere Default: Er lässt
`rechnung_aus_auftrag` scheitern, statt einen Bestandsauftrag stillschweigend
ein zweites Mal abzurechnen.
"""
from django.db import migrations

FORWARD_SQL = r"""
-- ---------------------------------------------------------------------------
-- workflow.work_order.billing_mode
-- ---------------------------------------------------------------------------
ALTER TABLE workflow.work_order
    ADD COLUMN billing_mode text NOT NULL DEFAULT 'PAUSCHAL'
    CONSTRAINT work_order_billing_mode CHECK (billing_mode IN ('PAUSCHAL', 'REGIE'));

COMMENT ON COLUMN workflow.work_order.billing_mode IS
    'PAUSCHAL: die Rechnung ist die Angebotskopie; Zeiten/Berichtspositionen sind '
    'Nachweis, kein Rechnungsposten (sonst wird doppelt kassiert). '
    'REGIE: die Rechnung entsteht aus Bericht + Zeiten.';

-- ---------------------------------------------------------------------------
-- invoicing.billing_link — die Abrechnungsbindung
-- ---------------------------------------------------------------------------
CREATE TABLE invoicing.billing_link (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id          uuid NOT NULL REFERENCES invoicing.invoice (id),
    -- Nullable mit Bedacht: die AKTIVE Bindung nennt immer ihre Position (CHECK
    -- unten); eine gelöste darf sie verloren haben, wenn der Entwurf, der sie
    -- trug, verworfen wurde (abrechnung.bindungen_loesen).
    invoice_line_id     uuid NULL REFERENCES invoicing.invoice_line (id),
    source_kind         text NOT NULL CHECK (source_kind IN
                        ('BERICHTSPOSITION', 'ZEITBUCHUNG', 'ANGEBOTSPOSITION')),
    site_report_line_id uuid NULL REFERENCES workflow.site_report_line (id),
    time_entry_id       uuid NULL REFERENCES workflow.time_entry (id),
    quote_line_id       uuid NULL REFERENCES invoicing.quote_line (id),
    -- Gelöst (Storno / verworfener Entwurf). NULL = aktiv = die Quelle ist belegt.
    released_at         timestamptz NULL,
    released_reason     text NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT billing_link_eine_quelle CHECK (
        num_nonnulls(site_report_line_id, time_entry_id, quote_line_id) = 1
    ),
    -- Die Art muss zur gesetzten Quellspalte passen — sonst behauptete die Zeile
    -- etwas anderes, als sie referenziert.
    CONSTRAINT billing_link_quelle_passt_zur_art CHECK (
        (source_kind = 'BERICHTSPOSITION' AND site_report_line_id IS NOT NULL)
     OR (source_kind = 'ZEITBUCHUNG'      AND time_entry_id       IS NOT NULL)
     OR (source_kind = 'ANGEBOTSPOSITION' AND quote_line_id       IS NOT NULL)
    ),
    -- Kein stilles Lösen: wer eine Bindung aufhebt, sagt warum.
    CONSTRAINT billing_link_freigabe_begruendet CHECK (
        (released_at IS NULL)
        = (released_reason IS NULL OR btrim(released_reason) = '')
    ),
    -- Eine AKTIVE Bindung nennt die Rechnungsposition, in der sie abgerechnet ist.
    CONSTRAINT billing_link_aktive_bindung_hat_position CHECK (
        released_at IS NOT NULL OR invoice_line_id IS NOT NULL
    )
);

-- DIE DOPPELABRECHNUNGSSPERRE (Kern dieses Slices): je Quelle höchstens EINE
-- aktive Bindung. Die Garantie liegt damit in der DATENBANK, nicht im Service —
-- zwei parallele Rechnungsläufe können dieselbe Zeitbuchung nicht beide greifen.
CREATE UNIQUE INDEX uq_billing_link_site_report_line
    ON invoicing.billing_link (site_report_line_id)
    WHERE site_report_line_id IS NOT NULL AND released_at IS NULL;
CREATE UNIQUE INDEX uq_billing_link_time_entry
    ON invoicing.billing_link (time_entry_id)
    WHERE time_entry_id IS NOT NULL AND released_at IS NULL;
CREATE UNIQUE INDEX uq_billing_link_quote_line
    ON invoicing.billing_link (quote_line_id)
    WHERE quote_line_id IS NOT NULL AND released_at IS NULL;

CREATE INDEX idx_billing_link_invoice ON invoicing.billing_link (invoice_id);
CREATE INDEX idx_billing_link_invoice_line
    ON invoicing.billing_link (invoice_line_id);
-- Der heiße Pfad: „trägt diese Rechnung noch aktive Bindungen?"
CREATE INDEX idx_billing_link_invoice_aktiv ON invoicing.billing_link (invoice_id)
    WHERE released_at IS NULL;

CREATE TRIGGER trg_billing_link_updated_at
    BEFORE UPDATE ON invoicing.billing_link
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_billing_link_audit
    AFTER UPDATE ON invoicing.billing_link
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
-- No-Delete ist hier RICHTIG (siehe Modulkopf): Ein DELETE machte die
-- Doppelabrechnungssperre spurlos rückgängig. Gelöst wird über released_at.
CREATE TRIGGER trg_billing_link_no_delete
    BEFORE DELETE ON invoicing.billing_link
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_billing_link_no_truncate
    BEFORE TRUNCATE ON invoicing.billing_link
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON invoicing.billing_link FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- Der Storno löst die Bindungen — DAS ist der Grund für dieses Design.
--
-- Eine veröffentlichte Rechnungsposition ist unveränderlich (B-21). Ohne diese
-- Freigabe wären die stornierten Stunden für immer verbrannt: nie wieder
-- abrechenbar, obwohl der Beleg, der sie abgerechnet hat, aufgehoben ist.
--
-- NUR der STORNO (Vollstorno). Die GUTSCHRIFT ist eine Teilkorrektur; die
-- Ursprungsrechnung besteht weiter und fordert weiterhin Geld — ihre Leistung
-- ist also weiterhin abgerechnet. Dieselbe Grenze zieht das Modul bereits bei
-- den Abschlägen (invoicing.advance_blocking_final).
-- ---------------------------------------------------------------------------
CREATE FUNCTION invoicing.release_billing_links_on_cancel() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.invoice_type = 'STORNO'
       AND NEW.status = 'VEROEFFENTLICHT'
       AND OLD.status IS DISTINCT FROM 'VEROEFFENTLICHT'
       AND NEW.reference_invoice_id IS NOT NULL THEN
        UPDATE invoicing.billing_link
           SET released_at = now(),
               released_reason =
                   'Storno ' || coalesce(NEW.invoice_number, NEW.id::text)
         WHERE invoice_id = NEW.reference_invoice_id
           AND released_at IS NULL;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_invoice_release_billing_links
    AFTER UPDATE OF status ON invoicing.invoice
    FOR EACH ROW EXECUTE FUNCTION invoicing.release_billing_links_on_cancel();

-- ---------------------------------------------------------------------------
-- Der Positionssatz einer gebundenen Rechnung ist fest.
--
-- Der Beleg-Editor ersetzt Positionen per Delete+Insert. Träfe er eine gebundene
-- Rechnung, gäbe es nur schlechte Ausgänge: Fremdschlüsselverletzung (500) oder —
-- mit CASCADE — das STILLE Verschwinden der Doppelabrechnungssperre. Also: klarer
-- Fachfehler.
--
-- Der Weg aus einem verunglückten Entwurf ist abrechnung.bindungen_loesen: es
-- löst erst die Bindungen (released_at, invoice_line_id := NULL) und entfernt
-- danach die gebundenen Positionen. In dem Moment trägt die Rechnung keine
-- aktive Bindung mehr — dieser Trigger lässt das DELETE also durch, und der
-- Entwurf stellt die freigegebenen Quellen auch nicht weiter in Rechnung.
-- ---------------------------------------------------------------------------
CREATE FUNCTION invoicing.protect_billed_invoice_lines() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_invoice uuid := CASE WHEN TG_OP = 'DELETE'
                           THEN OLD.invoice_id ELSE NEW.invoice_id END;
BEGIN
    IF EXISTS (
        SELECT 1 FROM invoicing.billing_link
         WHERE invoice_id = v_invoice AND released_at IS NULL
    ) THEN
        RAISE EXCEPTION
            'Rechnung %: Die Positionen sind an Berichtspositionen, Zeitbuchungen '
            'oder Angebotspositionen gebunden (Doppelabrechnungssperre) und können '
            'nicht geändert werden. Bindungen lösen oder Beleg stornieren.',
            v_invoice
            USING ERRCODE = 'raise_exception';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;

CREATE TRIGGER trg_invoice_line_billed
    BEFORE INSERT OR UPDATE OR DELETE ON invoicing.invoice_line
    FOR EACH ROW EXECUTE FUNCTION invoicing.protect_billed_invoice_lines();
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS trg_invoice_line_billed ON invoicing.invoice_line;
DROP FUNCTION IF EXISTS invoicing.protect_billed_invoice_lines();
DROP TRIGGER IF EXISTS trg_invoice_release_billing_links ON invoicing.invoice;
DROP FUNCTION IF EXISTS invoicing.release_billing_links_on_cancel();
DROP TABLE IF EXISTS invoicing.billing_link;
ALTER TABLE workflow.work_order DROP COLUMN IF EXISTS billing_mode;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0083_herkunftstreue_berichtsposition"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
