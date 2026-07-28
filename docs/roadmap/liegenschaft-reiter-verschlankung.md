# Liegenschaftsmappe verschlanken — Recherche und Empfehlung

*Auslöser: Praxistest Sascha (2026-07-28): „Wenn ich mir das ganze Reiter-Konstrukt
so ansehe, ist das ja schon ziemlich viel. Ich habe gestern mal eine Liegenschaft
angelegt und das ganze Szenario der Zuordnung und Kontaktanlage durchgespielt. Es
ist schon echt viel klicken."*

Diese Datei ist die **Recherche samt Empfehlung**, nicht das Umsetzungsprotokoll.
Was davon schon gebaut ist, steht unten in Abschnitt 7.

---

## 1. Befund: die Zahlen

| Detailseite | Reiter | Labels |
|---|---:|---|
| **liegenschaft-detail** | **11** | Übersicht · Struktur · Anlagen · Räume · Beteiligte · Verwaltung · Eigentum · Belegung · Projekte & Vorgänge · Dokumente · Dateien |
| projekt-detail | 9 | Übersicht · Liegenschaften · Vorgänge · Aufträge · Aufgaben · Logbuch · Checklisten · Dokumente · Dateien |
| auftrag-detail | 6–8 | dynamisch nach Recht |
| kontakt-detail | 4–5 | Stammdaten · Adressen · [Ansprechpartner] · Aufgaben · **Dokumente & Dateien** · Logbuch |

Die Liegenschaft hat die meisten Reiter im ganzen Produkt — und als einzige
zusätzlich eine Kopfzeile über den Reitern sowie eine komplett zweite Ansicht
(`/dossier/liegenschaft/:id`, 491 Zeilen), die dasselbe Objekt ohne Reiter zeigt.

**Drei Wege zu denselben Daten** (Reiter, Kopfzeile, Dossier) sind das eigentliche
Symptom. Nicht die Reiteranzahl allein.

## 2. Wie es dazu kam (kein Schlamperei-Befund)

Jeder Reiter hat eine dokumentierte, für sich richtige Begründung. Beispiele aus
dem Code:

* Verwaltung steht bewusst **nicht** bei den Beteiligten: Sie ist kein
  `property_party_role`, sondern ein Mandat. „Sonst verwechselt man Auftraggeber
  und Verwalter, und die Rechnung geht an den Falschen."
* Anlagen stehen direkt hinter Struktur, weil sie „das technische Herz" sind.
* Räume sind von Struktur getrennt, weil der Baum „was gibt es" beantwortet und
  das Aufmaß „wie groß ist es".

Alle Begründungen stimmen **einzeln**. Sie wurden nur nie **gegeneinander**
abgewogen: Jeder Slice hat einen Reiter angehängt, und niemand hat je die
Gesamtleiste beurteilt. Genau dieselbe Mechanik hat `HANDOFF.md` einmal auf
2.346 Zeilen wachsen lassen.

## 3. Die drei echten Probleme

### 3.1 Fünf Orte beantworten „wer gehört zu diesem Objekt?"

| Ort | Was dort steht |
|---|---|
| Kopfzeile | Verwaltung (+ Beauftragungsvollmacht), Eigentümer, Mieter — je Name + Telefon |
| Reiter **Beteiligte** | `property_party_role`: Eigentümergemeinschaft, Eigentümer, Betreiber, Hausmeisterei |
| Reiter **Verwaltung** | Mandate mit Zuständigkeiten |
| Reiter **Eigentum** | Eigentumsstände mit Anteilen, Bestätigungsstatus |
| Reiter **Belegung** | Belegungen je Einheit mit Mieterliste |

