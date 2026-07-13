"""Die Auftragszuordnung eines Angebots bleibt auch nach dem Versand setzbar.

## Der Fehler, den diese Migration behebt

`invoicing.freeze_sent_quote` (0018, B-30) friert ab Status VERSENDET die **ganze
Angebotszeile** ein; ausgenommen waren nur `status`, `replaced_by_quote_id` und
`updated_at`. Damit fiel auch `work_order_id` unter die Einfrierung — die
Zuordnung „dieses Angebot ist das Soll dieser Baustelle" war **nur bis zum
Versand** setzbar.

Das ist an der Wirklichkeit vorbei. Der reale Ablauf lautet:

    Angebot schreiben → versenden → Kunde nimmt an → **dann** Auftrag anlegen

Zum Zeitpunkt der Auftragsanlage steht das Angebot auf VERSENDET oder
ANGENOMMEN — die Zuordnung wäre also genau dann gesperrt, wenn man sie braucht.
Erschwerend: der Soll-Filter des Abgleichs (`SOLL_AUSGESCHLOSSENE_STATUS`)
schließt ENTWURF/INTERN_GEPRUEFT aus. Das Zeitfenster, in dem ein Angebot
zuordenbar war, und das Zeitfenster, in dem es ins Soll zählt, überschnitten sich
damit fast nicht — der Soll-Ist-Abgleich blieb in der Praxis leer.

## Warum `work_order_id` zu Recht eine Ausnahme ist

* Sie ist ein **interner Verweis, kein Beleginhalt**. Sie ändert weder Betrag noch
  Position noch Steuer noch das Sichtbild des Kundendokuments (der
  `billing_snapshot` und der `content_hash` bleiben unberührt) — sie ordnet den
  Beleg betriebsintern einem Auftrag zu.
* Sie steht damit auf einer Stufe mit den bereits ausgenommenen `status` und
  `replaced_by_quote_id` (Lebenszyklus- und Verweisfelder), **nicht** mit Preis,
  Menge oder Text.
* Der **zusammengesetzte FK** `(work_order_id, property_id) → work_order
  (id, property_id)` (0018/P3-12) bleibt in Kraft: ein Angebot lässt sich weiterhin
  nicht an einen Auftrag einer **fremden Liegenschaft** hängen.
* Die Änderung wird durch `audit.audit_row_update` **auditiert** — sie ist
  nachvollziehbar, nicht heimlich.

## B-30 bleibt im Kern unangetastet

Positionen (`invoicing.protect_quote_lines`), Abschnitte, Beträge, Texte, Daten,
`version` und der Snapshot eines versendeten Angebots sind weiterhin
unveränderlich. Ausgenommen sind ausschließlich die vier genannten Spalten. Der
Regelblock zu `replaced_by_quote_id` (P3-08) ist unverändert übernommen.

Rückwärts: die alte Funktionsfassung wird wiederhergestellt (die Funktion bleibt
also in beiden Richtungen vorhanden — Muster 0064). Ein Rückbau macht bestehende
Zuordnungen nicht rückgängig; er sperrt nur künftige Änderungen wieder.
"""
from django.db import migrations

FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION invoicing.freeze_sent_quote() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- P3-08: Der Nachfolgeverweis darf nur zusammen mit dem Übergang nach ERSETZT
    -- gesetzt werden und ist danach unveränderlich.
    IF NEW.replaced_by_quote_id IS DISTINCT FROM OLD.replaced_by_quote_id THEN
        IF NOT (NEW.status = 'ERSETZT' AND OLD.status <> 'ERSETZT'
                AND OLD.replaced_by_quote_id IS NULL) THEN
            RAISE EXCEPTION
                'Angebot %: Nachfolgeverweis nur beim Übergang nach ERSETZT setzbar und danach unveränderlich (B-30/P3-08)',
                OLD.id;
        END IF;
    END IF;

    IF OLD.status IN ('VERSENDET', 'ANGENOMMEN', 'ABGELEHNT', 'ABGELAUFEN', 'ERSETZT') THEN
        -- P3-09: version gehört NICHT zu den Ausnahmen — nach Versand eingefroren.
        -- work_order_id dagegen schon (0080): interner Verweis, kein Beleginhalt.
        -- Der zusammengesetzte FK (work_order_id, property_id) hält weiterhin die
        -- Liegenschaft zusammen; die Änderung wird auditiert.
        IF (to_jsonb(NEW) - 'status' - 'replaced_by_quote_id' - 'work_order_id' - 'updated_at')
           IS DISTINCT FROM
           (to_jsonb(OLD) - 'status' - 'replaced_by_quote_id' - 'work_order_id' - 'updated_at') THEN
            RAISE EXCEPTION
                'Angebot %: Inhalt ist nach Versand unveränderlich (B-30); Ersatzangebot verwenden', OLD.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON COLUMN invoicing.quote.work_order_id IS
    'Auftragszuordnung: die Aussage „dieses Angebot ist das Soll dieser Baustelle". '
    'Interner Verweis, kein Beleginhalt — deshalb auch nach dem Versand änderbar '
    '(0080), auditiert. Die Liegenschaft bleibt durch den zusammengesetzten FK '
    '(work_order_id, property_id) erzwungen.';
"""

REVERSE_SQL = r"""
CREATE OR REPLACE FUNCTION invoicing.freeze_sent_quote() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.replaced_by_quote_id IS DISTINCT FROM OLD.replaced_by_quote_id THEN
        IF NOT (NEW.status = 'ERSETZT' AND OLD.status <> 'ERSETZT'
                AND OLD.replaced_by_quote_id IS NULL) THEN
            RAISE EXCEPTION
                'Angebot %: Nachfolgeverweis nur beim Übergang nach ERSETZT setzbar und danach unveränderlich (B-30/P3-08)',
                OLD.id;
        END IF;
    END IF;

    IF OLD.status IN ('VERSENDET', 'ANGENOMMEN', 'ABGELEHNT', 'ABGELAUFEN', 'ERSETZT') THEN
        IF (to_jsonb(NEW) - 'status' - 'replaced_by_quote_id' - 'updated_at')
           IS DISTINCT FROM
           (to_jsonb(OLD) - 'status' - 'replaced_by_quote_id' - 'updated_at') THEN
            RAISE EXCEPTION
                'Angebot %: Inhalt ist nach Versand unveränderlich (B-30); Ersatzangebot verwenden', OLD.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON COLUMN invoicing.quote.work_order_id IS NULL;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0081_sitereportline"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
