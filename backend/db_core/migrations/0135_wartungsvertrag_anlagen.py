"""Wartungsvertrag ↔ technische Anlage — `maintenance.contract_asset` (n:m).

**Der Befund (Sascha, Praxistest):** Am Anlagendetail stand bisher der Satz
„Wartungsverträge werden zur Liegenschaft geführt, nicht zur einzelnen Anlage.
Die folgenden Verträge gelten für dieses Objekt — welche Anlage sie abdecken,
sagt das System (noch) nicht." Das war ehrlich und trotzdem unbrauchbar: Wer vor
einer von sechs Thermen steht, will wissen, ob **diese** unter Vertrag steht.
In einem Objekt mit dreißig Etagenthermen ist „irgendein Vertrag gilt für dieses
Haus" keine Auskunft, sondern eine Rückfrage beim Büro.

**Warum n:m und nicht `asset_id` am Vertrag.** Ein Wartungsvertrag deckt in der
Praxis mehrere Anlagen ab (ein Vertrag über alle Thermen eines Hauses ist der
Normalfall, nicht die Ausnahme), und dieselbe Anlage kann in mehreren Verträgen
stehen (Wartung jährlich, Abgasmessung nach Kehr- und Überprüfungsordnung im
eigenen Vertrag). Eine Spalte `asset_id` hätte den ersten Vertrag mit zwei
Thermen sofort erpresst: entweder zwei Verträge anlegen (falsch — es ist einer)
oder das Feld leer lassen (dann ist es wieder Zierrat). Vorbild für die Form ist
`hr.employee_trade` (0120): Zuordnungstabelle mit vollem Schutzstandard und
`active`-Flag statt DELETE.

**Leere Zuordnung heißt weiter „gilt fürs ganze Objekt".** Bestandsverträge
bekommen keine erfundene Anlage untergeschoben. Ein Vertrag ohne einzige
Zuordnung gilt wie bisher für die Liegenschaft; das Anlagendetail zeigt ihn dort
weiter an und spricht den Unterschied aus (`bezug` = ANLAGE | LIEGENSCHAFT).
Ein Vertrag, der ausdrücklich Anlage A abdeckt, taucht bei Anlage B **nicht**
mehr auf — genau das war der Fehlschluss, den die alte Anzeige erzwang.

**Die DB erzwingt die Objektgleichheit physisch.** Vertrag und Anlage müssen an
derselben Liegenschaft hängen; ohne diesen Zwang ließe sich der Vertrag von
Haus A an die Therme von Haus B hängen und niemand merkte es. Der Zwang läuft
über zwei zusammengesetzte FKs auf dieselbe `property_id`-Spalte dieser Tabelle
— dasselbe Muster wie `maintenance.inspection` (0071) und
`property.technical_asset` (0004). `property.technical_asset` hat den nötigen
UNIQUE (id, property_id) seit 0071; `maintenance.maintenance_contract` bekommt
ihn hier.
"""
from django.db import migrations

FORWARD_SQL = r"""
-- Zielschlüssel für den zusammengesetzten FK der Zuordnung. Fachlich ändert er
-- nichts (id ist bereits PK) — er macht (id, property_id) referenzierbar.
ALTER TABLE maintenance.maintenance_contract
    ADD CONSTRAINT maintenance_contract_id_property_key UNIQUE (id, property_id);

-- ---------------------------------------------------------------------------
-- Zuordnung Vertrag ↔ Anlage (n:m), voller Schutzstandard.
-- `id`/`version`/`created_at`/`updated_at` sind für audit.audit_row_update()
-- Pflicht (der ::uuid-Cast dort setzt eine uuid-PK namens `id` voraus, 0023).
-- ---------------------------------------------------------------------------
CREATE TABLE maintenance.contract_asset (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_id uuid NOT NULL,
    asset_id    uuid NOT NULL,
    -- Redundant? Nein: DIESE Spalte ist der Riegel. Beide FKs unten zeigen auf
    -- sie, damit Vertrag und Anlage zwingend an derselben Liegenschaft hängen.
    property_id uuid NOT NULL REFERENCES property.property (id),
    -- Deaktivieren statt Löschen, wie im ganzen Haus: Die Tabelle verbietet
    -- DELETE (unten), und „dieser Vertrag deckte 2026 die Therme ab" bleibt eine
    -- wahre Aussage, auch wenn die Zuordnung heute nicht mehr gilt.
    active      boolean NOT NULL DEFAULT true,
    created_by  uuid NOT NULL REFERENCES security.app_user (id),
    version     integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    -- Eine Zeile je Paar; ein zweites Zuordnen reaktiviert die vorhandene.
    CONSTRAINT contract_asset_unique UNIQUE (contract_id, asset_id),
    CONSTRAINT contract_asset_contract_fk
        FOREIGN KEY (contract_id, property_id)
        REFERENCES maintenance.maintenance_contract (id, property_id),
    CONSTRAINT contract_asset_asset_fk
        FOREIGN KEY (asset_id, property_id)
        REFERENCES property.technical_asset (id, property_id)
);

-- Die beiden echten Fragen: „welche Anlagen deckt dieser Vertrag ab?" und
-- „steht diese Anlage unter Vertrag?". Beendete Zuordnungen gehören in keine
-- der beiden Antworten — deshalb Teilindizes.
CREATE INDEX idx_contract_asset_contract
    ON maintenance.contract_asset (contract_id) WHERE active;
CREATE INDEX idx_contract_asset_asset
    ON maintenance.contract_asset (asset_id) WHERE active;

CREATE TRIGGER trg_contract_asset_updated_at
    BEFORE UPDATE ON maintenance.contract_asset
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_contract_asset_audit
    AFTER UPDATE ON maintenance.contract_asset
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_contract_asset_no_delete
    BEFORE DELETE ON maintenance.contract_asset
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_contract_asset_no_truncate
    BEFORE TRUNCATE ON maintenance.contract_asset
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON maintenance.contract_asset FROM PUBLIC;
"""

REVERSE_SQL = r"""
DROP TABLE IF EXISTS maintenance.contract_asset;
ALTER TABLE maintenance.maintenance_contract
    DROP CONSTRAINT IF EXISTS maintenance_contract_id_property_key;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0134_vollmacht_modell"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
