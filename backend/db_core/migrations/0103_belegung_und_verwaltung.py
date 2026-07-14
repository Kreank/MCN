"""Belegung (`tenure.*`) und Verwaltung (`management.*`) bekommen einen Schreibpfad.

Beide Schemata liegen seit **0005/0006** in der Datenbank und waren bis heute
**totes Schema**: kein Model, kein Service, kein Endpunkt, keine Zeile. Damit ist
das freigegebene Demo-Szenario (`docs/demo-szenario.md`) nicht abbildbar — die
WEG Badensche Straße 53 hat sechs Mieter und wird von der Stegos Immobilien GmbH
verwaltet, und weder das eine noch das andere ließ sich eintragen.

## Was diese Migration NICHT tut — und warum das der wichtigste Satz ist

Sie fügt **keine `party_id` an `tenure.occupancy`** hinzu. Der Slice-Auftrag ging
davon aus, die Belegung trage keinen Beteiligten. **Das Schema sagt etwas
anderes:** `tenure.occupancy_party` (0005, Z. 258 ff., Beschlüsse A-03/A-19) ist
seit dem ersten Tag genau dafür da — mit Rollen (`CONTRACTUAL_TENANT`,
`CO_TENANT`, `OCCUPANT`, `OWNER_OCCUPANT`, `COMMERCIAL_USER`), eigenem
Gültigkeitszeitraum, einem EXCLUDE gegen Doppelerfassung (F-10), einem deferred
Containment-Trigger (Beteiligtenzeitraum ⊆ Belegungszeitraum) und dem
MERGED-Schutz aus 0009.

Eine zusätzliche Spalte `occupancy.party_id` wäre eine **zweite Heimat für
denselben Fakt**. Zwei Wahrheiten laufen auseinander, und die großzügigere
gewinnt: Welcher Mieter gilt, wenn `occupancy.party_id` Robco sagt und
`occupancy_party` Musili? Dazu kommt der fachliche Verlust — eine einzelne Spalte
kann **kein Ehepaar** (zwei Vertragsmieter), **keinen Mitbewohner ohne Vertrag**
und **keinen Mieterwechsel mitten im Belegungszeitraum** abbilden. Der
Demo-Nutzen ist identisch: Der Monteur bekommt Name und Telefonnummer von Robco,
weil der Mieter ein ganz normaler `identity.party` ist.

**Leerstand** bleibt trivial darstellbar: eine Belegung mit
`occupancy_type = 'VACANT'` und **null** Beteiligten. Das ist dieselbe Aussage
wie „`party_id IS NULL`", nur ohne die zweite Wahrheit.

## Was sie tut

### 1. Schutzstandard vervollständigen (TRUNCATE)

Der Prüfauftrag lautete: „trägt die Tabelle den Schutzstandard?" — die Antwort ist
**fast**. Migration **0009** gibt `tenure.occupancy`, `tenure.occupancy_party`,
`management.management_mandate`, `management.management_responsibility` und
`management.party_authority` bereits **No-Delete + Audit**;
`management.management_mandate_unit` ist dort sogar **vollständig immutable**
(UPDATE **und** DELETE verboten — A-11: Umfangskorrekturen laufen über ein
Nachfolgemandat, nicht über das Umschreiben des laufenden). Das ist kein Zufall,
sondern eine getroffene Entscheidung, und der Service hält sich daran.

Was 0009 **nicht** gab, ist der TRUNCATE-Schutz (F-03 wurde dort nur für
`party_merge` und die Audit-Tabellen gezogen). Solange die Tabellen leer waren,
war das folgenlos. Ab heute stehen Fachdaten darin — also wird nachgezogen, wie
0101 es für `property.technical_asset` getan hat. *Was im Service sitzt, ist
umgehbar; erst was im Trigger sitzt, hält.*

### 2. Indizes, die es noch nicht gab

`0005`/`0006` legten **keinen einzigen** Index über die Primärschlüssel und
EXCLUDE-Constraints hinaus. Die Liegenschaftsmappe fragt aber genau quer dazu:
„alle Belegungen **dieser Liegenschaft**" (occupancy → unit → building →
property) und „alle Mandate **dieser Liegenschaft**". Ohne Index sind das Seq
Scans über die gesamte Belegungshistorie des Betriebs.

`idx_occupancy_party_party` trägt zusätzlich die **Objektsicht des Monteurs**
(`objektsicht.eigene_party_q`): „ist dieser Kontakt ein Mieter an einem meiner
Objekte?" fragt von der Party aus rückwärts.

### 3. Rechtematrix: der Monteur darf Mieter und Verwaltung LESEN

Die Startmatrix (0026) führt `tenure` und `management` bereits als Module — für
**jede** Rolle, über den Cross Join. Es fehlt also keine Zelle, sie steht für
MONTEUR nur auf `false`. Nachgezogen wird deshalb wie in **0099** (dem
Präzedenzfall für UPDATEs auf bestehende Zellen), nicht per INSERT.

| Rolle | Modul | Aktion | row_scope |
|---|---|---|---|
| MONTEUR | `tenure` | LESEN | EIGENE |
| MONTEUR | `management` | LESEN | EIGENE |

**Genau das ist der fachliche Kern des Slices:** Der Monteur fährt zur Badenschen
Straße und muss in die Wohnung EG rechts. Er braucht Name und Telefonnummer von
Robco, um einen Termin zu machen und hineinzukommen — und den Verwalter, wenn
niemand aufmacht. `identity/LESEN` (0099) hat er schon; ohne `tenure/LESEN` sieht
er nur, **dass** die Einheit belegt ist, nicht **von wem**.

**`row_scope = 'EIGENE'` heißt „meine Objekte"** — die eine Definition steht in
`db_core/services/objektsicht.py`, sie wird hier nicht nachgebaut. Ohne den
dortigen Filter wäre dieses `allowed = true` ein Vollzugriff auf die Mieterdaten
**aller** Liegenschaften des Betriebs; die Matrix allein begrenzt nichts.

**Bewusst NICHT vergeben:** ANLEGEN/AENDERN. Wer ändern dürfte, könnte den Mieter
einer Wohnung überschreiben, in der er gerade arbeitet — Mietverhältnisse sind
Sache des Büros. DISPOSITION, TECHNISCHE_LEITUNG, ADMINISTRATION und
GESCHAEFTSFUEHRUNG tragen die Schreibrechte bereits aus 0026; für sie ändert diese
Migration nichts.

## Rückwärts

`reverse_sql` nimmt exakt das zurück, was hier entsteht (Trigger, Indizes,
Matrixzellen). Es gibt keinen Grund für `noop`: Es werden keine Fachdaten
angefasst, keine Spalte fällt weg, nichts geht verloren.
"""
from django.db import migrations

