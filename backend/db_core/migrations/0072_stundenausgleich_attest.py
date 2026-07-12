"""HR-Reste: Stundenausgleich, Resturlaubs-Verfallsregel, Attest (DSGVO Art. 9).

Drei Bausteine, eine Migration.

1) hr.time_adjustment — die Ausgleichsbuchung auf dem Arbeitszeitkonto
-----------------------------------------------------------------------
Das Stundenkonto ist **abgeleitet, nie gespeichert** (Ist − Soll; Konvention wie
offener Rechnungsbetrag und Urlaubsverbrauch). Ein Ausgleich („der Rest der
Minusstunden wird einbehalten", „30 Mehrstunden werden ausgezahlt") ist deshalb
KEINE Korrektur eines gespeicherten Saldos — es gibt keinen. Er ist eine
**eigene Buchung** auf demselben Konto:

    Saldo = Ist − Soll + Σ Ausgleich

Damit bleibt der Saldo abgeleitet, und die Ausgleichsbuchung ist genau das, was
sie fachlich ist: eine begründete, datierte, zurechenbare Willenserklärung des
Arbeitgebers — kein stilles Umschreiben der Aufzeichnung.

**Minuten statt Stunden (numerisch exakt).** `minutes integer` — nicht
`numeric(6,2)` Stunden: 20 Minuten sind 0,3333… h und ließen sich in einer
Dezimalspalte nicht verlustfrei ablegen. Ein Arbeitszeitkonto, das bei jeder
Drittelstunde rundet, ist keine Aufzeichnung, sondern eine Schätzung. Die
Anzeige rechnet in Stunden um (2 Nachkommastellen), die **Wahrheit steht in
Minuten**. Das Vorzeichen trägt die Zahl: negativ = das Konto wird belastet
(Auszahlung, Freizeitausgleich, Einbehalt von Minusstunden), positiv = das Konto
wird gutgeschrieben (Gutschrift/Korrektur zugunsten des Beschäftigten).

**Ausgleichsart = Codeliste, keine Stammdatentabelle.** Anders als bei
`hr.time_category` (dort trägt `is_work_time` eine *fachlich harte*, gesetzlich
determinierte Eigenschaft, die der Betrieb je Kategorie setzen muss) hat die
Ausgleichsart **kein** frei zu konfigurierendes Attribut: Sie ist reine
Klassifikation für Auswertung und Nachvollziehbarkeit und wirkt sich nirgends
rechnerisch aus (das Vorzeichen steht in `minutes`, nicht in der Art). Eine
Konfigurationstabelle brächte also Pflegeaufwand und eine zweite Wahrheit, ohne
eine einzige Regel zu tragen — und eine frei erfundene Art ließe die Auswertung
(„was wurde ausgezahlt?") stillschweigend auseinanderlaufen. Vier Arten decken
die Praxis:

  * EINBEHALT           — Minusstunden werden einbehalten/verfallen (Konto steigt
                          auf 0: positive Buchung).
  * AUSZAHLUNG          — Mehrstunden werden ausgezahlt (Konto sinkt).
  * FREIZEITAUSGLEICH   — Mehrstunden werden in Freizeit abgegolten (Konto sinkt).
  * KORREKTUR           — begründete Kontoberichtigung (beide Vorzeichen).

**Kein stilles Löschen (GoBD/Nachvollziehbarkeit): append-only + Storno.**
Die Tabelle erbt No-Delete/No-Truncate/Audit. Eine Fehlbuchung wird **storniert**,
nicht gelöscht: Die Storno-Zeile trägt `reversal_of_id` auf die Ursprungsbuchung
und deren negierte Minuten; die Ursprungsbuchung geht auf `STORNIERT`. **Beide
Zeilen bleiben stehen und beide zählen nicht mehr** — die Summe läuft über
`status = 'GEBUCHT' AND reversal_of_id IS NULL`. (Die Storno-Zeile trägt ihre
Minuten trotzdem, damit die Liste den Vorgang lesbar zeigt; sie doppelt den
Betrag nicht, weil sie aus der Summe fällt.) Ein Trigger erzwingt: höchstens ein
Storno je Buchung (UNIQUE), nur aus `GEBUCHT` heraus, exakt negierte Minuten,
gleicher Mitarbeiter, kein Storno eines Stornos — und ansonsten ist die Zeile
**unveränderlich** (nur der Übergang GEBUCHT → STORNIERT ist erlaubt).

2) Verfallsregel für den Resturlaub (company.company_profile)
--------------------------------------------------------------
`vacation_carryover_expiry_month` / `_day`. **Default NULL = kein Verfall.**
Viele Betriebe lassen Resturlaub zum 31.03. verfallen (§ 7 Abs. 3 BUrlG:
Übertragung nur bei betrieblichen/personenbezogenen Gründen, dann bis 31.03.) —
aber das ist eine **betriebliche Vereinbarung**, keine Naturkonstante, und
BAG/EuGH lassen den Anspruch ohnehin nur verfallen, wenn der Arbeitgeber seiner
Hinweis- und Aufforderungsobliegenheit nachgekommen ist. Deshalb: **wir rechnen
nichts weg, was der Betrieb nicht ausdrücklich eingestellt hat.** Ohne
Einstellung verfällt nichts; der Verbrauch bleibt wie bisher abgeleitet.
Beide Spalten zusammen oder gar nicht (CHECK).

3) content.file_link.absence_id — die Arbeitsunfähigkeitsbescheinigung
-----------------------------------------------------------------------
Gesundheitsdaten sind eine **besondere Kategorie nach DSGVO Art. 9**. Die
Verarbeitung stützt sich auf Art. 9 Abs. 2 lit. b i. V. m. § 26 Abs. 3 BDSG
(Erfüllung arbeitsrechtlicher Pflichten — Entgeltfortzahlung nach § 3 EFZG,
Nachweis nach § 5 EFZG).

Die Datei selbst liegt in der bestehenden Ablage (`content.file`, MinIO); NEU ist
allein die **Zielspalte** `absence_id`. Daraus folgen drei physische Regeln hier,
der Rest steht in `api/dateien.py` (Ziel-Guard) und `db_core/services/dateien.py`:

  * Der `num_nonnulls(...) = 1`-CHECK wird um `absence_id` erweitert — ein Attest
    hängt an **genau einer** Abwesenheit und an nichts sonst.
  * **Kein Attest an einer verworfenen Abwesenheit** (ABGELEHNT/ZURUECKGEZOGEN):
    Ein solcher Antrag ist gegenstandslos; Gesundheitsdaten daran zu heften wäre
    eine Verarbeitung ohne Zweck (Art. 5 Abs. 1 lit. b/c — Zweckbindung,
    Datenminimierung). Trigger, nicht nur Service.
  * **An einer GENEHMIGTEN Abwesenheit bleibt der Anhang zulässig.** Das ist eine
    bewusste Entscheidung gegen das naheliegende „genehmigt = versiegelt": Die
    Erstbescheinigung trifft typischerweise NACH der Erfassung/Genehmigung der
    Krankheit ein (§ 5 Abs. 1 EFZG: Vorlage ab dem vierten Tag), und
    Folgebescheinigungen kommen laufend nach. Wer den Upload mit der Genehmigung
    sperrt, macht den Nachweis, den er verlangt, unmöglich. Der Bericht
    (`site_report`) ist der Gegenfall — dort versiegelt die **Unterschrift** einen
    fertigen Nachweis; hier ist das Attest der Nachweis, der erst noch kommt.

Was hier bewusst NICHT entsteht: **kein Diagnosefeld, keine Diagnose im Namen.**
Gespeichert wird nur „Arbeitsunfähigkeitsbescheinigung von–bis" (das ergibt sich
bereits aus der Abwesenheit selbst) — der vom Client gelieferte Dateiname wird in
`api/dateien.py` verworfen und durch einen neutralen ersetzt, damit
`grippaler_infekt.pdf` nicht in einer Dateiliste landet.
"""
import django.db.models.functions.datetime
from django.db import migrations, models

