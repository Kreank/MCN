"""Trigramm-Indizes für die Artikelsuche über Nummer und Matchcode.

Die Artikelsuche (Hero-Operatoren `+`/`|`/`*`) verodert die Textfelder
`article_number`, `description` und `matchcode`. Trigramm-GIN-Indizes gab es
bisher nur auf `description`/`long_description` (0038). Ein OR-Zweig ohne
passenden Index zwingt den Planner zum Seq-Scan der GESAMTEN Tabelle —
bei 2,3 Mio Artikeln scannt damit jede Suche alles, inklusive `count()`.

Ein einfacher btree (`idx_article_matchcode`, `article_article_number_key`)
hilft hier nicht: er trägt nur exakte/Präfix-Vergleiche, nicht `ILIKE '%…%'`
oder `~*`. Substring-/Wildcard-Suche braucht `gin_trgm_ops`.

Produktionshinweis: Der Aufbau eines GIN-Index auf 2,3 Mio Zeilen sperrt in
dieser transaktionalen Migration kurzzeitig Schreibzugriffe. Beim Rollout auf
eine bereits große Produktions-DB stattdessen `CREATE INDEX CONCURRENTLY`
außerhalb der Transaktion fahren; das etablierte Migrationsmuster hier
(BEGIN/COMMIT wie 0038) ist für den Erstaufbau bewusst transaktional.
"""
from django.db import migrations

FORWARD_SQL = r"""
CREATE INDEX IF NOT EXISTS idx_article_number_trgm
    ON pricing.article USING gin (article_number gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_article_matchcode_trgm
    ON pricing.article USING gin (matchcode gin_trgm_ops)
    WHERE matchcode IS NOT NULL;
"""

REVERSE_SQL = r"""
DROP INDEX IF EXISTS pricing.idx_article_matchcode_trgm;
DROP INDEX IF EXISTS pricing.idx_article_number_trgm;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0042_artikel_hero_paritaet"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
