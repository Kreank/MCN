# KI-Assistent: Befunde und Umsetzungsplan

**Stand:** 2026-07-22 · **Status:** Entscheidungsgrundlage, nichts davon ist umgesetzt
**Anlass:** Enttäuschender Test des KI-Assistenten im Disponenten-Betrieb
**Nächster Schritt:** Besprechung mit Sascha, dann Entscheidung über die Reihenfolge

---

## 0. Worum es geht

Zwei Testfragen an den Assistenten, beide unbefriedigend beantwortet:

> **F:** Wie viele Aufträge existieren für die Liegenschaft WEG Wartuburgstr. 52?
> **A:** Im übergebenen Kontext sind keine Aufträge für die Liegenschaft WEG Wartuburgstr. 52
> aufgeführt, sodass eine genaue Anzahl nicht bestimmt werden kann.

> **F:** WEG Wartubrugstr. 52: wann war der letzte Termin?
> **A:** Im übergebenen Kontext sind keine Informationen zu Aufträgen oder Terminen für die
> Liegenschaft WEG Wartuburgstr. 52 enthalten.

Obwohl Auftrag und Termin existieren. Die Sorge dahinter ist berechtigt und größer als der
Einzelfall: **Wenn das System schon an einer Suchanfrage scheitert, was passiert, wenn die KI
Daten anlegen darf?**

Dieses Dokument hält fest, was die Ursachenanalyse ergeben hat, was drei Recherche-Läufe
belegen konnten (und was nicht), was im eigenen Ökosystem bereits an Vorarbeit existiert, und
welche Umsetzung daraus folgt.

---

## 1. Die Diagnose — reproduziert, nicht vermutet

Alle Aussagen dieses Abschnitts sind am laufenden System nachgespielt worden (rein lesend).

### 1.1 Die eigentliche Ursache: ein Tippfehler, gegen den die Suche wehrlos ist

Die Liegenschaft heißt in der Datenbank **`WEG Wartburgstr 52`**. Getippt wurde
`Wartuburgstr.` bzw. `Wartubrugstr.`. Die globale Suche macht reines Substring-Matching auf
normalisierter Form — **keinerlei Fuzzy-Toleranz für Entitäten**. Ein Buchstabe daneben = null
Treffer.

Nachgespielt über `assistent._suchtreffer()`:

| Frage | Suchstufen | Endtreffer |
|---|---|---|
| `…WEG Wartuburgstr. 52?` | 3 Stufen, alle 0 roh | **[]** |
| `…WEG Wartburgstr. 52?` | Stufe 1+2 → 0, Stufe 3 → 6 roh | LIEGENSCHAFT + AUFTRAG |
| `Wartubrugstr. 52: letzter Termin?` | 3 Stufen, alle 0 roh | **[]** |
| `Wartburgstr 52: letzter Termin?` | Stufe 1+2 → 0, Stufe 3 → 6 roh | EINSATZ E-26-0002 |

Der Assistent hat also **korrekt** geantwortet — er hatte tatsächlich einen leeren Kontext.
Sein Fehler ist ein anderer, und er ist der eigentlich enttäuschende:

> Er sagt „steht nicht im Kontext", statt „diese Liegenschaft kenne ich nicht — meintest du
> *WEG Wartburgstr 52*?"

Für den Nutzer sieht **„Objekt existiert nicht"** genauso aus wie **„Objekt existiert, aber
keine Daten"**. Diese Ununterscheidbarkeit ist der Kern der Enttäuschung, nicht die fehlende
Antwort.

### 1.2 Die Floskel-Liste ist zu schwach — und rettet nur zufällig

Selbst bei **korrekter** Schreibweise liefern die Suchstufen 1 und 2 null Treffer.
`_FLOSKELN` (assistent.py:257) enthält `wie`, aber nicht `viele`, nicht `existieren`, nicht
`letzte`. Diese bleiben als UND-verknüpfte Pflicht-Tokens stehen.

Gerettet wird die Anfrage nur von Stufe 3 (`_suchbegriffe`, assistent.py:388-395), die auf
„längstes Token + alle Zifferntokens" reduziert. Das funktioniert hier **nur, weil
`Wartburgstr.` zufällig länger ist als `existieren`** (13 gegen 10 Zeichen normalisiert). Bei
einem kürzeren Straßennamen kippt es.

Eine handgepflegte Stoppwortliste ist strukturell die falsche Antwort: Sie muss vollständig
sein, um zu funktionieren, und Vollständigkeit ist bei natürlicher Sprache nicht erreichbar.

### 1.3 Termin-Fragen sind strukturell unbeantwortbar

