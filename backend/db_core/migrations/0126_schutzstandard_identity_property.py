"""Schutzstandard für Kontaktstammdaten und Liegenschaft (Befund H7/I2, AP4).

Warum jetzt
-----------
`CLAUDE.md` verlangt für jede Fachtabelle den Schutzstandard (Audit, No-Delete,
No-Truncate). Sieben Tabellen unterlaufen ihn bis heute:

* `identity.party_address`, `identity.contact_point` — **kein einziger Trigger**
* `identity.person`, `identity.organization` — nur Typkonsistenz (0002)
* `identity.party` — nur `updated_at` und Merge-Wächter (0002/0031)
* `property.property` — nur `updated_at` (0004)
* `identity.address` — `trg_address_immutable` (0003) sperrt UPDATE/DELETE,
  aber TRUNCATE liefe daran vorbei

Aufgefallen ist es nie, weil es zu diesen Tabellen **kaum Schreibpfade außer
INSERT** gab. Genau das ändert AP4 (Telefon korrigieren, Adresszuordnung
beenden, Namen ändern, Liegenschaftsadresse umhängen). Die Querschnittsregel des
Befunddokuments lautet: *Audit-Trigger für jede Tabelle, die einen neuen
Schreibpfad bekommt* — sonst wächst mit jedem Endpunkt die Nachweislücke.

Bezeichnend ist der Vergleich: `identity.party_relationship` trägt den vollen
Satz seit 0009, obwohl sie strukturell identisch zu `party_address` und
`contact_point` ist (zeitabhängige Zuordnung mit `valid_from`/`valid_until` und
Exclusion-Constraint). Die beiden wurden schlicht vergessen; es gibt keine
Kommentarzeile, die sie ausnimmt.

Die Falle: person und organization haben keine Spalte `id`
---------------------------------------------------------
`audit.audit_row_update()` (0009) schreibt `(to_jsonb(NEW) ->> 'id')::uuid` als
`target_id`. Bei `identity.person` und `identity.organization` heißt der
Primärschlüssel aber **`party_id`** (0002:96, 0002:105) — der Standardtrigger
schriebe dort stumm `NULL` und das Audit wüsste nicht, WEN es protokolliert.

Deshalb hier `audit.audit_row_update_key()`: dieselbe Funktion, aber der Name
der Schlüsselspalte kommt als Trigger-Argument. Die bestehende Funktion bleibt
unangetastet — sechzehn Tabellen hängen daran.

Kein REVOKE DELETE, kein Audit auf address
------------------------------------------
Wie bei `property.room` (0086) und `property_party_role` (0009) sperrt der
No-Delete-*Trigger* fachlich und mit lesbarer Meldung; ein zusätzliches
`REVOKE DELETE` brächte nur eine Rechte-Fehlermeldung aus der Tiefe. TRUNCATE
dagegen umgeht jeden Row-Trigger und muss auf Rechteebene weg.

`identity.address` bekommt **kein** Audit: Die Tabelle ist append-only, ein
UPDATE kann es dort gar nicht geben (H1). Sie bekommt nur den fehlenden
TRUNCATE-Schutz.

Rückwärts
---------
Trigger und Funktion fallen. Das `REVOKE` wird bewusst nicht zurückgenommen —
TRUNCATE liegt bei PUBLIC ohnehin nicht an, ein `GRANT` beim Zurückrollen
vergäbe ein Recht, das vorher niemand hatte.
"""
from django.db import migrations