FORWARD_SQL = r"""
-- ---------------------------------------------------------------------------
-- 1. Schutzstandard: TRUNCATE (No-Delete und Audit kommen aus 0009)
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_occupancy_no_truncate
    BEFORE TRUNCATE ON tenure.occupancy
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_occupancy_party_no_truncate
    BEFORE TRUNCATE ON tenure.occupancy_party
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_mandate_no_truncate
    BEFORE TRUNCATE ON management.management_mandate
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_mandate_unit_no_truncate
    BEFORE TRUNCATE ON management.management_mandate_unit
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_responsibility_no_truncate
    BEFORE TRUNCATE ON management.management_responsibility
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();

REVOKE TRUNCATE ON tenure.occupancy FROM PUBLIC;
REVOKE TRUNCATE ON tenure.occupancy_party FROM PUBLIC;
REVOKE TRUNCATE ON management.management_mandate FROM PUBLIC;
REVOKE TRUNCATE ON management.management_mandate_unit FROM PUBLIC;
REVOKE TRUNCATE ON management.management_responsibility FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- 2. Indizes für die Abfragewege, die es ab heute gibt
-- ---------------------------------------------------------------------------
-- „Alle Belegungen dieser Einheit" (Liegenschaftsmappe, Reiter Belegung).
CREATE INDEX idx_occupancy_unit ON tenure.occupancy (unit_id);
-- „Die Beteiligten dieser Belegung" (Mietername zur Einheit).
CREATE INDEX idx_occupancy_party_occupancy ON tenure.occupancy_party (occupancy_id);
-- „Ist dieser Kontakt Mieter an einem meiner Objekte?" — die Objektsicht des
-- Monteurs fragt von der Party aus rückwärts (objektsicht.eigene_party_q).
CREATE INDEX idx_occupancy_party_party ON tenure.occupancy_party (party_id);
-- „Wer verwaltet diese Liegenschaft?"
CREATE INDEX idx_mandate_property ON management.management_mandate (property_id);
CREATE INDEX idx_mandate_management_party
    ON management.management_mandate (management_party_id);
CREATE INDEX idx_mandate_unit_unit ON management.management_mandate_unit (unit_id);
CREATE INDEX idx_responsibility_mandate
    ON management.management_responsibility (mandate_id);

-- ---------------------------------------------------------------------------
-- 3. Rechtematrix: MONTEUR sieht Mieter und Verwaltung SEINER Objekte
--    (Muster 0099 — UPDATE bestehender Zellen, kein INSERT: die Module stehen
--    seit 0026 in der Matrix, nur auf `false`.)
-- ---------------------------------------------------------------------------
UPDATE security.role_permission
SET allowed = true, row_scope = 'EIGENE'
WHERE role_code = 'MONTEUR'
  AND module IN ('tenure', 'management')
  AND action = 'LESEN';
"""

REVERSE_SQL = r"""
UPDATE security.role_permission
SET allowed = false, row_scope = 'EIGENE'
WHERE role_code = 'MONTEUR'
  AND module IN ('tenure', 'management')
  AND action = 'LESEN';

DROP INDEX management.idx_responsibility_mandate;
DROP INDEX management.idx_mandate_unit_unit;
DROP INDEX management.idx_mandate_management_party;
DROP INDEX management.idx_mandate_property;
DROP INDEX tenure.idx_occupancy_party_party;
DROP INDEX tenure.idx_occupancy_party_occupancy;
DROP INDEX tenure.idx_occupancy_unit;

GRANT TRUNCATE ON management.management_responsibility TO PUBLIC;
GRANT TRUNCATE ON management.management_mandate_unit TO PUBLIC;
GRANT TRUNCATE ON management.management_mandate TO PUBLIC;
GRANT TRUNCATE ON tenure.occupancy_party TO PUBLIC;
GRANT TRUNCATE ON tenure.occupancy TO PUBLIC;

DROP TRIGGER trg_responsibility_no_truncate ON management.management_responsibility;
DROP TRIGGER trg_mandate_unit_no_truncate ON management.management_mandate_unit;
DROP TRIGGER trg_mandate_no_truncate ON management.management_mandate;
DROP TRIGGER trg_occupancy_party_no_truncate ON tenure.occupancy_party;
DROP TRIGGER trg_occupancy_no_truncate ON tenure.occupancy;
"""


class Migration(migrations.Migration):
    dependencies = [("db_core", "0102_monteur_angebot_ohne_preise")]
    operations = [migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL)]