Das Wort „Termin" setzt über `_TYPWOERTER` (assistent.py:291) den Typfilter auf `EINSATZ`.
`_passende_treffer()` wirft daraufhin die Liegenschaft aus der Trefferliste. Und `EINSATZ`
steht **nicht** in `_DOSSIER_TYPEN` (assistent.py:117) — es wird also gar kein Dossier
montiert. Der gesamte Kontext ist dann eine Zeile Fließtext:

```json
{"typ": "EINSATZ", "titel": "Waschtisch austauschen",
 "info": "E-26-0002 · AU-26-0002 · Wartburgstr. 52, 10823 Berlin · 23.07.2026 07:00 · BESTAETIGT"}
```

Das Datum steht nur im Untertitel — Zufall, nicht Konstruktion. Zusätzlich: Das
Liegenschafts-Dossier führt `anlagen`, `faelligkeiten`, `vorgaenge`, `auftraege` — **`einsaetze`
nicht**, obwohl `dossier.liegenschaft_dossier()` sie liefert (dossier.py:799-807).

Nebenbefund zur Datenlage: Der einzige Einsatz an diesem Objekt liegt am **23.07.2026 in der
Zukunft**. Die fachlich richtige Antwort auf „wann *war* der letzte Termin" wäre gewesen:
*„Noch keiner — aber am 23.07. ist einer geplant."* Ein System, das Vergangenheit und Zukunft
nicht trennt, kann diese Antwort gar nicht formulieren.

### 1.4 „Wie viele …" trifft die falsche Kennzahl-Quelle

Das UI zeigte beim ersten Test den Intent **`Kennzahl`**. Dieser Intent füllt
`_kennzahlen()` (assistent.py:599) — und das sind **ausschließlich firmenweite** Zahlen:
offene Aufgaben, Vorgänge der letzten 48 h, Fälligkeiten, versendete Angebote. Nichts davon
hat mit der gestellten Frage zu tun.

Objektbezogene Zählungen entstehen nur beiläufig über den Dossier-Weg (`_vorgang_block`,
assistent.py:556) — und dort steht `offene_auftraege`, also die Zahl der **offenen**, nie die
Gesamtzahl. Auf „wie viele Aufträge *existieren*" gibt es im Kontext schlicht keine Zahl.

Verschärfend: Die mitgelieferte Liste ist auf `MAX_LISTE = 5` gedeckelt. Bei zwölf Aufträgen
könnte das Modell nur bis fünf zählen — und läge **still** falsch. Das ist der gefährlichste
der vier Befunde, weil er nicht als Fehler auffällt.

---

## 2. Was im eigenen Ökosystem bereits existiert

Eine interne Erhebung über `websearch` und `mcn` hat erheblich mehr Vorarbeit zutage gefördert
als erwartet. Das Wichtigste:

### 2.1 Der `knowledge`-Beschluss ist da — und ungebaut

