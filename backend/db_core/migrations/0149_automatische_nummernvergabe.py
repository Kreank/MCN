"""Automatische Nummernvergabe für Artikel, Leistungen, Gebäude und Einheiten.

**Das Problem.** Vier sichtbare Nummern mussten bisher von Hand getippt werden:
`pricing.article.article_number`, `pricing.assembly.assembly_number`,
`property.building.building_number` und `property.unit.unit_number`. Der
UNIQUE-Constraint verhindert zwar echte Doppelvergabe — aber wenn drei Leute
gleichzeitig erfassen, bekommt der Zweite einen IntegrityError ins Gesicht und
tippt neu, mitten im Angebot. Jede andere Nummer im System (OBJ-, MA-, RES-,
EB-, V-, AU-, RE-) wird längst von der Datenbank vergeben; diese vier waren die
letzten Handarbeitsplätze.

**Der Ansatz: leer heißt automatisch.** Die Nummer bleibt eingebbar. Wer eine
sprechende Nummer braucht — „Hinterhaus", „WE 12 links" — trägt sie ein und
behält sie. Wer nichts einträgt, bekommt die nächste freie. Der entscheidende
Punkt ist das *Wann*: Die Vergabe passiert im BEFORE-INSERT-Trigger, also im
Schreibmoment, nicht beim Öffnen der Maske. Damit gibt es kein Rennen — zwei
gleichzeitige Erfasser ziehen zwei verschiedene Nummern, ohne voneinander zu
wissen. Ein in der Maske vorbelegter Vorschlag hätte das Problem nur verschoben.

Der Leerstring zählt dabei wie NULL als „nicht gesetzt": Die ORM schickt für ein
unbelegtes TextField '' statt NULL (dieselbe Überlegung wie in Migration 0120).

**Warum eigene Sequenzen statt `workflow.next_number()`.** Artikel und
Leistungen sind Stammdaten, keine Belege. Sie gehören in keinen GoBD-Belegkreis
und brauchen keine Jahresgrenze — ein Artikel lebt über Jahre. Muster ist
deshalb `property.property_number_seq` (OBJ-#####), nicht der Belegkreis.

**Warum `lpad` hier eine Fallunterscheidung braucht.** `lpad(x, 5, '0')`
schneidet RECHTS ab, sobald `x` länger als fünf Zeichen ist: Aus 100000 würde
'10000' — dieselbe Nummer wie 10000. Oberhalb der Polsterbreite wird deshalb
ungepolstert weitergezählt (WF-03, wie in `workflow.next_number`). Die
bestehenden Sequenzen OBJ-/MA-/RES-/EB- tragen diese Falle noch; hier wird sie
nicht wiederholt.

**Warum die Trigger den Bestand prüfen.** Trägt jemand von Hand „ART-00007" ein,
läuft die Sequenz später genau dort hinein. Der Trigger zieht dann die nächste
freie Nummer weiter, statt den Anlegevorgang mit einem UNIQUE-Verstoß
abzubrechen. Der Constraint bleibt der letzte Wächter, ist aber nicht mehr der
erste Kontakt des Nutzers mit dem Problem.

**Warum Gebäude/Einheit anders funktionieren.** Ihre Nummern sind nicht global,
sondern **je Liegenschaft** eindeutig (Beschluss A-09, `UNIQUE (property_id,
building_number)` bzw. `(property_id, unit_number)`). Eine globale Sequenz taugt
dafür nicht — gezählt wird der Bestand der jeweiligen Liegenschaft. Weil zwei
gleichzeitige Anlagen an DERSELBEN Liegenschaft sonst beide dasselbe `max()+1`
läsen, sperrt der Trigger vorher auf die Liegenschaft (`pg_advisory_xact_lock`,
endet mit der Transaktion). Die Sperre gilt nur für diese eine Liegenschaft —
Anlagen an anderen Objekten laufen ungehindert weiter.

Einheiten zählen dabei über **alle Gebäude einer Liegenschaft hinweg** durch
(01, 02, 03 …), nicht je Gebäude neu. Das ist keine Designwahl, sondern folgt
aus A-09: eine je Gebäude neu startende Nummer verstieße gegen
`UNIQUE (property_id, unit_number)`.

**Importe bleiben unberührt.** DATANORM legt Artikel mit `DN-<Namespace>-<Nr>`
an, IDS-Connect ebenso — beides gesetzte Nummern, die der Trigger unverändert
durchlässt. Der neue Kreis ART-##### kollidiert damit nicht. Für den Import-Pfad
(Vollkataloge mit Millionen Zeilen) ist der Trigger bewusst ohne dynamisches SQL
geschrieben: Der Normalfall „Nummer ist gesetzt" kostet einen Feldvergleich.
"""
from django.db import migrations