CREATE_SQL = r"""
-- ---------------------------------------------------------------------------
-- 1. hr.time_adjustment — Ausgleichsbuchung auf dem Arbeitszeitkonto
-- ---------------------------------------------------------------------------
CREATE TABLE hr.time_adjustment (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id     uuid NOT NULL REFERENCES hr.employee (id),
    adjustment_type text NOT NULL CHECK (adjustment_type IN
                    ('EINBEHALT', 'AUSZAHLUNG', 'FREIZEITAUSGLEICH', 'KORREKTUR')),
    -- Verbuchungszeitpunkt: der Tag, an dem der Ausgleich auf dem Konto wirkt.
    effective_on    date NOT NULL,
    -- Vorzeichenbehaftet, in MINUTEN (exakt; 20 min sind keine 0,33 h).
    -- 0 waere keine Buchung, sondern Rauschen in der Aufzeichnung.
    minutes         integer NOT NULL CHECK (minutes <> 0 AND abs(minutes) <= 600000),
    -- Pflicht. Ohne Begruendung ist eine Kontobewegung nicht nachvollziehbar
    -- (GoBD-Nachvollziehbarkeit, und arbeitsrechtlich muss der Beschaeftigte
    -- erkennen koennen, warum sein Konto sich aendert).
    reason          text NOT NULL CHECK (btrim(reason) <> ''),
    status          text NOT NULL DEFAULT 'GEBUCHT'
                    CHECK (status IN ('GEBUCHT', 'STORNIERT')),
    -- Storno-Muster: die Storno-Zeile zeigt auf die stornierte Buchung.
    reversal_of_id  uuid NULL REFERENCES hr.time_adjustment (id),
    created_by      uuid NOT NULL REFERENCES security.app_user (id),
    version         integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Hoechstens EIN Storno je Buchung (sonst zoege ein zweites Storno den Betrag
-- ein weiteres Mal in die Liste und verwirrte die Nachvollziehbarkeit).
CREATE UNIQUE INDEX uq_time_adjustment_reversal
    ON hr.time_adjustment (reversal_of_id) WHERE reversal_of_id IS NOT NULL;
CREATE INDEX idx_time_adjustment_employee
    ON hr.time_adjustment (employee_id, effective_on);

COMMENT ON TABLE hr.time_adjustment IS
    'Ausgleichsbuchung auf dem Arbeitszeitkonto. Der Saldo bleibt abgeleitet: Ist - Soll + Summe(Ausgleich). Append-only, Korrektur nur per Storno.';
COMMENT ON COLUMN hr.time_adjustment.minutes IS
    'Vorzeichenbehaftet, in Minuten. Positiv = Gutschrift aufs Konto, negativ = Belastung.';

CREATE FUNCTION hr.enforce_time_adjustment() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_orig hr.time_adjustment%ROWTYPE;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.reversal_of_id IS NOT NULL THEN
            SELECT * INTO v_orig FROM hr.time_adjustment
            WHERE id = NEW.reversal_of_id FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'Stundenausgleich: die zu stornierende Buchung % existiert nicht',
                    NEW.reversal_of_id USING ERRCODE = 'raise_exception';
            END IF;
            IF v_orig.reversal_of_id IS NOT NULL THEN
                RAISE EXCEPTION
                    'Stundenausgleich: eine Storno-Buchung kann nicht storniert werden'
                    USING ERRCODE = 'raise_exception';
            END IF;
            IF v_orig.status <> 'GEBUCHT' THEN
                RAISE EXCEPTION
                    'Stundenausgleich: die Buchung ist bereits storniert'
                    USING ERRCODE = 'raise_exception';
            END IF;
            IF NEW.employee_id <> v_orig.employee_id THEN
                RAISE EXCEPTION
                    'Stundenausgleich: ein Storno gehoert demselben Mitarbeiter wie die Buchung'
                    USING ERRCODE = 'raise_exception';
            END IF;
            IF NEW.minutes <> -v_orig.minutes THEN
                RAISE EXCEPTION
                    'Stundenausgleich: ein Storno traegt exakt die negierten Minuten der Buchung (% statt %)',
                    NEW.minutes, -v_orig.minutes USING ERRCODE = 'raise_exception';
            END IF;
            IF NEW.status <> 'GEBUCHT' THEN
                RAISE EXCEPTION
                    'Stundenausgleich: eine Storno-Buchung wird im Status GEBUCHT angelegt'
                    USING ERRCODE = 'raise_exception';
            END IF;
            -- Die Ursprungsbuchung faellt auf STORNIERT. Beide Zeilen bleiben
            -- stehen, beide zaehlen nicht mehr (Summe: GEBUCHT + reversal_of_id IS NULL).
            UPDATE hr.time_adjustment SET status = 'STORNIERT'
            WHERE id = v_orig.id;
        ELSIF NEW.status <> 'GEBUCHT' THEN
            RAISE EXCEPTION
                'Stundenausgleich: eine neue Buchung wird im Status GEBUCHT angelegt'
                USING ERRCODE = 'raise_exception';
        END IF;
        RETURN NEW;
    END IF;

    -- UPDATE: alles unveraenderlich ausser dem Uebergang GEBUCHT -> STORNIERT.
    IF NEW.employee_id IS DISTINCT FROM OLD.employee_id
       OR NEW.adjustment_type IS DISTINCT FROM OLD.adjustment_type
       OR NEW.effective_on IS DISTINCT FROM OLD.effective_on
       OR NEW.minutes IS DISTINCT FROM OLD.minutes
       OR NEW.reason IS DISTINCT FROM OLD.reason
       OR NEW.reversal_of_id IS DISTINCT FROM OLD.reversal_of_id
       OR NEW.created_by IS DISTINCT FROM OLD.created_by THEN
        RAISE EXCEPTION
            'Stundenausgleich %: eine gebuchte Ausgleichsbuchung ist unveraenderlich — eine Fehlbuchung wird storniert, nicht umgeschrieben',
            OLD.id USING ERRCODE = 'raise_exception';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status
       AND NOT (OLD.status = 'GEBUCHT' AND NEW.status = 'STORNIERT') THEN
        RAISE EXCEPTION
            'Stundenausgleich %: Statuswechsel % -> % ist nicht zulaessig',
            OLD.id, OLD.status, NEW.status USING ERRCODE = 'raise_exception';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_time_adjustment_enforce
    BEFORE INSERT OR UPDATE ON hr.time_adjustment
    FOR EACH ROW EXECUTE FUNCTION hr.enforce_time_adjustment();
CREATE TRIGGER trg_time_adjustment_updated_at
    BEFORE UPDATE ON hr.time_adjustment
    FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
CREATE TRIGGER trg_time_adjustment_audit
    AFTER UPDATE ON hr.time_adjustment
    FOR EACH ROW EXECUTE FUNCTION audit.audit_row_update();
CREATE TRIGGER trg_time_adjustment_no_delete
    BEFORE DELETE ON hr.time_adjustment
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
CREATE TRIGGER trg_time_adjustment_no_truncate
    BEFORE TRUNCATE ON hr.time_adjustment
    FOR EACH STATEMENT EXECUTE FUNCTION util.forbid_mutation();
REVOKE DELETE, TRUNCATE ON hr.time_adjustment FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- 2. Verfallsregel fuer den Resturlaub (NULL = kein Verfall — Default!)
-- ---------------------------------------------------------------------------
ALTER TABLE company.company_profile
    ADD COLUMN vacation_carryover_expiry_month smallint NULL
        CHECK (vacation_carryover_expiry_month IS NULL
               OR vacation_carryover_expiry_month BETWEEN 1 AND 12),
    ADD COLUMN vacation_carryover_expiry_day smallint NULL
        CHECK (vacation_carryover_expiry_day IS NULL
               OR vacation_carryover_expiry_day BETWEEN 1 AND 31),
    ADD CONSTRAINT company_profile_carryover_expiry_paar
        CHECK ((vacation_carryover_expiry_month IS NULL)
               = (vacation_carryover_expiry_day IS NULL));

COMMENT ON COLUMN company.company_profile.vacation_carryover_expiry_month IS
    'Monat des Resturlaubs-Verfalls im Folgejahr (z. B. 3 fuer den 31.03.). NULL = kein Verfall (Default); der Uebertrag verfaellt dann nie von selbst.';

-- ---------------------------------------------------------------------------
-- 3. content.file_link.absence_id — Attest (DSGVO Art. 9)
-- ---------------------------------------------------------------------------
ALTER TABLE content.file_link
    ADD COLUMN absence_id uuid NULL REFERENCES hr.absence (id);

ALTER TABLE content.file_link DROP CONSTRAINT file_link_check;
ALTER TABLE content.file_link ADD CONSTRAINT file_link_check CHECK (
    num_nonnulls(service_case_id, work_order_id, service_job_id, property_id,
                 unit_id, asset_id, quote_id, invoice_id, party_id,
                 communication_id, project_id, article_id, site_report_id,
                 absence_id) = 1
);

COMMENT ON COLUMN content.file_link.absence_id IS
    'Arbeitsunfaehigkeitsbescheinigung an einer Abwesenheit. Gesundheitsdatum (DSGVO Art. 9) — Zugriff nur fuer den Betroffenen selbst und die Personalverwaltung (siehe api/dateien.py).';

-- Kein Attest an einer verworfenen Abwesenheit: der Antrag ist gegenstandslos,
-- ein Gesundheitsdatum daran waere eine Verarbeitung ohne Zweck (Art. 5).
CREATE FUNCTION content.check_absence_attachment() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_status text;
BEGIN
    IF NEW.absence_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT a.status INTO v_status FROM hr.absence a WHERE a.id = NEW.absence_id;
    IF v_status IS NULL THEN
        RAISE EXCEPTION 'file_link: Die Abwesenheit % existiert nicht.', NEW.absence_id
            USING ERRCODE = 'raise_exception';
    END IF;
    IF v_status IN ('ABGELEHNT', 'ZURUECKGEZOGEN') THEN
        RAISE EXCEPTION
            'file_link: An einer % Abwesenheit kann keine Bescheinigung hinterlegt werden — der Antrag ist gegenstandslos.',
            v_status USING ERRCODE = 'raise_exception';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_file_link_absence
    BEFORE INSERT OR UPDATE ON content.file_link
    FOR EACH ROW EXECUTE FUNCTION content.check_absence_attachment();
"""

