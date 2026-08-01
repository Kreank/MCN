"""Öffentliche Links (`security.public_link`) + der Systemakteur, der sie ausführt.

**Warum EINE generische Tabelle und nicht je Verbraucher eine eigene.**
Ein öffentlicher Link ist immer dasselbe Ding: ein Bearer-Geheimnis, das ohne
Anmeldung genau eine Sache an genau einem Objekt erlaubt. Die Mechanik — nur den
SHA-256-Hash speichern, Ablauf prüfen, Widerruf, Einmal-Einlösung, Drosselung —
ist für „Angebot freigeben", „Termin buchen" und jeden künftigen Fall Zeichen für
Zeichen identisch. Genau solche Mechanik dupliziert man nicht: die zweite Kopie
ist die, in der die Ablaufprüfung fehlt. Verbraucherspezifisch ist allein das
**Ziel** — und das trägt die Zeile als weiche Referenz (`target_type` +
`target_id`, kein harter FK), dasselbe Zugeständnis wie `notify.notification`
(0137) und `audit.domain_event` (0008): ein harter FK ginge auf genau eine
Tabelle und stünde bei der zweiten Link-Art sofort im Weg.

`purpose` ist ein **geschlossenes** Vokabular. Jede weitere Link-Art kostet eine
Migration — bewusst, wie bei `notification.kind`: eine offene Textspalte hätte
binnen weniger Slices vier Schreibweisen derselben Art, und keine Auswertung
könnte noch sagen, was ein Link eigentlich darf.

**Schema `security`, nicht `invoicing`.** Die Zeile ist kein Beleginhalt, sie ist
ein Zugangsmittel — dieselbe Familie wie `security.device_token` (0114) und
`security.login_throttle` (0116). In `invoicing` gelegt hätte sie den nächsten
Aufrufer aus `workflow` zu einer zweiten Tabelle verleitet.

**Nur der Hash.** `token_hash` trägt einen CHECK auf `^[0-9a-f]{64}$` — dass hier
niemals Klartext landet, ist damit nicht Konvention, sondern physisch geprüft.
Der Klartext existiert genau einmal: in der Antwort auf „Link erzeugen".

**Der Guard-Trigger friert alles ein außer dem Lebenslauf.** Änderbar sind nur
`used_at`, `revoked_at`, `use_count` und `version`; `revoked_at` ist eine
Einbahnstraße (einmal gesetzt, nie zurück), `used_at` darf nur vorwärts laufen
und nie wieder NULL werden, `use_count` wächst nur. Ohne diese Sperre könnte ein
Link nach dem Widerruf sein Ziel wechseln oder eine Einlösung zurückgenommen
werden — ein Replay-Schutz, den man zurücksetzen kann, ist keiner.

**`single_use` — einmalig oder mehrfach, und zwar am Link.** Nicht jeder
anmeldefreie Weg ist nach der ersten Nutzung erledigt: Die Angebotsfreigabe ist
es (genau **eine** Erklärung, danach nur noch Anzeige), die als Nächstes
aufsetzende Kunden-Terminbuchung ist es ausdrücklich **nicht** — dort darf der
Kunde über denselben Link absagen und umbuchen. Ein Unterbau, dessen Token nach
dem ersten Klick tot ist, kann das strukturell nicht tragen.

Die Eigenschaft steht deshalb als Spalte an der Zeile und nicht im Ermessen des
Verbrauchers: So kann die **Datenbank** sie durchsetzen
(`CHECK (NOT single_use OR use_count <= 1)`) statt nur ein Service, und der
Guard friert sie mit ein — ein Link, der sich nachträglich von „einmalig" auf
„mehrfach" stellen ließe, wäre kein Einmal-Token. Welchen Wert ein Zweck
bekommt, leitet `oeffentlicher_link.link_erzeugen` zentral aus `purpose` ab
(fail-closed: unbekannter Zweck ⇒ einmalig); kein Aufrufer setzt ihn von Hand.

**Ein eingelöster Link bleibt lesbar, bis er abläuft.** Wer ihn eingelöst hat,
hat den Besitz bereits nachgewiesen — ihm den Ausgang zu verweigern, verrät
niemandem etwas und lässt nur den Kunden glauben, seine Zusage sei
fehlgeschlagen, weil ein Neuladen „ungültiger Link" zeigt. Ununterscheidbar
bleiben allein **unbekannt, abgelaufen und widerrufen**; das ist die Menge, in
der Raten überhaupt einen Erkenntnisgewinn hätte.

---

**Der Systemakteur `Online-Selbstbedienung`.**

Bisher gab es keinen. `wartung_faellige_ausloesen` und `ki_tool_queue_tick`
nahmen mangels Alternative „den ältesten aktiven Account" — der Audit-Trail führte
danach einen zufälligen Menschen als Urheber von Taten, die ein Automat begangen
hat. Das ist kein Schönheitsfehler: Ein Audit, das den Falschen nennt, ist
schlimmer als eins, das schweigt. Beide Kommandos fallen ab jetzt auf diesen
Akteur zurück (`--actor` bleibt als Übersteuerung).

**Er darf sich nicht anmelden — und das ist physisch abgesichert.** Angemeldet
wird sich ausschließlich über `accounts.User` (E-Mail + Passwort,
`accounts.backends.EmailBackend`); `security.app_user` trägt gar keine
Zugangsdaten. Ein app_user ohne zugehörige `accounts.User`-Zeile hat deshalb
schlicht keinen Anmeldeweg. Damit das so bleibt, bekommt `security.app_user` die
Spalte `is_system` und `public.accounts_user` einen Trigger, der ein Login-Konto
für einen Systemakteur zurückweist. Ein Service-Check wäre umgehbar; dieser
Trigger hält auch gegen den Django-Admin, gegen `createsuperuser` und gegen jede
künftige Anlagestelle.

**Seine Rolle trägt genau ein Recht.** `SYSTEM_SELBSTBEDIENUNG` darf
`invoicing/AENDERN` — das ist die Aktion, die die Online-Freigabe auslöst
(`beleg.set_quote_status`), und sonst nichts. Ehrlichkeitshinweis in beide
Richtungen: Die Rechtematrix wird laut 0026 in der App-Schicht durchgesetzt, die
DB erzwingt sie nicht. Die Rolle ist hier also eine **Deklaration** dessen, was
der Automat tun darf, kein Riegel — der Riegel ist, dass die öffentlichen
Endpunkte genau einen Service aufrufen und der Akteur sich nicht anmelden kann.

Zwei neue `notify.notification.kind`-Werte (`ANGEBOT_ANGENOMMEN`,
`ANGEBOT_ABGELEHNT`) — dass das eine Migration kostet, ist die in 0137
festgehaltene Absicht.
"""
from django.db import migrations