Ein „Eigentümer" lässt sich über **zwei** Wege erfassen: als Rolle unter
Beteiligte oder als Eigentumsstand unter Eigentum. Es gibt keine Synchronisation
zwischen beiden. Verzahnt ist nur Belegung → Eigentum („Eigentümer (bewohnt)").

**Das ist der teuerste Punkt der Liste**: nicht Klickzahl, sondern
Doppelerfassung mit auseinanderlaufenden Ständen.

### 3.2 Zwei Formulare für denselben Raum

Der Struktur-Baum hat ein Kurzformular (Name, Fläche, Höhe), der Reiter Räume den
vollen Editor (Hüllflächen, Öffnungen, Grundriss). Beide schreiben `RoomIn`.
Zwei Masken für einen Datensatz laufen erfahrungsgemäß auseinander.

### 3.3 Seltenes bekommt so viel Platz wie Tägliches

Dokumente und Dateien sind zwei Reiter — in `kontakt-detail` sind sie längst
**einer** („Dokumente & Dateien"). Räume/Aufmaß braucht der Disponent am Telefon
nie; sie stehen an vierter Stelle.

## 4. Gemessene Klickpfade (Ist)

Alle vier Standardaufgaben brauchen heute **je einen Reiterwechsel und einen
Dialog** — das ist bereits das Ergebnis der I13-Verschlankung:

| Aufgabe | Weg | Klicks |
|---|---|---:|
| Gebäude anlegen | Struktur → ＋ Gebäude → Nummer → Speichern | ~3 |
| Einheit anlegen | Struktur → ＋ Einheit am Gebäude → Typ/Nummer → Speichern | ~3 (Serie: 2) |
| Mieter zuordnen | Belegung → Belegen → Nutzungsart/ab/Kontakt → Speichern | ~3 |
| Anlage anlegen + Einheit | Anlagen → Anlage erfassen → Name/Art/Gebäude/Einheit | ~4 |

**Nicht die einzelne Aufgabe ist teuer, sondern die Kette.** Ein neues Objekt
vollständig aufzunehmen heißt heute: Struktur → Struktur → Belegung → Anlagen →
(Beteiligte) → (Verwaltung) — vier bis sechs Reiterwechsel, dazwischen jedes Mal
der Abgleich „welche Einheit war das noch gleich?". Genau dieser Abgleich ist
das, was sich müde anfühlt.

## 5. Empfehlung: 11 → 6 Reiter

| Neu | Enthält heute | Wie |
|---|---|---|
| **Übersicht** | Übersicht | unverändert |
| **Gebäude** | Struktur + Anlagen + Räume | Eine Seite, drei Sichten über einen Segment-Umschalter: **Gebäudeansicht** (Haus, s. u.) · **Liste & Bearbeiten** (Baum) · **Aufmaß** (Raumeditor) |
| **Beteiligte** | Beteiligte + Verwaltung + Eigentum + Belegung | Ein Reiter mit vier Abschnitten **untereinander** (nicht als Unterreiter): Verwaltung · Eigentum · Belegung · sonstige Rollen |
| **Vorgänge** | Projekte & Vorgänge | unverändert |
| **Dokumente & Dateien** | Dokumente + Dateien | zusammengelegt, wie in `kontakt-detail` |
| — | — | Kopfzeile bleibt (sie steht über allem und ist der Grund, warum die Reiter beim Telefonat gar nicht gebraucht werden) |

### Warum „Beteiligte" als ein Reiter mit Abschnitten und nicht als Unterreiter

Die fachliche Trennung Mandat ≠ Rolle ≠ Eigentum ≠ Belegung ist **richtig** und
muss sichtbar bleiben — sie darf nur nicht **Navigation** sein. Vier Abschnitte
mit Überschriften untereinander erhalten die Trennung und ersparen das Suchen:
Wer „wer gehört zum Objekt" beantworten will, scrollt statt zu raten, in welchem
der vier Reiter der Name steht.

Zusätzlich fällig, unabhängig vom Layout: **Beteiligte-Rolle `PROPERTY_OWNER` und
Eigentumsstand müssen sich gegenseitig kennen** (mindestens ein Hinweis „hier ist
ein Eigentümer erfasst, der im Eigentumsstand fehlt"). Das ist ein Datenproblem,
kein Reiterproblem — und es bleibt bestehen, wenn nur zusammengelegt wird.

### Was NICHT zusammengelegt werden sollte

* **Vorgänge** bleibt eigenständig — andere Frage („was läuft"), andere Rechte,
  vier eigene Ladepfade.
* **Übersicht** bleibt — sie ist der Einstieg, keine Datenliste.
* Die **Kopfzeile** bleibt, wie sie ist. Sie ist bewusste Redundanz für den
  Telefonfall und hat sich bewährt.

## 6. Reihenfolge der Umsetzung (Aufwand steigend)

1. **Dokumente + Dateien zusammenlegen** — reine Template-Arbeit, Muster
   existiert in `kontakt-detail`. Halber Tag.
2. **Räume in den Reiter Gebäude** als dritte Sicht — Komponenten bleiben, nur
   die Einbettung wandert. Zusätzlich: das Kurzformular im Baum durch den
   Editor-Aufruf ersetzen (behebt 3.2). Ein Tag.
3. **Anlagen in den Reiter Gebäude** — die Gebäudeansicht zeigt die Technik
   ohnehin schon; die Liste wird zur zweiten Sicht. Ein Tag.
4. **Beteiligte/Verwaltung/Eigentum/Belegung zusammenlegen** — der größte
   Schritt, vier eigenständige Komponenten mit eigenem State untereinander. Zwei
   bis drei Tage, und der einzige Punkt mit echtem Regressionsrisiko.

Technisch ist der Umbau billig: Alle Reiter liegen im selben Component-Tree und
werden über `@if (tab() === 'xyz')` ein-/ausgeblendet (`Mappe`-Komponente,
Signal `aktiv`). Es gibt **keine** Deep-Links auf Reiter, kein Router-Outlet, kein
serverseitiges Reiterwissen — es ist reine Template-Umstellung.

## 7. Was davon bereits gebaut ist (2026-07-28)

**Die Gebäudeansicht** (`features/gebaeudeansicht/`, Endpunkt
`GET /api/property/properties/{id}/gebaeudeansicht`): die Liegenschaft als
Gebäudeschnitt — mehrere Häuser nebeneinander (Vorderhaus, Seitenflügel,
Hinterhaus), Etagen von oben nach unten, Einheiten als Kacheln mit Nummer,
Belegungsstatus, Bewohner und Technik; Zentralanlagen im Sockel. Ein Klick auf
eine Kachel zeigt Bewohner mit Telefon/E-Mail und die Anlagen der Einheit.

Sie sitzt im Reiter **Struktur** als erste von zwei Sichten („Gebäudeansicht" /
„Liste & Bearbeiten") — der erste Schritt von Punkt 2/3 der Empfehlung, ohne
einen zwölften Reiter anzulegen.

Damit beantwortet **eine** Ansicht, wofür bisher drei Reiter nötig waren:
Struktur (welche Einheit), Belegung (wer wohnt drin), Anlagen (welche Therme,
zentral oder nicht).

Live geprüft am 2026-07-28 gegen ein Demo-Objekt in der Dev-DB („ZZ Münsterstraße
24", 3 Gebäude / 23 Einheiten / 8 Anlagen) — hell und dunkel, mit Auswahl-Panel;
das Skript dazu ist ein Wegwerf-Stück und liegt nicht im Repo.

**Eine Einschränkung, bewusst so gelassen:** Bei drei Gebäuden reicht die
Lesebreite der Mappe (`--content-w`, 64rem) nicht für alle Häuser nebeneinander
— der Streifen scrollt in sich (mit Scroll-Schatten als Hinweis). Die Mappe auf
`--content-w-wide` zu stellen wäre die naheliegende Lösung, betrifft aber **alle
elf Reiter** und gehört deshalb in dieselbe Entscheidung wie die Zusammenlegung.

Offen bleibt die Zusammenlegung selbst — sie ist eine Entscheidung des Users,
weil sie die Navigation umbaut, an die er sich gewöhnt hat.

---

## Anhang: Datenquellen der Recherche

* `frontend/src/app/features/liegenschaft-detail/liegenschaft-detail.ts:112-132`
  (Reiterliste samt Begründungen), `.html` (724 Zeilen, Reiterblöcke)
* `kopfzeile.ts:28-43` (bewusste Redundanz), `api/property.py` (Endpunkt)
* `shared/mappe/mappe.ts` (Reitermechanik, `model<string>('aktiv')`)
* `features/belegung/`, `eigentum/`, `verwaltung/`, `anlagen/`, `raumaufmass/`
* Vergleichsseiten: `projekt-detail`, `auftrag-detail`, `kontakt-detail`