`docs/ENTSCHEIDUNGEN.md`, Abschnitt 11, legt ein `knowledge`-Schema fest. `grep` über `db/`
und `backend/`: **null Treffer**. Der Beschluss ist dokumentiert, aber nicht implementiert —
konsistent mit der dort ebenfalls festgehaltenen Terminierung („ganz zum Schluss, nicht
vorziehen"). Er begründet drei Dinge, die eigenständig wertvoll sind:

- **Warum dieselbe Datenbank:** Ein Monteur mit `row_scope='EIGENE'` darf über die Suche nichts
  finden, was er in der Oberfläche nie sähe. Eine separate Vektor-DB hieße Rechte duplizieren
  oder nachträglich filtern — *„Genau so lecken RAG-Systeme."*
- **Warum eigenes Schema:** `knowledge.*` ist ausdrücklich vom Schutzstandard ausgenommen —
  kein No-Delete-Trigger, denn *„Wer dort pflichtschuldig den No-Delete-Trigger anhängt, macht
  Re-Indexieren physisch unmöglich."* Der Denkrahmen **„der Index ist Cache, nicht Original"**
  löst nebenbei den GoBD-gegen-DSGVO-Konflikt.
- **Embedding-Modell und Dimension gehören an jeden Chunk** — sonst mischen sich beim
  Modellwechsel alte und neue Vektoren, und *„die Suche liefert ja weiterhin irgendetwas, nur
  das Falsche."*

⚠️ Der letzte Punkt ist im **laufenden** ki-Backend nicht erfüllt: `dokumente` hat keine
Modell-/Dimensionsspalte, das Modell steckt global in `EMBED_DIM`. Genau die Falle, vor der das
spätere MCN-Papier warnt.

⚠️ Namensinkonsistenz: `ki-orchestrierung.md` verweist auf `ai.embedding`,
`ENTSCHEIDUNGEN.md` auf `knowledge.*`. Zwei Papiere, zwei Schemata, keins gebaut. Muss vor der
Umsetzung entschieden werden.

### 2.2 Die Trennungslogik existiert schon und ist richtig

`websearch/README.md` formuliert als Kernprinzip: *„keine ‚eine große RAG', sondern getrennte
Sammlungen."* Konsequent umgesetzt — nur Unstrukturiertes wird gechunkt:

| Bestand | Ablage | Begründung im Code |
|---|---|---|
| Freitext-Dokumente | `dokumente` (pgvector, chunk-weise) | — |
| Datanorm Vaillant/Bosch (36k Artikel) | eigene relationale Tabellen + `tsvector` | *„Datanorm ist strukturiert, kein Ingest"* |
| Großhandels-Artikelstamm (2 Mio.) | eigene Datenbank `material` | bewusst getrennt von der Vektor-DB |
| Kuratierter Bestellkatalog (2.076) | JSON-Datei + deterministischer Index | — |

Die Begründung ist stärker als bloße Ordnung: Ein Geräte-Dossier bündelt Ersatzteile, Wartung
und Garantie — **das ist ein JOIN, keine Ähnlichkeit.** Ein Vektorstore könnte es prinzipiell
nicht leisten.

### 2.3 Die wichtigste Lehre: „ich weiß es nicht" muss erlaubt sein

Der Angebots-Resolver erreichte bei **387 echten Angebotspositionen aus 18 CRM-Belegen**
`auto_falsch = 0` in allen Stufen. Nicht durch bessere Embeddings. Die dokumentierte Diagnose:

> „Bisherige Fehlerquelle war nicht das Modell, sondern dass ‚ich weiß es nicht' keine erlaubte
> Antwort war — erzwungene Auswahl riet in 12 % falsch."

Die 12 % entsprachen exakt den 123 Kollisionsgruppen, bei denen die unterscheidende Information
in der Notiz gar nicht enthalten war. Mechanik: eindeutig → automatisch; mehrdeutig → Rückfrage
mit hervorgehobenem Unterschied; die Entscheidung wird als Alias gelernt; ein späterer
Widerspruch sperrt den Schlüssel dauerhaft. Die Rückfragen versiegen nach ~200 Entscheidungen
(29/50 → 5/50).

**Das ist exakt das Muster, das dem Assistenten fehlt.** Es ist gebaut, gemessen und bewährt —
nur an der falschen Stelle.

### 2.4 Weitere übertragbare Lehren aus `websearch`

- **„Parsing-Qualität dominiert"** — kein Chunker kann Parsing-Fehler heilen. Die alte Pipeline
  extrahierte mit Docling die Struktur und plättete sie dann zu Fließtext.
- **Die 7×-Lücke bei OCR** — schnelle Direkt-OCR verpasste Bildunterschriften; nach
  Layout-Pipeline: 184 KB → 1,31 MB, 325 → 1.781 Chunks. Aufgefallen nur durch
  Plausibilitätsprüfung („325 Chunks bei 350 Seiten kann nicht alles sein"). **Lehre:
  Chunk-Zahl gegen Seitenzahl gegenprüfen.**
- **Query-Embedding dominiert die Latenz** — `/rag/suche` dauert ~12 s, davon der größte Teil
  das Embedden der *Anfrage* über Ollama auf der geteilten Karte. Inhärent, kein Bug.
- **Modellgröße hat eine Untergrenze** — 0.8b getestet: fachlich unbrauchbar (Mengen geraten).
  9b nötig.
- **Kontext-Shift frisst Antworten** — bei zu knappem `num_ctx` schob Ollama bei großen
  Tool-Ergebnissen Daten heraus, die Antwort brach mitten in der Liste ab.

### 2.5 Was nachweislich fehlt

- **E-Mail-Empfang gibt es nirgends.** MCN kann Mails versenden (`services/mail.py`), nicht
  empfangen. `ai.content_item.source_type` sieht `'EMAIL'` bereits vor. Das ist Neubau.
- **OCR ist in MCN nur eine Registry-Hülle** — `_CAPABILITIES` kennt `OCR`, es existiert kein
  Client, keine PDF-Extraktion.
- **`ai.embedding` ist eine leere Hülle** — Tabelle und Model existieren, einzige Verwendung ist
  ein Test.
- **Zeit-/Versions-Awareness fehlt** — im eigenen Recherchepapier vom 25.06. als Punkt 5
  markiert (*„kritisch bei Gesetzen/Normen — veraltete Fassung ≠ aktuelle Wahrheit"*), nicht
  umgesetzt, obwohl ~2.469 Chunks Verordnungen im Bestand lagen.
- **Die Parkbegründung für Graph-RAG existiert nirgends schriftlich.** Workspace-weit gesucht.

---

## 3. Was drei Recherche-Läufe ergeben haben

Rohberichte unter `docs/recherche/`. Jede Behauptung wurde adversarisch geprüft (drei
unabhängige Prüfer, zwei Gegenstimmen kippen sie). **Von 75 geprüften Behauptungen haben 38
überlebt, 37 wurden verworfen.**

### 3.1 Belegt — und direkt anwendbar

**Der passende Postgres-Operator für den Tippfehler ist nicht der naheliegende.**
`pg_trgm` bietet drei Stufen; die offizielle Doku weist sie ausdrücklich zu:

> „the strict_word_similarity function is useful for finding the similarity to whole words,
> while word_similarity is more suitable for finding the similarity for parts of words."

| Operator | Funktion | Default-Schwelle | Vergleicht |
|---|---|---|---|
| `%` | `similarity` | 0,3 | ganze Zeichenketten |
| `<%` | `word_similarity` | 0,6 | Wortteile |
| **`<<%`** | **`strict_word_similarity`** | **0,5** | **ganze Wörter, an Wortgrenzen** |

Für „Wartuburgstr" gegen „WEG **Wartburgstr** 52" — ein vollständiges Wort gegen ein Wort
*innerhalb* eines längeren Namens — ist `<<%` der spezifikationsgemäße Kandidat. Vergleiche
sind streng „größer als", nicht „größer oder gleich". `pg_trgm` ignoriert Nicht-Alphanumerisches,
der Punkt in „Wartburgstr." fällt also ohnehin weg.

**Levenshtein als Nachfilter — mit zwei dokumentierten Fallen.**
`levenshtein_less_equal(source, target, max_d)` ist der Frühabbruch für Schwellwert-Filterung.
Aber:

1. Der Rückgabewert oberhalb `max_d` ist **weder Sentinel noch echte Distanz**. Das Doku-Beispiel
   liefert `3` für `levenshtein_less_equal('extensive','exhaustive',2)`, obwohl die wahre Distanz
   4 ist. → Ausschließlich auf `result <= max_d` testen. **Niemals darauf ranken oder ihn
   anzeigen.**
2. Negatives `max_d` fällt **still** auf volles `levenshtein` zurück. Bei dynamisch aus der
   Query-Länge berechnetem `max_d` absichern.
3. Nicht indexgestützt — rechnet pro Zeile. Nur als Nachrangstufe über einer bereits per
   Trigramm-Index reduzierten Kandidatenmenge. `levenshtein` hat ein Limit von 255 Zeichen.

**Phonetik scheidet für deutsche Daten aus.** Die `fuzzystrmatch`-Doku wörtlich: *„At present,
the soundex, metaphone, dmetaphone, and dmetaphone_alt functions do not work well with multibyte
encodings (such as UTF-8). Use daitch_mokotoff or levenshtein with such data."* `daitch_mokotoff`
existiert erst ab PG 15 und ist für Personennamen entworfen — über deutsche Straßennamen sagt die
Doku nichts. Kölner Phonetik ist in PostgreSQL nicht enthalten.

**Der `german`-Stemmer zerlegt keine Komposita.** Snowball selbst: *„To split up compound words
cannot be done without a dictionary, and the purely algorithmic stemmers presented here do not
attempt it."* Decompounding gibt es in PostgreSQL ausschließlich über Ispell/Hunspell — und dort
nur mit den *„basic compound word operations of Hunspell"*, also mit dokumentierter
Qualitätsobergrenze. Alle Doku-Beispiele sind norwegisch; deutsche Zerlegungsqualität ist
**nicht** belegt.

**Constrained Decoding ist gerechtfertigt — aber nicht überall.** Ein 8B-Modell bricht ohne
Grammatik-Zwang in **38 % der Function-Calling-Fälle** das Format (XGrammar, arXiv:2411.15100:
62 % → 100 % syntaktische Korrektheit). Der Nutzen ist Format-Compliance, *nicht* bessere
inhaltliche Antworten.

**Aber: kein schema-erzwungener Selbstdiagnose-Schritt.** Bei Qwen3-8B fiel die Genauigkeit
unter Zwang von 50,0 % auf 38,0 %; **96 von 100** Erstdiagnosen klassifizierten den Fehler
fälschlich als `FORMATTING_MISMATCH`, **58 Fälle** blieben in Endlosschleifen. (Einschränkung:
Einzelautor-Preprint, n=100, p≈0,059 — formal nicht signifikant. Eine zweite Messung findet mit
−8,7 pp eine deutlich kleinere Strafe.) → **Fehlfall 1 muss deterministisch in Code
diagnostiziert werden, nicht von einem Modellschritt.**

**Freies Text-to-SQL ist für uns keine Option.** Die spezialisierte Spitze der 7B-Klasse
(Arctic-Text2SQL-R1, per RL gegen Live-Datenbanken nachtrainiert) erreicht **68,47 %** auf
BIRD-Test — eine unabhängige Reproduktion maß nur 56,8 %. Generische Modelle derselben Größe:
**Granite-3.1-8B 36,0 %**, CodeLlama-7B 5,4 %. Für `qwen3.5:9b q4_K_M` realistisch 30–50 %, vor
Quantisierungsverlust, auf Englisch gemessen, ohne Rechtefilter.

Dazu ein eigenständiges Sicherheitsargument: **Sobald das Modell SQL-Struktur erzeugt, entfällt
der Prepared-Statement-Schutz prinzipbedingt** — der parametrisiert nur Literale, niemals
Bezeichner, Klauseln oder Statement-Typ.

**RRF-Mechanik.** `score = Σ 1/(k + rang)`, Rang beginnt bei 1, `k = 60` als Default bei
Elasticsearch wie Azure. `k` steuert die Flachheit der Abklingkurve, **nicht** die Gewichtung
einer Quelle.

### 3.2 Ausdrücklich widerlegt — nicht wieder aufsammeln

Diese Behauptungen klingen plausibel, sind aber mit 3:0 gefallen:

- ❌ **„Ein Index für alles scheitert nachweislich"** (vector search dilution). *Sämtliche*
  Belege dafür wurden verworfen. Das ist **kein Gegenbeweis** — die Quellen waren zu schwach —,
  aber wir dürfen es nicht als gesichert behandeln. *Was tatsächlich dafür spricht, ist unser
  eigenes System: `_artikel_erlaubt` musste als Notbremse gebaut werden, weil 2 Mio.
  Artikelzeilen jede unscharfe Frage gewannen. Gemessene Erfahrung im eigenen Bestand schlägt
  hier ein Preprint über eine englische Behördendatenbank.*
- ❌ „Strukturierte Ausgabeformate verschlechtern das Reasoning generell" — **und** die
  Gegenbehauptung, Constrained Decoding verbessere die Genauigkeit. **Beide Richtungen
  verworfen.** Die Frage ist offen, nicht entschieden.
- ❌ „`websearch_to_tsquery` löst den UND-Zwang." Die Doku: *„text not inside quote marks will
  be converted to terms separated by & operators."* Löst Fehlfall 2 **nicht**.
- ❌ „`plainto_tsquery` ist der Mechanismus hinter Fehlfall 2." Der UND-Zwang steckt in
  **unserem** Anwendungscode (`suche._tokens_q`).
- ❌ „RRF fusioniert rangbasiert, weil BM25- und Vektor-Scores unvergleichbare Wertebereiche
  haben." Die geläufige Begründung ist nicht belegt.
- ❌ „CodeAct schlägt JSON-Aktionen um bis zu 20 Prozentpunkte." Damit entfällt das
  Hauptargument für einen Umbau auf code-ausführendes Tool-Use.
- ❌ Der konkrete Inhalt der mitgelieferten `unaccent.rules` (Ä→A gegen Ä→AE) und die Signatur
  von `unaccent()` als SQL-Funktion. → **Vor Einsatz die installierte Datei selbst nachsehen**,
  inklusive `IMMUTABLE`-Problematik für Ausdrucksindizes.

### 3.3 Für die Wissensschicht belegt

- **Late Chunking geht mit bge-m3 nicht.** Das Verfahren setzt Mean-Pooling voraus, bge-m3 nutzt
  CLS-Pooling; gemessen bricht NDCG@5 von 0,246 auf 0,070 ein. *Bemerkenswert: Unser Team kam
  über einen anderen Weg zum selben Schluss — Ollama liefert nur gepoolte Vektoren. Zwei
  unabhängige Gründe, dieselbe richtige Entscheidung.*
- **Contextual Retrieval ist in der publizierten Form VRAM-gebunden** — ~20 GB Spitze selbst mit
  4-Bit-Phi-3.5-mini. Relativierung der Prüfer: batchgrößenabhängige Beobachtung, keine untere
  Schranke. *Unsere Lösung über Doclings `contextualize()` plus Titel-Präfix war die richtige
  Abkürzung.*
- **Der Cross-Encoder-Reranker trägt mehr als teure LLM-Kontextualisierung** — im direkten
  Vergleich nur 0,008 NDCG@5 Unterschied, und der Gewinn tritt laut Autoren *nur mit*
  Reranking überhaupt ein. Wir haben den Reranker bereits.
- **Chunking-Investitionen lohnen weniger als gedacht** — auf evidenz-armen Aufgaben praktisch
  null Unterschied (64,50 gegen 65,53 Evidence-Recall). Wo es wirkte, kam der Großteil
  nachweislich vom nachgelagerten Retrieval-Verfahren, nicht vom Chunker.
- **Teure LLM-Graphextraktion zahlt sich nicht aus** — ein fünffach reicherer Graph (244 statt
  48 Entitäten) löste die strukturellen Anfragen trotzdem nicht. Für uns: Die Kette
  Objekt→Anlage→Gerät→Norm liegt bereits relational in Postgres. *(schwach belegt, konvergiert
  aber mit der damaligen Entscheidung, Graph zu parken)*

### 3.4 Methodische Warnung — bitte ernst nehmen

**Belegt wurde fast ausschließlich, was in offizieller Dokumentation steht.** Alles Vergleichende
und Wertende ist in der Prüfung gefallen. Konkret:

- **Sämtliche Benchmarks sind englischsprachig.** Keine einzige verifizierte Zahl betrifft
  deutsche Prompts, deutsche Komposita oder deutschen Fachjargon.
- **Alle Modellzahlen gelten für unquantisierte Gewichte.** Der Effekt von `q4_K_M` auf
  Instruction-Following und Tool-Calling blieb trotz expliziter Frage **unbelegt**.
- **Keine Benchmark umfasst Mandanten- oder Rechtefilterung.**
- Bei mehreren Prüfläufen war das Suchbudget erschöpft, sodass keine unabhängige Gegensuche
  stattfand. Für Dokumentationsaussagen (Operator-Semantik, Defaults, Formeln) ist das
  unkritisch — für Wertendes wäre es gravierend.

**Konsequenz:** Die Recherche kann uns Sackgassen ersparen und die Richtung geben. Die Zahlen,
die für *unser* System gelten, wird nur unser eigener Eval-Harness liefern.

---

## 4. Was nicht durch Recherche lösbar ist

| Frage | Warum Lesen nicht hilft | Wie zu klären |
|---|---|---|
| Schwellwerte für `<<%` / Levenshtein | Keine Doku, hängt am Datenbestand | Goldliste echter Tippfehler, empirisch kalibrieren |
| Passt ein Embedder neben dem 9B-Modell auf die Karte? | Bereich blieb unbelegt | Messen: `nvidia-smi`, `ollama ps` |
| Kommt Ollamas Schema-Modus mit unseren Schemata klar? | Engine unter dem JSON-Modus undokumentiert | Schemata aus `assistent.py` gegen den Endpunkt testen |
| HNSW-Recall bei scharfen ACL-Filtern | Kernfrage für rechtegefiltertes RAG, unbeantwortet | Mit echten Daten messen |
| Sind Embeddings personenbezogene Daten? | Rechtsfrage | **Fachanwalt / Datenschutzbeauftragter** |
| Dürfen DIN/VDI/DVGW in einen internen Index? | Lizenzrecht | **Fachanwalt** — vor dem ersten Ingest |
| GoBD-Aufbewahrung gegen Art. 17 | Rechtsfrage mit technischer Folge | **Fachanwalt**; technisch adressiert der „Index ist Cache"-Rahmen aus ENTSCHEIDUNGEN.md §11 einen Teil |

> ⚠️ **Die juristischen Fragen sind Vorbedingung, kein Nachgedanke.** Sie müssen geklärt sein,
> *bevor* die erste E-Mail oder Norm indiziert wird. Ein Index, der nachträglich bereinigt
> werden muss, ist teurer als einer, der von Anfang an richtig geschnitten ist.

---

## 5. Umsetzungsplan

Reihenfolge nach Wirkung pro Aufwand. **Nichts davon ist begonnen.**

### Stufe 0 — Eval-Harness (das Messgerät)

*Empfohlener erster Schritt. Ohne ihn ist jede folgende Änderung Bauchgefühl.*

Der entscheidende Vorteil unserer Architektur: **Die deterministische Retrieval-Mitte ist ohne
Modellaufruf testbar.** `_suchtreffer(frage, sicht)` ist eine reine Funktion — Frage rein,
Entitätsliste raus. Das lässt sich als gewöhnlicher `pytest` fahren: schnell, deterministisch,
in CI.

1. **Goldliste** aus echten Fragen: je Frage die erwarteten Entitäts-IDs. Inklusive einer
   **Tippfehler-Sammlung** aus tatsächlichen Nutzereingaben — die brauchen wir ohnehin zur
   Schwellwert-Kalibrierung.
2. **Retrieval-Metriken getrennt** von Antwortmetriken (Recall@k, MRR). Ein Retrieval-Fehler und
   ein Formulierungsfehler sind verschiedene Krankheiten.
3. **Antwortqualität separat und seltener**, mit lokalem Judge.
   ⚠️ Ob ein 9B-Modell als Judge zuverlässig genug ist, blieb **unbelegt** — als Signal
   behandeln, nicht als Wahrheit.
4. Größenordnung nötiger Testfälle: **unbelegt**. Pragmatisch mit den echten Fragen aus
   `docs/DISPONENT_BEFUNDE.md` beginnen und wachsen lassen.

**Aufwand:** überschaubar. **Risiko:** keins (rein additiv, kein Produktivpfad berührt).

### Stufe 1 — Tippfehler-Kaskade und „meintest du …?"

Löst Fehlfall 1, den sichtbarsten. Gestufte Rückfallkaskade statt Fusionsmaschinerie — bei
Dutzenden bis wenigen Hundert Objekten ist RRF nicht belegt sinnvoll:

```
exakte Tokensuche  →  0 Treffer?
   → strict_word_similarity (<<%) gegen Entitätsnamen, Index-gestützt
      → Kandidaten mit levenshtein_less_equal nachfiltern (nur <= max_d testen!)
         → drei unterscheidbare Zustände
```

**Die drei Zustände sind der eigentliche Punkt:**

| Zustand | Antwort des Assistenten |
|---|---|
| `GEFUNDEN` | normal beantworten |
| `GEMEINT-ABER-UNSICHER` | „Meintest du *WEG Wartburgstr 52*?" |
| `NICHT-VORHANDEN` | „Eine Liegenschaft mit diesem Namen kenne ich nicht." |

Damit wird die heutige Ununterscheidbarkeit aufgelöst. Das ist dasselbe Muster, das der
Angebots-Resolver mit `auto_falsch = 0` bewährt hat: **Rückfragen statt Raten.**

> 🔒 **Sicherheitshinweis:** Die Vorschlagsliste muss durch **dieselbe `Sicht`** laufen wie die
> reguläre Suche. Sonst verrät ein „meintest du …?" die Existenz von Objekten, die der Nutzer
> nicht sehen darf. Der bestehende `_fokus_treffer()`-Baustein macht das bereits vorbildlich —
> er schickt jeden Eintrag frisch durch die rechtegefilterte Suche.

> ⚠️ Diagnose und Zustandsentscheidung gehören **deterministisch in Code**, nicht in einen
> Modellschritt (Befund 3.1: schema-erzwungene Selbstdiagnose kippt in Endlosschleifen).

**Offen:** konkrete Schwellwerte. Nicht dokumentiert, muss über die Goldliste aus Stufe 0
kalibriert werden. → *Das ist der Grund, warum Stufe 0 zuerst kommen sollte.*

### Stufe 2 — UND-Zwang lockern

Löst Fehlfall 2 an der Wurzel statt über die Floskelliste. Betrifft `suche._tokens_q`.

Statt „alle Tokens sind Pflicht" → **mindestens N Tokens müssen treffen, Rangfolge nach Anzahl
und Nähe der Treffer.** Damit schadet ein durchgerutschtes „viele" nicht mehr, es rankt nur
schlechter. Die Floskelliste bleibt als Optimierung, ist aber nicht mehr kritisch.

`ts_rank_cd` (Cover Density) wäre der Postgres-Baustein für die Nähe-Komponente.
⚠️ Er braucht Positionsinformation und liefert bei gestrippten `tsvector`s konstant 0.

**Risiko:** höher als Stufe 1 — das ist ein Eingriff in die zentrale Suche, die auch die
Oberfläche nutzt. **Ohne Stufe 0 würde ich das nicht anfassen.**

### Stufe 3 — Kennzahlen-Katalog statt Listen zählen

Löst Fehlfall 4, den gefährlichsten (still falsche Zahlen).

Ein **Katalog vordefinierter, parametrisierter Kennzahlen** — das Modell wählt nur Kennzahl und
Parameter aus einem Enum, schreibt **kein SQL**:

```
anzahl_auftraege(objekt_id, zeitraum?, status?)
anzahl_vorgaenge(objekt_id, …)
summe_offene_posten(objekt_id)
letzter_einsatz(objekt_id)  /  naechster_einsatz(objekt_id)
```

Vorteile: Prepared Statements greifen voll, der bestehende Rechtefilter bleibt eine
WHERE-Bedingung, und die Zahlen sind **gezählt statt generiert**. Passt exakt zur bestehenden
Doktrin und zu `dossier.py`s These: *„~80 % der späteren Auskunftsqualität hängen nicht am
Modell, sondern daran, dass die Zahlen exakt, prüfbar und rechtegefiltert an einer Stelle
zusammenkommen."*

Ausdrücklich **nicht**: freies Text-to-SQL (Befund 3.1).

### Stufe 4 — Einsätze im Kontext

Löst Fehlfall 3. Kleine, klar umrissene Ergänzung:

- `EINSATZ` in `_DOSSIER_TYPEN` aufnehmen
- `einsaetze` in den Liegenschafts-Extrakt (`_kompaktes_dossier`) — sinnvollerweise **letzter
  vergangener und nächster geplanter**, nicht die Rohliste
- Gattungswort-Typfilter entschärfen: „Termin" darf die Liegenschaft nicht aus den Treffern
  werfen

### Später: die Wissensschicht

Erst nach den Stufen 0–4, und erst nach juristischer Klärung. Grundlage ist
`ENTSCHEIDUNGEN.md` §11:

- `knowledge`-Schema in **derselben** Datenbank (Rechte bleiben eine WHERE-Bedingung)
- **Embedding-Modell und Dimension an jedem Chunk** — die Lehre aus dem ki-Backend
- **Getrennte Korpora** nach Herkunft (`source_kind`), nicht ein Topf
- **Zeitliche Gültigkeit von Anfang an** (bitemporal) — sonst liefert das System irgendwann
  selbstbewusst veraltetes Recht
- Für E-Mails: Ingestion existiert noch gar nicht, das ist Neubau
- Für Normen: möglicherweise **Verweis- statt Volltextarchitektur**, abhängig von der
  Lizenzprüfung

---

## 6. Was wir bewusst nicht bauen

| Nicht bauen | Begründung |
|---|---|
| Freies Text-to-SQL | 30–50 % erwartbare Genauigkeit; Prepared-Statement-Schutz entfällt |
| Schema-erzwungener Selbstdiagnose-Schritt | Kippt in Format-Fixierung und Endlosschleifen |
| Offener ReAct-Agentenloop | Das CodeAct-Argument dafür wurde 3:0 widerlegt |
| Late Chunking auf bge-m3 | Pooling-inkompatibel; zusätzlich durch Ollama blockiert |
| LLM-Massen-Kontextualisierung | VRAM-gebunden; Reranker trägt mehr |
| Graph-RAG mit LLM-Entity-Extraktion | Beziehungen liegen bereits relational vor |
| Hunspell-Decompounding (jetzt) | Kein Fehlfall scheitert an Komposita; später für Normtexte relevant |
| Eigenes Fine-Tuning | Aufwand, den ein Ein-Personen-CRM nicht stemmt |

---

## 7. Zu entscheiden

1. **Reihenfolge:** Stufe 0 (Messgerät) zuerst, oder Stufe 1 (sichtbare Wirkung) zuerst?
   *Empfehlung: Stufe 0 — die Schwellwerte aus Stufe 1 lassen sich ohne Messgerät nur raten,
   und Raten ist genau das, was wir abstellen wollen.*
2. **Schema-Namensfrage:** `ai.embedding` oder `knowledge.*`? Zwei Papiere widersprechen sich.
3. **Juristische Klärung** anstoßen — wer, bis wann? Blockiert die gesamte Wissensschicht.
4. **Graph-RAG-Parkbegründung** neu herleiten oder die alte Entscheidung übernehmen?
5. Sollen die offenen **Messungen** (VRAM, Ollama-Schema, HNSW-Recall) vorgezogen werden?

---

## Anhang: Quellen

- **Rohberichte:** `docs/recherche/2026-07-22_*.md` — je mit Konfidenz, Abstimmungsergebnis,
  Zitat und Einschränkungen pro Behauptung, plus vollständiger Liste der widerlegten Aussagen
- **Reproduktion der Fehlfälle:** über `assistent._suchtreffer()` und `_montiere_kontext()` am
  laufenden System, rein lesend
- **Interne Erhebung:** `websearch/` (README, `api/app.py`, `api/pipeline_v2.py`,
  `api/vaillant_wissen.py`, `docs/RECHERCHE_RAG_VERBESSERUNGEN.md`) und `mcn/`
  (`docs/ENTSCHEIDUNGEN.md`, `docs/ki-orchestrierung.md`, `db_core/services/`)
