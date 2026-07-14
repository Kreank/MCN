"""Die negative Nettowandfläche wird auch beim ANLEGEN einer Wand unmöglich.

Review-Befund an 0089. Der Modulkopf dort (und der HANDOFF) behaupteten, eine
negative Nettowandfläche sei **physisch unmöglich**. Sie war es nicht.

## Der Befund

Auf `property.room_surface` lagen genau zwei Trigger:

    trg_room_surface_passt           BEFORE UPDATE OF gross_area_m2
    trg_room_surface_loeschen_passt  BEFORE DELETE

**Kein INSERT.** Dazu kam die Ausstiegsklausel in Grenze (b): Ist
`Σ Bauteilflächen = 0`, gilt die Wandfläche als *unbekannt* (nicht 0) und es wird
nicht geprüft — richtig gedacht, aber sie deckte den Fall zu, in dem die erste
Wand gerade **entsteht**:

    Raum ohne Hüllflächen
      + freie Öffnung (surface_id IS NULL) 25 m²      -> erlaubt (Wand unbekannt)
      + INSERT der ersten Wand mit 10 m²              -> KEIN Trigger feuerte
      => wall_area_net_m2 = −15,000 m²

Über die heutige API war das nicht erreichbar, weil `set_aufbau` den ganzen Satz
ersetzt und Wände **vor** Öffnungen einfügt. Der Schutz lag damit in der
**Reihenfolge im Service** — und genau das ist die Projektlehre: *Was im Service
sitzt, ist umgehbar; erst was im Trigger sitzt, hält.* Ein direkter INSERT (ORM,
SQL, künftiger Service, KI-Agent) ging daran vorbei, und die negative Menge wäre
als Mengengrundlage fürs Verputzen/Streichen in ein Angebot gelaufen.

## Der Fix

`property.enforce_room_opening_fits` bekommt einen INSERT-Zweig für
`room_surface` und feuert ab jetzt auch dort. Die neue Zeile steht beim BEFORE
INSERT noch nicht in der Tabelle — ihre Bruttofläche wird deshalb zu
`Σ Bauteilflächen` **hinzugerechnet** (`v_add_wand`), bevor Grenze (b) prüft.

Grenze (a) bleibt beim INSERT wirkungslos und muss es sein: Eine gerade erst
entstehende Wand hat noch keine Öffnungen (die Öffnung hängt per FK an ihr).

**Der legitime Fall bleibt legitim:** Wer das Fenster vor der Wand erfasst, darf
das weiterhin. Verboten ist nicht die Reihenfolge, sondern das **Ergebnis** —
eine Wand, die kleiner ist als die Summe der Öffnungen des Raumes.

`set_aufbau` läuft unverändert durch: Es löscht erst Öffnungen, dann Wände, und
fügt dann Wände vor Öffnungen ein. Beim Wand-INSERT ist `Σ Öffnungen = 0` — es
gibt keinen Zwischenzustand, der fälschlich feuern könnte. Ein CONSTRAINT TRIGGER
DEFERRED ist deshalb **nicht** nötig (und wäre teurer: er verschöbe die Prüfung
ans Transaktionsende, wo der Fehler nicht mehr der Zeile zuzuordnen ist).

Serialisierungspunkt bleibt die **Raumzeile** (`FOR UPDATE`), nicht die Wand —
Grenze (b) spannt über alle Wände des Raumes.

PL/pgSQL-Fallen aus 0089 (beide gelten weiter, beide eingehalten):
  (1) `FOR UPDATE` ist mit Aggregatfunktionen nicht erlaubt.
  (2) Eine `NEW.<feld>`-Referenz wird beim **Planen** aufgelöst, auch in einem
      CASE-Zweig, der nie zutrifft — derselbe Trigger feuert aus zwei Tabellen
      mit verschiedenen Zeilentypen. Felder deshalb **oben** in lokale Variablen
      heben, unten nur noch Variablen verwenden.
"""
from django.db import migrations

FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION property.enforce_room_opening_fits() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_room       uuid;
    v_surface_id uuid;      -- betroffene Wand (NULL = freier Mengenabzug)
    v_drop       uuid;      -- Wand, die gerade gelöscht wird
    v_skip_open  uuid;      -- Öffnung, die gerade geschrieben wird (alte Zeile ignorieren)
    v_add_area   numeric;   -- Fläche der neuen/geänderten Öffnung
    v_add_wand   numeric;   -- Bruttofläche der neuen Wand (steht noch nicht in der Tabelle)
    v_neu_id     uuid;      -- Wand, deren Bruttofläche gerade geändert wird …
    v_neu_gross  numeric;   -- … und ihr NEUER Wert
    v_gross      numeric;
    v_belegt     numeric;
    v_label      text;
    v_summe_wand numeric;
    v_summe_oeff numeric;