CREATE_SQL = r"""
-- ---------------------------------------------------------------------------
-- 1. Artikel und Leistungen: eigene Sequenzen (kein GoBD-Belegkreis)
-- ---------------------------------------------------------------------------
CREATE SEQUENCE pricing.article_number_seq;
CREATE SEQUENCE pricing.assembly_number_seq;

COMMENT ON SEQUENCE pricing.article_number_seq IS
    'Zaehler fuer automatisch vergebene Artikelnummern (ART-#####). Kein Belegkreis: Artikel sind Stammdaten, nicht GoBD-pflichtige Belege.';
COMMENT ON SEQUENCE pricing.assembly_number_seq IS
    'Zaehler fuer automatisch vergebene Leistungsnummern (LEI-#####). Siehe article_number_seq.';

CREATE FUNCTION pricing.assign_article_number() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_value    bigint;
    v_nummer   text;
    v_versuche integer := 0;
BEGIN
    -- Gesetzte Nummer gewinnt. Der Leerstring zaehlt als „nicht gesetzt": ohne
    -- db_default schickt die ORM fuer ein unbelegtes TextField '' statt NULL.
    -- Dieser Zweig ist der heisse Pfad des DATANORM-Imports (Millionen Zeilen)
    -- und deshalb bewusst ein einfacher Feldvergleich ohne dynamisches SQL.
    IF NEW.article_number IS NOT NULL AND btrim(NEW.article_number) <> '' THEN
        RETURN NEW;
    END IF;
    LOOP
        v_versuche := v_versuche + 1;
        v_value := nextval('pricing.article_number_seq');
        -- lpad trunkiert RECHTS: aus 100000 wuerde '10000' und damit eine
        -- bereits vergebene Nummer. Oberhalb der Polsterbreite ungepolstert
        -- weiterzaehlen (WF-03, wie workflow.next_number).
        v_nummer := 'ART-' || CASE WHEN v_value < 100000
                                   THEN lpad(v_value::text, 5, '0')
                                   ELSE v_value::text END;
        EXIT WHEN NOT EXISTS (
            SELECT 1 FROM pricing.article WHERE article_number = v_nummer
        );
        -- Von Hand vergebene ART-Nummern koennen dem Zaehler im Weg stehen;
        -- dann die naechste freie ziehen statt den Anlegevorgang abzubrechen.
        IF v_versuche >= 1000 THEN
            RAISE EXCEPTION 'Keine freie Artikelnummer nach % Versuchen gefunden', v_versuche;
        END IF;
    END LOOP;
    NEW.article_number := v_nummer;
    RETURN NEW;
END;
$$;

CREATE FUNCTION pricing.assign_assembly_number() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_value    bigint;
    v_nummer   text;
    v_versuche integer := 0;
BEGIN
    IF NEW.assembly_number IS NOT NULL AND btrim(NEW.assembly_number) <> '' THEN
        RETURN NEW;
    END IF;
    LOOP
        v_versuche := v_versuche + 1;
        v_value := nextval('pricing.assembly_number_seq');
        v_nummer := 'LEI-' || CASE WHEN v_value < 100000
                                   THEN lpad(v_value::text, 5, '0')
                                   ELSE v_value::text END;
        EXIT WHEN NOT EXISTS (
            SELECT 1 FROM pricing.assembly WHERE assembly_number = v_nummer
        );
        IF v_versuche >= 1000 THEN
            RAISE EXCEPTION 'Keine freie Leistungsnummer nach % Versuchen gefunden', v_versuche;
        END IF;
    END LOOP;
    NEW.assembly_number := v_nummer;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_article_number
    BEFORE INSERT ON pricing.article
    FOR EACH ROW EXECUTE FUNCTION pricing.assign_article_number();
CREATE TRIGGER trg_assembly_number
    BEFORE INSERT ON pricing.assembly
    FOR EACH ROW EXECUTE FUNCTION pricing.assign_assembly_number();

-- Die Spalten bleiben NOT NULL. NOT NULL wird NACH den BEFORE-Triggern geprueft,
-- ein NULL aus der Anwendung ist zu diesem Zeitpunkt also laengst ersetzt.

-- ---------------------------------------------------------------------------
-- 2. Gebaeude und Einheiten: je Liegenschaft hochzaehlen (A-09)
-- ---------------------------------------------------------------------------
CREATE FUNCTION property.assign_building_number() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_next integer;
BEGIN
    IF NEW.building_number IS NOT NULL AND btrim(NEW.building_number) <> '' THEN
        RETURN NEW;
    END IF;
    -- Ohne Sperre laesen zwei gleichzeitige Anlagen an DERSELBEN Liegenschaft
    -- dasselbe max()+1. Gesperrt wird nur diese Liegenschaft; die Sperre endet
    -- mit der Transaktion.
    PERFORM pg_advisory_xact_lock(
        hashtext('property.building'), hashtext(NEW.property_id::text)
    );
    -- Nur rein numerische Bestandsnummern zaehlen mit: „Hinterhaus" laesst sich
    -- nicht hochzaehlen. Die Laengengrenze haelt den ::integer-Cast im Rahmen.
    SELECT coalesce(max(building_number::integer), 0) + 1 INTO v_next
      FROM property.building
     WHERE property_id = NEW.property_id
       AND building_number ~ '^[0-9]{1,9}$';
    NEW.building_number := v_next::text;
    RETURN NEW;
END;
$$;

CREATE FUNCTION property.assign_unit_number() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_next integer;
BEGIN
    IF NEW.unit_number IS NOT NULL AND btrim(NEW.unit_number) <> '' THEN
        RETURN NEW;
    END IF;
    PERFORM pg_advisory_xact_lock(
        hashtext('property.unit'), hashtext(NEW.property_id::text)
    );
    -- Gezaehlt wird je LIEGENSCHAFT, nicht je Gebaeude: A-09 verlangt
    -- UNIQUE (property_id, unit_number). Ein je Gebaeude neu startender Zaehler
    -- liefe im zweiten Gebaeude sofort in den Constraint.
    SELECT coalesce(max(unit_number::integer), 0) + 1 INTO v_next
      FROM property.unit
     WHERE property_id = NEW.property_id
       AND unit_number ~ '^[0-9]{1,9}$';
    NEW.unit_number := CASE WHEN v_next < 100
                            THEN lpad(v_next::text, 2, '0')
                            ELSE v_next::text END;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_building_number
    BEFORE INSERT ON property.building
    FOR EACH ROW EXECUTE FUNCTION property.assign_building_number();
CREATE TRIGGER trg_unit_number
    BEFORE INSERT ON property.unit
    FOR EACH ROW EXECUTE FUNCTION property.assign_unit_number();
"""

DROP_SQL = r"""
DROP TRIGGER IF EXISTS trg_unit_number ON property.unit;
DROP TRIGGER IF EXISTS trg_building_number ON property.building;
DROP FUNCTION IF EXISTS property.assign_unit_number();
DROP FUNCTION IF EXISTS property.assign_building_number();

DROP TRIGGER IF EXISTS trg_assembly_number ON pricing.assembly;
DROP TRIGGER IF EXISTS trg_article_number ON pricing.article;
DROP FUNCTION IF EXISTS pricing.assign_assembly_number();
DROP FUNCTION IF EXISTS pricing.assign_article_number();

DROP SEQUENCE IF EXISTS pricing.assembly_number_seq;
DROP SEQUENCE IF EXISTS pricing.article_number_seq;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0148_arbeitszeitfenster"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
    ]