# Feste Kennung des Systemakteurs. Bewusst ein synthetisch aussehender Wert: Er
# steht als Konstante in `db_core/services/oeffentlicher_link.py` und darf sich
# nie ändern — er ist der Urheber in jedem Audit-Eintrag der Automaten.
SYSTEMAKTEUR_ID = "00000000-0000-4000-8000-000000000001"

FORWARD_SQL = r"""
-- ===========================================================================
-- security.app_user.is_system — Kennzeichen „technischer Akteur, kein Mensch"
-- ===========================================================================
ALTER TABLE security.app_user
    ADD COLUMN is_system boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN security.app_user.is_system IS
    'Technischer Akteur (Scheduler, Online-Selbstbedienung). Für solche Zeilen '
    'ist ein Login-Konto (accounts.User) physisch ausgeschlossen.';

-- ===========================================================================
-- Kein Anmeldeweg für Systemakteure — durchgesetzt an der Login-Tabelle
-- ===========================================================================
-- accounts_user ist Djangos eigene Tabelle (managed = True). Der Trigger ändert
-- an ihrer Struktur nichts und überlebt daher normale Django-Migrationen; er
-- greift bei JEDEM Schreiber — Admin, createsuperuser, Shell, Fixture.
CREATE FUNCTION security.forbid_login_for_system_actor() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.app_user_id IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM security.app_user
           WHERE id = NEW.app_user_id AND is_system
       ) THEN
        RAISE EXCEPTION
            'security.app_user %: technischer Akteur — für ihn ist kein '
            'Login-Konto zulässig', NEW.app_user_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_accounts_user_kein_systemakteur
    BEFORE INSERT OR UPDATE OF app_user_id ON public.accounts_user
    FOR EACH ROW EXECUTE FUNCTION security.forbid_login_for_system_actor();

-- ===========================================================================
-- security.public_link — ein Link, ein Ziel, eine erlaubte Handlung
-- ===========================================================================
CREATE TABLE security.public_link (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Geschlossenes Vokabular (siehe Kopf). Was der Link erlaubt, steht hier —
    -- nicht in der URL und nicht im Ermessen des Aufrufers.
    purpose       text NOT NULL CHECK (purpose IN ('ANGEBOT_FREIGABE')),
    -- Weiche Referenz aufs Ziel (kein FK, siehe Kopf), z. B. 'invoicing.quote'.
    target_type   text NOT NULL CHECK (btrim(target_type) <> ''),
    target_id     uuid NOT NULL,
    -- NUR der SHA-256-Hex-Hash. Der CHECK macht aus „wir speichern keinen
    -- Klartext" eine physische Zusicherung: 64 Zeichen aus [0-9a-f], sonst nichts.
    token_hash    text NOT NULL UNIQUE CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    expires_at    timestamptz NOT NULL,
    -- Darf dieser Link genau EINMAL eingelöst werden? Wird aus `purpose`
    -- abgeleitet (siehe Kopf), steht aber an der Zeile, damit der CHECK unten
    -- sie physisch durchsetzt. Der Guard friert sie ein.
    single_use    boolean NOT NULL DEFAULT true,
    -- Letzte Einlösung. Läuft nur vorwärts und wird nie wieder NULL.
    used_at       timestamptz NULL,
    -- Zurückgezogen. Einbahnstraße — stilllegen statt löschen, die Tabelle
    -- trägt ohnehin den No-Delete-Schutz.
    revoked_at    timestamptz NULL,
    -- Zahl der Einlösungen.
    use_count     integer NOT NULL DEFAULT 0 CHECK (use_count >= 0),
    created_by    uuid NOT NULL REFERENCES security.app_user (id),
    version       integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    -- „Einmalig" heißt einmalig — in der Datenbank, nicht im Service. Der
    -- CHECK greift bei INSERT UND UPDATE; der Guard-Trigger unten kann das
    -- nicht leisten, er sitzt nur auf UPDATE.
    CONSTRAINT public_link_einmalig_nur_einmal
        CHECK (NOT single_use OR use_count <= 1)
);

-- Die zweite Frage an diese Tabelle (die erste beantwortet der UNIQUE auf dem
-- Hash): „welche Links zeigen auf dieses Objekt?" — für die Liste im Leitstand.
CREATE INDEX idx_public_link_ziel
    ON security.public_link (target_type, target_id, created_at DESC);

-- Nach der Ausgabe ist nur noch der Lebenslauf beweglich.
CREATE FUNCTION security.guard_public_link() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.id          IS DISTINCT FROM OLD.id
       OR NEW.purpose     IS DISTINCT FROM OLD.purpose
       OR NEW.target_type IS DISTINCT FROM OLD.target_type
       OR NEW.target_id   IS DISTINCT FROM OLD.target_id
       OR NEW.token_hash  IS DISTINCT FROM OLD.token_hash
       OR NEW.expires_at  IS DISTINCT FROM OLD.expires_at
       OR NEW.single_use  IS DISTINCT FROM OLD.single_use
       OR NEW.created_by  IS DISTINCT FROM OLD.created_by
       OR NEW.created_at  IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION
            'public_link %: Zweck, Ziel, Token, Ablauf und Einmaligkeit sind '
            'unveränderlich', OLD.id;
    END IF;
    -- Eine Einlösung wird nicht zurückgenommen. Bei einem mehrfach nutzbaren
    -- Link darf der Zeitpunkt vorrücken (er nennt die LETZTE Nutzung) — zurück
    -- oder auf NULL nie. Ein zurücksetzbarer Replay-Schutz wäre keiner.
    IF OLD.used_at IS NOT NULL
       AND (NEW.used_at IS NULL OR NEW.used_at < OLD.used_at) THEN
        RAISE EXCEPTION
            'public_link %: eine Einlösung wird nicht zurückgenommen', OLD.id;
    END IF;
    IF OLD.revoked_at IS NOT NULL
       AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at THEN
        RAISE EXCEPTION
            'public_link %: ein Widerruf wird nicht zurückgenommen', OLD.id;
    END IF;
    IF NEW.use_count < OLD.use_count THEN
        RAISE EXCEPTION
            'public_link %: die Nutzungszählung läuft nicht rückwärts', OLD.id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_public_link_guard BEFORE UPDATE ON security.public_link
    FOR EACH ROW EXECUTE FUNCTION security.guard_public_link();
CREATE TRIGGER trg_public_link_updated_at BEFORE UPDATE ON security.public_link
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_public_link_audit AFTER UPDATE ON security.public_link
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_public_link_no_delete BEFORE DELETE ON security.public_link
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_public_link_no_truncate BEFORE TRUNCATE ON security.public_link
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON security.public_link FROM PUBLIC;

-- ===========================================================================
-- Der Systemakteur + seine Rolle
-- ===========================================================================
INSERT INTO security.app_user (id, display_name, status, is_system, version)
VALUES ('__SYSTEMAKTEUR__', 'Online-Selbstbedienung', 'ACTIVE', true, 1)
ON CONFLICT (id) DO NOTHING;

INSERT INTO security.role (code, label)
VALUES ('SYSTEM_SELBSTBEDIENUNG', 'System — Online-Selbstbedienung')
ON CONFLICT (code) DO NOTHING;

-- Genau ein Recht: der Statuswechsel, den die Online-Freigabe auslöst.
INSERT INTO security.role_permission (role_code, module, action, allowed, row_scope)
VALUES ('SYSTEM_SELBSTBEDIENUNG', 'invoicing', 'AENDERN', true, 'ALLE')
ON CONFLICT (role_code, module, action) DO NOTHING;

-- Zuordnung: der Akteur beschenkt sich selbst (granted_by ist NOT NULL, und
-- einen Menschen dafür zu nehmen wäre genau die Falschzuschreibung, die dieser
-- Slice beseitigt).
INSERT INTO security.user_role (user_id, role_code, valid_from, granted_by)
VALUES ('__SYSTEMAKTEUR__', 'SYSTEM_SELBSTBEDIENUNG', CURRENT_DATE,
        '__SYSTEMAKTEUR__');

-- ===========================================================================
-- notify.notification.kind — zwei neue Arten (siehe Kopf von 0137)
-- ===========================================================================
ALTER TABLE notify.notification DROP CONSTRAINT notification_kind_check;
ALTER TABLE notify.notification ADD CONSTRAINT notification_kind_check
    CHECK (kind IN (
        'AUFGABE_ZUGEWIESEN',
        'AUFGABE_ENTZOGEN',
        'AUFGABE_ERLEDIGT',
        'AUFGABE_WIEDEROFFEN',
        'AUFGABE_VERWORFEN',
        'AUFGABE_KOMMENTAR',
        -- Ein Kunde hat über seinen Freigabelink entschieden. Empfänger sind
        -- ausschließlich Konten, die das Angebot ohnehin sehen dürfen
        -- (invoicing/VERSENDEN) — docs/INVARIANTEN.md, Abschnitt 5.
        'ANGEBOT_ANGENOMMEN',
        'ANGEBOT_ABGELEHNT'
    ));
""".replace("__SYSTEMAKTEUR__", SYSTEMAKTEUR_ID)
# Bewusst `.replace` statt %-Formatierung: Das SQL enthält PL/pgSQL-RAISE-
# Formatstrings mit einzelnen `%` — eine %-Formatierung würde daran scheitern.