BEGIN
    -- Felder von NEW/OLD werden HIER in lokale Variablen gehoben (siehe Modulkopf,
    -- Falle 2): In den SQL-Anweisungen unten darf keine NEW.<feld>-Referenz stehen.
    IF TG_TABLE_NAME = 'room_opening' THEN
        v_room       := NEW.room_id;
        v_surface_id := NEW.surface_id;
        v_skip_open  := NEW.id;
        v_add_area   := round(NEW.quantity * NEW.width_m * NEW.height_m, 3);
    ELSIF TG_OP = 'DELETE' THEN            -- room_surface DELETE
        v_room := OLD.room_id;
        v_drop := OLD.id;
    ELSIF TG_OP = 'INSERT' THEN            -- room_surface INSERT (neu, 0095)
        -- Die Zeile steht noch nicht in der Tabelle: ihre Fläche wird Σ Bauteil-
        -- flächen HINZUGERECHNET. Grenze (a) greift hier nicht (v_surface_id
        -- bleibt NULL) — eine neue Wand hat noch keine Öffnungen.
        v_room     := NEW.room_id;
        v_add_wand := NEW.gross_area_m2;
    ELSE                                   -- room_surface UPDATE OF gross_area_m2
        v_room       := NEW.room_id;
        v_surface_id := NEW.id;
        v_neu_id     := NEW.id;
        v_neu_gross  := NEW.gross_area_m2;
    END IF;

    -- Serialisierungspunkt ist die RAUMzeile, nicht die Wand: Grenze (b) spannt
    -- über alle Wände des Raumes, zwei gleichzeitige Schreibvorgänge an
    -- VERSCHIEDENEN Wänden wären über eine Wandsperre nicht serialisiert.
    -- READ COMMITTED gibt jeder folgenden Anweisung einen frischen Snapshot,
    -- sobald die Sperre erteilt ist.
    PERFORM 1 FROM property.room WHERE id = v_room FOR UPDATE;

    -- (a) je Wand — nur wenn eine bestehende Wand betroffen ist.
    IF v_surface_id IS NOT NULL THEN
        SELECT gross_area_m2, coalesce(label, surface_type)
          INTO v_gross, v_label
          FROM property.room_surface
         WHERE id = v_surface_id;

        IF FOUND THEN
            IF v_neu_id IS NOT NULL THEN
                v_gross := v_neu_gross;   -- die NEUE Bruttofläche gilt
            END IF;

            SELECT coalesce(sum(area_m2), 0) INTO v_belegt
              FROM property.room_opening
             WHERE surface_id = v_surface_id
               AND (v_skip_open IS NULL OR id <> v_skip_open);

            IF TG_TABLE_NAME = 'room_opening' THEN
                v_belegt := v_belegt + v_add_area;
            END IF;

            IF v_belegt > v_gross THEN
                RAISE EXCEPTION
                    'Die Öffnungen (% m²) sind größer als die Fläche „%" (% m²).',
                    v_belegt, v_label, v_gross
                    USING ERRCODE = 'check_violation',
                          CONSTRAINT = 'room_opening_passt_in_flaeche';
            END IF;
        END IF;
    END IF;

    -- (b) je Raum — fängt die Öffnungen OHNE Wandzuordnung, das Löschen einer Wand,
    -- unter der freie Öffnungen liegen, UND das Anlegen der ersten Wand unter
    -- einer bereits erfassten freien Öffnung (0095).
    SELECT coalesce(sum(
               CASE WHEN v_neu_id IS NOT NULL AND id = v_neu_id
                    THEN v_neu_gross
                    ELSE gross_area_m2 END), 0)
      INTO v_summe_wand
      FROM property.room_surface
     WHERE room_id = v_room
       AND (v_drop IS NULL OR id <> v_drop);

    IF v_add_wand IS NOT NULL THEN
        v_summe_wand := v_summe_wand + v_add_wand;
    END IF;

    -- Kein Bauteil aufgenommen ⇒ die Wandfläche ist UNBEKANNT, nicht 0. Dann gibt
    -- es keine Grenze, gegen die man prüfen könnte — Fenster dürfen vor den
    -- Wänden erfasst werden. (Sobald eine Wand entsteht, zählt sie hier mit: der
    -- Ausstieg deckt den INSERT der ersten Wand NICHT mehr zu.)
    IF v_summe_wand = 0 THEN
        IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
    END IF;

    SELECT coalesce(sum(area_m2), 0) INTO v_summe_oeff
      FROM property.room_opening
     WHERE room_id = v_room
       AND (v_skip_open IS NULL OR id <> v_skip_open);

    IF TG_TABLE_NAME = 'room_opening' THEN
        v_summe_oeff := v_summe_oeff + v_add_area;
    END IF;

    IF v_summe_oeff > v_summe_wand THEN
        RAISE EXCEPTION
            'Die Öffnungen des Raumes (% m²) sind größer als seine Bauteilflächen (% m²) — '
            'die Nettowandfläche wäre negativ.',
            v_summe_oeff, v_summe_wand
            USING ERRCODE = 'check_violation',
                  CONSTRAINT = 'room_opening_passt_in_flaeche';
    END IF;

    IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END;