DROP_SQL = r"""
DROP TRIGGER IF EXISTS trg_file_link_absence ON content.file_link;
DROP FUNCTION IF EXISTS content.check_absence_attachment();

ALTER TABLE content.file_link DROP CONSTRAINT file_link_check;
ALTER TABLE content.file_link ADD CONSTRAINT file_link_check CHECK (
    num_nonnulls(service_case_id, work_order_id, service_job_id, property_id,
                 unit_id, asset_id, quote_id, invoice_id, party_id,
                 communication_id, project_id, article_id, site_report_id) = 1
);
ALTER TABLE content.file_link DROP COLUMN absence_id;

ALTER TABLE company.company_profile
    DROP CONSTRAINT IF EXISTS company_profile_carryover_expiry_paar,
    DROP COLUMN IF EXISTS vacation_carryover_expiry_month,
    DROP COLUMN IF EXISTS vacation_carryover_expiry_day;

DROP TABLE IF EXISTS hr.time_adjustment;
DROP FUNCTION IF EXISTS hr.enforce_time_adjustment();
"""


class Migration(migrations.Migration):

    # Zwei parallel gebaute Zweige hängen an 0068 (0069 Aufschlagsmatrix,
    # 0071 Fälligkeiten). Diese Migration führt sie zusammen und bleibt das
    # einzige Blatt — sonst verweigerte Django den Graphen („multiple leaf
    # nodes"). Fachlich berühren sich die drei nicht. Muster: 0026.
    dependencies = [
        ("db_core", "0069_aufschlagsmatrix"),
        ("db_core", "0071_faelligkeiten"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
        # State-only (managed=False ⇒ kein DDL).
        migrations.CreateModel(
            name="TimeAdjustment",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False)),
                ("adjustment_type", models.TextField()),
                ("effective_on", models.DateField()),
                ("minutes", models.IntegerField()),
                ("reason", models.TextField()),
                ("status", models.TextField(db_default=models.Value("GEBUCHT"))),
                ("version", models.IntegerField(db_default=models.Value(1))),
                (
                    "created_at",
                    models.DateTimeField(
                        db_default=django.db.models.functions.datetime.Now()
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        db_default=django.db.models.functions.datetime.Now()
                    ),
                ),
            ],
            options={"db_table": 'hr"."time_adjustment', "managed": False},
        ),
    ]