FORWARD_SQL = r"""
-- ---------------------------------------------------------------------------
-- Audit-Variante fuer Tabellen, deren Primaerschluessel nicht `id` heisst.
-- ---------------------------------------------------------------------------
CREATE FUNCTION audit.audit_row_update_key() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_user uuid := nullif(current_setting('app.current_user_id', true), '')::uuid;
    v_key  text := TG_ARGV[0];
BEGIN
    INSERT INTO audit.audit_entry
        (actor_type, actor_user_id, action, target_type, target_id,
         before_excerpt, after_excerpt)
    VALUES
        (CASE WHEN v_user IS NULL THEN 'SYSTEM' ELSE 'USER' END,
         v_user,
         'ROW_UPDATE',
         TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME,
         (to_jsonb(NEW) ->> v_key)::uuid,
         to_jsonb(OLD),
         to_jsonb(NEW));
    RETURN NULL;
END;
$$;

COMMENT ON FUNCTION audit.audit_row_update_key() IS
    'Wie audit.audit_row_update(), aber die Schluesselspalte kommt als Trigger-Argument. Fuer identity.person und identity.organization, deren PK party_id heisst.';

-- ---------------------------------------------------------------------------
-- identity.party_address — zeitabhaengige Zuordnung, bisher OHNE jeden Trigger
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_party_address_audit
    AFTER UPDATE ON identity.party_address
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_party_address_no_delete
    BEFORE DELETE ON identity.party_address
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_party_address_no_truncate
    BEFORE TRUNCATE ON identity.party_address
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE TRUNCATE ON identity.party_address FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- identity.contact_point — dito
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_contact_point_audit
    AFTER UPDATE ON identity.contact_point
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_contact_point_no_delete
    BEFORE DELETE ON identity.contact_point
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_contact_point_no_truncate
    BEFORE TRUNCATE ON identity.contact_point
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE TRUNCATE ON identity.contact_point FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- identity.person / identity.organization — PK heisst party_id (siehe Kopf)
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_person_audit
    AFTER UPDATE ON identity.person
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update_key('party_id');
CREATE TRIGGER trg_person_no_delete
    BEFORE DELETE ON identity.person
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_person_no_truncate
    BEFORE TRUNCATE ON identity.person
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE TRUNCATE ON identity.person FROM PUBLIC;

CREATE TRIGGER trg_organization_audit
    AFTER UPDATE ON identity.organization
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update_key('party_id');
CREATE TRIGGER trg_organization_no_delete
    BEFORE DELETE ON identity.organization
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_organization_no_truncate
    BEFORE TRUNCATE ON identity.organization
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE TRUNCATE ON identity.organization FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- identity.party — traegt display_name, den die Namensaenderung fortschreibt
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_party_audit
    AFTER UPDATE ON identity.party
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_party_no_delete
    BEFORE DELETE ON identity.party
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_party_no_truncate
    BEFORE TRUNCATE ON identity.party
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE TRUNCATE ON identity.party FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- property.property — bekommt mit AP4 erstmals ein PATCH
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_property_audit
    AFTER UPDATE ON property.property
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_property_no_delete
    BEFORE DELETE ON property.property
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_property_no_truncate
    BEFORE TRUNCATE ON property.property
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE TRUNCATE ON property.property FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- identity.address — UPDATE/DELETE sperrt bereits trg_address_immutable (0003).
-- Es fehlte nur der TRUNCATE-Schutz, der jeden Row-Trigger umginge.
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_address_no_truncate
    BEFORE TRUNCATE ON identity.address
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE TRUNCATE ON identity.address FROM PUBLIC;
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS trg_address_no_truncate ON identity.address;

DROP TRIGGER IF EXISTS trg_property_no_truncate ON property.property;
DROP TRIGGER IF EXISTS trg_property_no_delete ON property.property;
DROP TRIGGER IF EXISTS trg_property_audit ON property.property;

DROP TRIGGER IF EXISTS trg_party_no_truncate ON identity.party;
DROP TRIGGER IF EXISTS trg_party_no_delete ON identity.party;
DROP TRIGGER IF EXISTS trg_party_audit ON identity.party;

DROP TRIGGER IF EXISTS trg_organization_no_truncate ON identity.organization;
DROP TRIGGER IF EXISTS trg_organization_no_delete ON identity.organization;
DROP TRIGGER IF EXISTS trg_organization_audit ON identity.organization;

DROP TRIGGER IF EXISTS trg_person_no_truncate ON identity.person;
DROP TRIGGER IF EXISTS trg_person_no_delete ON identity.person;
DROP TRIGGER IF EXISTS trg_person_audit ON identity.person;

DROP TRIGGER IF EXISTS trg_contact_point_no_truncate ON identity.contact_point;
DROP TRIGGER IF EXISTS trg_contact_point_no_delete ON identity.contact_point;
DROP TRIGGER IF EXISTS trg_contact_point_audit ON identity.contact_point;

DROP TRIGGER IF EXISTS trg_party_address_no_truncate ON identity.party_address;
DROP TRIGGER IF EXISTS trg_party_address_no_delete ON identity.party_address;
DROP TRIGGER IF EXISTS trg_party_address_audit ON identity.party_address;

DROP FUNCTION IF EXISTS audit.audit_row_update_key();
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0125_vorname_optional"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