$$;

-- Die Lücke: Bisher feuerte auf room_surface NUR bei UPDATE OF gross_area_m2 und
-- bei DELETE ein Trigger. Das ANLEGEN einer Wand blieb ungeprüft.
CREATE TRIGGER trg_room_surface_einfuegen_passt
    BEFORE INSERT ON property.room_surface
    FOR EACH ROW EXECUTE FUNCTION property.enforce_room_opening_fits();
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS trg_room_surface_einfuegen_passt ON property.room_surface;

-- Zurück auf die Fassung aus 0089 (ohne INSERT-Zweig).
CREATE OR REPLACE FUNCTION property.enforce_room_opening_fits() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_room       uuid;
    v_surface_id uuid;
    v_drop       uuid;
    v_skip_open  uuid;
    v_add_area   numeric;
    v_neu_id     uuid;
    v_neu_gross  numeric;
    v_gross      numeric;
    v_belegt     numeric;
    v_label      text;
    v_summe_wand numeric;
    v_summe_oeff numeric;
BEGIN
    IF TG_TABLE_NAME = 'room_opening' THEN
        v_room       := NEW.room_id;
        v_surface_id := NEW.surface_id;
        v_skip_open  := NEW.id;
        v_add_area   := round(NEW.quantity * NEW.width_m * NEW.height_m, 3);
    ELSIF TG_OP = 'DELETE' THEN
        v_room := OLD.room_id;
        v_drop := OLD.id;
    ELSE
        v_room       := NEW.room_id;
        v_surface_id := NEW.id;
        v_neu_id     := NEW.id;
        v_neu_gross  := NEW.gross_area_m2;
    END IF;

    PERFORM 1 FROM property.room WHERE id = v_room FOR UPDATE;

    IF v_surface_id IS NOT NULL THEN
        SELECT gross_area_m2, coalesce(label, surface_type)
          INTO v_gross, v_label
          FROM property.room_surface
         WHERE id = v_surface_id;

        IF FOUND THEN
            IF v_neu_id IS NOT NULL THEN
                v_gross := v_neu_gross;
            END IF;

            SELECT coalesce(sum(area_m2), 0) INTO v_belegt
              FROM property.room_opening
             WHERE surface_id = v_surface_id
               AND (v_skip_open IS NULL OR id <> v_skip_open);

            IF TG_TABLE_NAME = 'room_opening' THEN
                v_belegt := v_belegt + v_add_area;
            END IF;

            IF v_belegt > v_gross THEN
                RAISE EXCEPTION
                    'Die Öffnungen (% m²) sind größer als die Fläche „%" (% m²).',
                    v_belegt, v_label, v_gross
                    USING ERRCODE = 'check_violation',
                          CONSTRAINT = 'room_opening_passt_in_flaeche';
            END IF;
        END IF;
    END IF;

    SELECT coalesce(sum(
               CASE WHEN v_neu_id IS NOT NULL AND id = v_neu_id
                    THEN v_neu_gross
                    ELSE gross_area_m2 END), 0)
      INTO v_summe_wand
      FROM property.room_surface
     WHERE room_id = v_room
       AND (v_drop IS NULL OR id <> v_drop);

    IF v_summe_wand = 0 THEN
        IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
    END IF;

    SELECT coalesce(sum(area_m2), 0) INTO v_summe_oeff
      FROM property.room_opening
     WHERE room_id = v_room
       AND (v_skip_open IS NULL OR id <> v_skip_open);

    IF TG_TABLE_NAME = 'room_opening' THEN
        v_summe_oeff := v_summe_oeff + v_add_area;
    END IF;

    IF v_summe_oeff > v_summe_wand THEN
        RAISE EXCEPTION
            'Die Öffnungen des Raumes (% m²) sind größer als seine Bauteilflächen (% m²) — '
            'die Nettowandfläche wäre negativ.',
            v_summe_oeff, v_summe_wand
            USING ERRCODE = 'check_violation',
                  CONSTRAINT = 'room_opening_passt_in_flaeche';
    END IF;

    IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END;
$$;
"""


class Migration(migrations.Migration):

    dependencies = [("db_core", "0094_kante_nur_an_der_wand")]

    operations = [migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL)]
