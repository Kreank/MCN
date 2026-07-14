"""Rechtematrix: Der Monteur sieht sein ganzes Objekt.

## Der Anlass (fachlich, nicht technisch)

Ein Mieter meldet „Heizkörper kalt". Zwei Tage vorher hat bei einem anderen Mieter
desselben Hauses ein Heizkörper geleckt und musste getauscht werden; im Objekt
steht eine Zentralanlage. **Das sind die Informationen, ohne die der Monteur
zweimal fährt.** Bis hierher sah er sie nicht: Die Startmatrix (0026) gibt der
Rolle MONTEUR Rechte nur auf `workflow` und `content` — Liegenschaft, Beteiligte
und Objekthistorie waren 403.

## Was diese Migration ändert

Zwei Zellen-Gruppen der bestehenden Matrix werden auf `allowed = true` gesetzt
(`row_scope` steht für MONTEUR ohnehin schon durchgängig auf `'EIGENE'`, siehe
0026 Z. 115 — er wird hier trotzdem explizit gesetzt, damit die Zeile für sich
allein lesbar ist):

| Modul | Aktionen | Warum |
|---|---|---|
| `property` | LESEN, ANLEGEN, AENDERN | Objekt sehen; Räume/Anlagen/Gebäude an **seinen** Objekten erfassen |
| `identity` | LESEN | Er muss den Mieter anrufen können, der die Meldung gemacht hat |

**`row_scope = 'EIGENE'` heißt hier: „meine Objekte"** — die Liegenschaften, an
denen er je einen Einsatz hatte. Die Regel steht an genau einer Stelle im Code:
`db_core/services/objektsicht.py`. Ohne den dortigen Filter wäre dieses
`allowed = true` ein Vollzugriff; die Matrix allein begrenzt nichts.

## Was BEWUSST unverändert bleibt

* **`invoicing` und `pricing` bleiben `false`.** Angebote, Rechnungen und Preise
  sieht der Monteur nicht — auch nicht an seinem eigenen Objekt. „Angebot ohne
  Preise, nur Mengen" ist ein **eigener, späterer** Slice (Entscheidung des Users);
  eine halbe Preisunterdrückung wäre schlimmer als keine. Weil das Recht schlicht
  fehlt, gibt es hier nichts zu filtern und nichts zu vergessen — fail-closed durch
  Abwesenheit.
* **`maintenance` bleibt `false`** (0071): Fälligkeitsplanung ist kein
  Monteurs-Arbeitsbereich; das Ergebnis erreicht ihn als Einsatz/Aufgabe.
* **`workflow` und `content`** behalten ihre bestehenden Zellen (LESEN/ANLEGEN/
  AENDERN bzw. LESEN/ANLEGEN, je `EIGENE`). Die Objektsicht macht daraus **lesend**
  mehr (Vorgänge/Aufträge/Berichte an meinen Objekten); die **Schreibpfade** bleiben
  unverändert eng (eigener Einsatz, eigener Bericht) — das entscheidet der Code, den
  diese Zellen tragen, nicht die Matrix.
* **`property/ANLEGEN` erlaubt keine neue Liegenschaft.** `POST /properties` bleibt
  auf `require` (fail-closed → 403 bei EIGENE): Wer noch kein Objekt hat, hat auch
  keins, an dem er etwas anlegen dürfte — ein Monteur, der Objekte erfindet, hätte
  sich seine eigene Sichtbarkeit gebaut. Erlaubt sind Anlagen **an** meinen Objekten
  (Räume, Gebäude, Einheiten, technische Anlagen).

## Präzedenzfall

Dies ist das **erste UPDATE bestehender Matrixzellen** (bisher gab es nur INSERTs
für neue Module: 0021 `hr`, 0071 `maintenance`). Deshalb eng gefasst: WHERE auf
Rolle + Modul + Aktionsliste, kein `IN`-Sammelupdate über Module hinweg, und ein
`reverse_sql`, das exakt dieselben Zellen wieder schließt (`allowed = false`;
`row_scope` bleibt auf `'EIGENE'` — genau so stand er vorher).
"""
from django.db import migrations

CREATE_SQL = r"""
UPDATE security.role_permission
SET allowed = true, row_scope = 'EIGENE'
WHERE role_code = 'MONTEUR'
  AND (
        (module = 'property' AND action IN ('LESEN', 'ANLEGEN', 'AENDERN'))
     OR (module = 'identity' AND action = 'LESEN')
  );
"""

DROP_SQL = r"""
UPDATE security.role_permission
SET allowed = false, row_scope = 'EIGENE'
WHERE role_code = 'MONTEUR'
  AND (
        (module = 'property' AND action IN ('LESEN', 'ANLEGEN', 'AENDERN'))
     OR (module = 'identity' AND action = 'LESEN')
  );
"""


class Migration(migrations.Migration):

    dependencies = [("db_core", "0098_suche_normalisiert_trigramm")]

    operations = [
        # reverse_sql: reine Stammdatenpflege (security.role_permission), keine
        # Fachdaten. Der Rückweg stellt exakt den Zustand vor dieser Migration her.
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
    ]
