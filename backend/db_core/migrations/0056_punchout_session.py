"""IDS-Connect Punchout-Session (pricing.punchout_session) — Shop-Roundtrip.

Der HTTP-Roundtrip des itek-2.5-Warenkorbverfahrens: MCN öffnet den Händler-Shop
per Punchout-Formular und übergibt eine **hookurl**, an die der Shop den fertigen
Warenkorb zurück-POSTet. Damit dieser Rückruf (ein separater, unauthentifizierter
Request aus dem Browser des Handwerkers/vom Shop) sicher der auslösenden Aktion
zugeordnet werden kann, hält diese Tabelle eine kurzlebige Session mit einem
**Einmal-Token**.

Grundsatzentscheidungen:

1. **Token nur als Hash.** In der hookurl steht ein zufälliges Bearer-Token
   (`secrets.token_urlsafe`); in der DB liegt ausschließlich dessen SHA-256-Hash
   (`token_hash`, UNIQUE). Ein DB-Leak gibt damit keine nutzbaren Rückgabe-URLs
   preis — dasselbe Prinzip wie bei Passwort-Hashes. Der Rückgabe-Endpunkt hasht
   das eingehende Token und schlägt darüber nach.

2. **Statusautomat OFFEN → EINGELOEST.** Eine Session wird genau einmal eingelöst
   (der Shop liefert den Warenkorb). Die Kohärenz „eingelöst ⇒ Warenkorb-XML +
   Zeitpunkt vorhanden" erzwingt ein CHECK. Abgelaufene Sessions (`expires_at`)
   werden im Service abgewiesen (kein Cron nötig; die Prüfung ist zustandslos).

3. **Kontext, keine Automatik.** `quote_id` merkt sich nur, aus welchem Angebot der
   Punchout gestartet wurde (Audit/UX). Das Übernehmen der zurückgegebenen
   Positionen in den Beleg geschieht bewusst NICHT hier automatisch, sondern über
   den regulären Angebots-Editor (der Server prüft die Positionen wie bei jeder
   anderen Bearbeitung).

Schutzstandard wie bei jeder neuen Fachtabelle (updated_at/Audit/No-Delete/
No-Truncate/REVOKE); zusätzlich friert `protect_punchout_session()` die
identitätsstiftenden Felder (Token/Anbindung/Aktion/Ersteller) ein.
"""
from django.db import migrations

FORWARD_SQL = r"""
CREATE TABLE pricing.punchout_session (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    connection_id      uuid NOT NULL REFERENCES pricing.supplier_connection (id),
    quote_id           uuid NULL REFERENCES invoicing.quote (id),
    token_hash         text NOT NULL UNIQUE CHECK (btrim(token_hash) <> ''),
    action             text NOT NULL CHECK (action IN ('WKE', 'WKS')),
    status             text NOT NULL DEFAULT 'OFFEN'
                       CHECK (status IN ('OFFEN', 'EINGELOEST')),
    returned_cart_xml  text NULL,
    created_by         uuid NOT NULL REFERENCES security.app_user (id),
    expires_at         timestamptz NOT NULL,
    redeemed_at        timestamptz NULL,
    -- Kohärenz: eingelöst ⇒ Warenkorb + Zeitpunkt vollständig
    CONSTRAINT punchout_session_redeemed_coherent CHECK (
        status <> 'EINGELOEST'
        OR (returned_cart_xml IS NOT NULL AND redeemed_at IS NOT NULL)
    ),
    version            integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_punchout_session_connection ON pricing.punchout_session (connection_id);
CREATE INDEX idx_punchout_session_quote ON pricing.punchout_session (quote_id);

CREATE TRIGGER trg_punchout_session_updated_at
    BEFORE UPDATE ON pricing.punchout_session
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_punchout_session_audit
    AFTER UPDATE ON pricing.punchout_session
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_punchout_session_no_delete
    BEFORE DELETE ON pricing.punchout_session
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_punchout_session_no_truncate
    BEFORE TRUNCATE ON pricing.punchout_session
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON pricing.punchout_session FROM PUBLIC;

-- Die identitätsstiftenden Felder sind nach der Anlage unveränderlich; eine
-- eingelöste Session darf nicht wieder auf OFFEN zurückfallen (Replay-Schutz).
CREATE FUNCTION pricing.protect_punchout_session() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.token_hash IS DISTINCT FROM OLD.token_hash
       OR NEW.connection_id IS DISTINCT FROM OLD.connection_id
       OR NEW.action IS DISTINCT FROM OLD.action
       OR NEW.created_by IS DISTINCT FROM OLD.created_by
       OR NEW.expires_at IS DISTINCT FROM OLD.expires_at THEN
        RAISE EXCEPTION
            'punchout_session %: Token/Anbindung/Aktion/Ersteller sind unveränderlich',
            OLD.id;
    END IF;
    IF OLD.status = 'EINGELOEST' AND NEW.status <> 'EINGELOEST' THEN
        RAISE EXCEPTION
            'punchout_session %: eine eingelöste Session ist endgültig', OLD.id;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_punchout_session_protect
    BEFORE UPDATE ON pricing.punchout_session
    FOR EACH ROW EXECUTE FUNCTION pricing.protect_punchout_session();
"""

REVERSE_SQL = r"""
DROP FUNCTION IF EXISTS pricing.protect_punchout_session() CASCADE;
DROP TABLE IF EXISTS pricing.punchout_session;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0055_sitereport"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