REVERSE_SQL = r"""
ALTER TABLE notify.notification DROP CONSTRAINT notification_kind_check;
ALTER TABLE notify.notification ADD CONSTRAINT notification_kind_check
    CHECK (kind IN (
        'AUFGABE_ZUGEWIESEN', 'AUFGABE_ENTZOGEN', 'AUFGABE_ERLEDIGT',
        'AUFGABE_WIEDEROFFEN', 'AUFGABE_VERWORFEN', 'AUFGABE_KOMMENTAR'
    ));

DROP TRIGGER IF EXISTS trg_accounts_user_kein_systemakteur ON public.accounts_user;
DROP FUNCTION IF EXISTS security.forbid_login_for_system_actor() CASCADE;
DROP TABLE IF EXISTS security.public_link;
DROP FUNCTION IF EXISTS security.guard_public_link() CASCADE;
ALTER TABLE security.app_user DROP COLUMN IF EXISTS is_system;
"""


class Migration(migrations.Migration):

    dependencies = [
        # ⚠️ 0139/0140 gehören einem parallel gebauten Slice (Material→Rechnung)
        # und fassen andere Tabellen an. Der Zusammenschluss beider Stränge
        # passiert **dort**: 0140 hängt an 0139 UND an 0142 und ist damit das
        # einzige Blatt. Diese Zeile darf deshalb NICHT auf 0140 gezogen werden —
        # das ergäbe einen Zyklus.
        ("db_core", "0139_material_abrechenbar"),
        # Der Login-Trigger sitzt auf Djangos eigener Tabelle — sie muss es
        # geben, bevor er entsteht.
        ("accounts", "0002_alter_user_email_user_uniq_user_email_ci"),
    ]

    operations = [
        # Rückwärts nur, solange keine Links ausgegeben und keine Automatenläufe
        # protokolliert sind (Politik aus db/README.md). Der Systemakteur und
        # seine Rollenzuordnung bleiben stehen: user_role trägt No-Delete, und
        # ein Audit-Urheber wird nicht rückwärts migriert.
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
