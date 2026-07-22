# Befunde aus dem Disponenten-Test — Runde 2

Saschas zweite Testrunde (2026-07-21), aufgenommen im Wortlaut und technisch
eingeordnet. Runde 1 liegt in `DISPONENT_BEFUNDE.md`; die dortigen Konventionen
gelten hier weiter.

> **Für den Dev-Agenten:** Arbeitsauftrag, von Sascha freigegeben. Reihenfolge
> und Zuschnitt stehen am Ende unter *Arbeitspakete*.

**Einordnung je Befund:** **UI** (billig, nur Oberfläche/Endpunkt) ·
**MODELL** (Schemaänderung) · **REGEL** (bewusst so gebaut) ·
**ARCHITEKTUR** (betrifft mehr als einen Bereich)

---

## Die Regel, die aus dieser Runde hervorgeht

Sascha wörtlich:

> „Grundsätzlich solltest du dir merken, dass egal was erstellt wird an
> Dokumenten innerhalb dieses Systems, alles über diesen Dokumentenkonfigurator
> läuft."

Das ist keine Einzelanforderung, sondern eine **Architekturregel**. Jedes
Dokument erbt den Aufbau, den Angebot und Rechnung schon haben:

1. **Briefkopf** — Auftraggeber, Adresse, Mieter, Lage/Wohnungsnummer,
   Auftragsnummer, Eigentümer
2. **Freitext** (dokumentabhängig)
3. **Positionen** — Material, Arbeitszeit; Artikel/Leistungen **suchbar und
   hinzufügbar wie im Angebot**
4. **Kalkulationsübersicht** — auch bei internen Dokumenten sichtbar

Der Baustellenbericht ist der Anlassfall: Er wurde als eigene Maske gebaut und
ist deshalb unbrauchbar. Die Regel verhindert die Wiederholung bei jedem neuen
Dokumenttyp.

---

## A — Bilder und Dateien

| # | Befund (Sascha) | Art | Status |
|---|---|---|---|
| A1 | „Bilder sollten gleich sichtbar sein im Frontend." Heute vermutlich nur Dateinamen in einer Liste. | UI | offen |
| A2 | „Außerdem klickbar! Wenn man drauf klickt, öffnet sich das Bild in groß." Großansicht/Lightbox. | UI | offen |
| A3 | Dateien: **Vorschau** einbauen. | UI | offen |
| A4 | Dateien **kategorisieren**: Bilder, Videos, Baustellenberichte. | MODELL | offen |
| A5 | „Gerne auch die Möglichkeit einbauen, eigene Kategorien einfügen, bearbeiten und löschen/deaktivieren zu können." — also **gepflegte Codeliste**, keine feste Aufzählung. Deaktivieren statt löschen passt zum Schutzstandard des Repos. | MODELL | offen |

---

## B — Baustellenbericht

| # | Befund (Sascha) | Art | Status |
|---|---|---|---|
| B1 | „Vollkatastrophe! Nicht zu gebrauchen!" — eigene Maske statt Dokumentenkonfigurator. | ARCHITEKTUR | offen |
| B2 | Aufbau **1:1 wie das Angebot**, mit einem Unterschied: über der Materialliste ein Freitextfeld. | ARCHITEKTUR | offen |
| B3 | Briefkopf muss tragen: **Auftraggeber, Adresse, Name des Mieters, Lage der Wohnung/Wohnungsnummer, Auftragsnummer, Eigentümer** (falls vorhanden). | UI | offen |
| B4 | Freitext „Ausgeführte Arbeiten". **Zugleich KI-Andockpunkt:** „Monteur gibt Notizen ein, KI macht sinnvollen Bericht daraus." | UI + KI | offen |
| B5 | Darunter Positionen wie gewohnt: Material, Arbeitszeit; Artikel/Leistungen **suchbar und hinzufügbar wie bei Angebot und Rechnung**. | ARCHITEKTUR | offen |
| B6 | **Klarstellung Sascha 2026-07-21 — kein Konflikt mit Migration 0080:** „Nein, in Baustellenberichten **keine Kalkulationen** anzeigen! Das hast du falsch verstanden." Die vermisste Kalkulation betrifft die **Angebots-Übersicht** (siehe G2), nicht den Bericht. Die Invariante aus `0080_berichtspositionen.py` („Der Bericht führt KEINE PREISE", weil er unterschrieben und versiegelt wird) bleibt **unangetastet** — der Bericht bekommt Briefkopf, Freitext und Positionen **ohne Geld**. | — | geklärt |
| B7 | **Bestandsaufnahme 2026-07-21:** Positionen für Material und Arbeitszeit **existieren bereits** (`workflow.site_report_line`, Migration 0080), inklusive `planned_quantity` für den Soll-Ist-Abgleich gegen das Angebot. Es fehlt also **nicht das Datenmodell**, sondern Briefkopf, ein ordentlicher Freitext und die Oberfläche. | — | geklärt |
| B8 | **Der eigentliche Mangel:** Der Bericht kennt seinen Auftrag nur als UUID. **Keines** der sechs Briefkopf-Felder ist über die Bericht-API oder im PDF erreichbar; das PDF zeigt `Auftrag: <Titel>` und `Objekt: <Name · Stadt>`, sonst nichts. Die Daten sind vollständig da, für fünf der sechs Felder gibt es fertige Services (`auftrag.py` PRINCIPAL, `beleg.delivery_stammdaten`, `property_steckbrief` Eigentümer, `belegung.aktive_mieter`). **Reine Verdrahtung.** | UI | **in Arbeit** |
| B9 | **Zwei Fallstricke für den Briefkopf:** (1) `site_report.work_order_id` ist seit 0064 **nullable** — ein Bericht am freien Termin (Begehungsprotokoll) hat gar keinen Auftrag und damit keinen Auftraggeber und keine Auftragsnummer. Der Briefkopf muss das aushalten. (2) Für den **unterschriebenen** Bericht gilt derselbe GoBD-Gedanke wie beim Beleg: Entweder die Briefkopfdaten werden beim Unterschreiben eingefroren, oder ein späterer Mieterwechsel ändert rückwirkend, was auf dem unterschriebenen Dokument steht. | — | **beide erledigt** — (1) mit dem Briefkopf-Slice, (2) mit Migration 0132: `header_snapshot` wird beim Unterzeichnen gesetzt und von `protect_site_report` versiegelt |

**Zu B9 (2) — was die Kontrollprobe zeigte.** Der erste Anlauf des Regressionstests
war wertlos: Er ließ die Vormieterin zum 1. August ausziehen, während „heute" der
22. Juli war — am Stichtag war sie also ohnehin noch die aktive Mieterin, und der
Test hätte auch ohne Snapshot bestanden. Mit vollzogenem Wechsel und
abgeschaltetem Snapshot-Pfad liefert er jetzt genau die Aussage, um die es geht:
`['Norbert Nachmieter'] == ['Erika Vormieterin']` — ohne die Migration steht der
Nachmieter auf einem Dokument, das die Vormieterin unterschrieben hat.

**Anmerkung zu B3:** Die Wohnungslage ist genau das, was I11f aus Runde 1
blockiert — `auftrag.unit_id` existiert in der DB, ist aber nicht angebunden.
Ohne diese Anbindung kann der Briefkopf die Wohnungsnummer nicht führen.
**B3 und I11f hängen zusammen.**

---

## C — Projekte & Vorgänge

| # | Befund (Sascha) | Art | Status |
|---|---|---|---|
| C1 | „Vorgänge sind mittlerweile eher uninteressant und dienen hauptsächlich dazu, wenn man nicht weiß, ob der Chef das annehmen will oder nicht." — bestätigt die Rolle des Vorgangs als **optionaler Eingangskorb**. | — | geklärt |
| C2 | „Viel wichtiger ist zu sehen, welche **Aufträge** schon in einer Liegenschaft stattgefunden haben." | UI | offen |
| C3 | Liste erweitern um **Aufträge und Einsätze/Termine**. „Das ist viel interessanter zu wissen." | UI | offen |

---

## D — Aufgaben

| # | Befund (Sascha) | Art | Status |
|---|---|---|---|
| D1 | „Grundsätzlich schon gut." | — | geklärt |
| D2 | Aufgaben an **Aufträge** binden können. | MODELL (klein) | offen |

---

## E — Persönlicher Bereich (heute „Meine Zeiten")

| # | Befund (Sascha) | Art | Status |
|---|---|---|---|
| E1 | „Zeiterfassung grundsätzlich funktional." | — | geklärt |
| E2 | „Frontend gefällt mir nicht wirklich." | UI | **teilweise — Rückfrage offen** (siehe unten) |
| E3 | „Merkt sich keine bereits eingetragenen Zeiten, um bei der nächsten Buchung da weiterzumachen." | UI | **erledigt** — „Von" setzt auf das Ende der letzten Buchung des Tages auf, „Bis" folgt als Vorschlag |
| E4 | „Zeitenübersicht gefällt mir nicht, zu unübersichtlich." | UI | **erledigt** — Stundenkonto (Soll/Ist/**Saldo**) steht jetzt vorn, die 30 flachen Zeilen sind nach Kalenderwoche gebündelt mit Wochensummen |
| E5 | „Eingetragene Arbeitszeiten sollten von einem anderen bestätigt werden (**Zeitkontrolle**)." | MODELL | **erledigt** — der Statusautomat mit Vier-Augen-Bestätigung stand schon (0067); sichtbar fehlte der Ablehnungsgrund und der Name des Prüfers, beides jetzt in „Letzte 30 Tage" |
| E6 | Unter diesem Reiter zusätzlich: **Urlaubsanträge, Krankmeldungen, Überstundenausgleich beantragen**. | UI/MODELL | **erledigt** — Migration 0130 (MONTEUR darf eigene Anträge anlegen/einreichen/zurückziehen, Genehmigen bleibt bei `hr/FREIGEBEN`), Migration 0131 (neue Art `FREIZEITAUSGLEICH`), Antragsdialog in der Personalakte |
| E7 | **Umbenennen**: nicht „Meine Zeiten", sondern „Persönlicher Bereich"/„Mein Bereich" — „darin wird dann alles gebündelt, was diesen Angestellten betrifft." | UI | **erledigt** — `/mein-bereich` mit den Reitern „Meine Zeiten" und „Personalakte & Anträge"; die alten Pfade leiten weiter |

**Zu E2 — was ich angenommen habe und was offenbleibt.** „Frontend gefällt mir
nicht wirklich" stand direkt neben E4 („zu unübersichtlich"); ich habe beide als
zwei Anläufe auf dieselbe Sache gelesen und die substanzielle Lesart bedient:
Der Übersicht fehlte nicht Gestaltung, sondern die **Aussage** — 30 Zeilen mit
Tagessummen beantworten nicht die Frage, die man an eine Zeitübersicht hat
(„liege ich vor oder zurück?"). Der Saldo-Endpunkt stand längst und war nur
nirgends angebunden.

**Falls E2 etwas anderes meinte** (Farbigkeit, Dichte, die Stempeluhr selbst,
das Handy-Layout), bitte einmal konkret sagen — dafür würde ich sonst raten.

**Zusätzlich in diesem Slice** (nicht auf der Liste, aber dieselbe Lücke): Beim
Nachtragen von Hand ließ sich die Zeit keinem Einsatz zuordnen — beim Stempeln
setzt der Server die Zuordnung selbst, von Hand fehlte sie, und die
Nachkalkulation griff ins Leere. Der Nachtrag-Dialog bietet jetzt die Einsätze
des gewählten Tages an.

---

## F — Mitarbeiter anlegen

| # | Befund (Sascha) | Art | Status |
|---|---|---|---|
| F1 | „Warum kann ich Personen aus meinen Kontakten da finden? Ich lege meine Mitarbeiter ja nicht wie einen Kunden an!" | MODELL | offen |
| F2 | Auftrag an den Entwickler: **recherchieren, wie andere Tools Mitarbeiterverwaltung handhaben**, und daraus eine Empfehlung ableiten. | — | **läuft** |

---

## G — Dokumente / Angebots-Editor

| # | Befund (Sascha) | Art | Status |
|---|---|---|---|
| G1 | Beim Öffnen eines Angebots: Positionen wirken „eingebacken", „festgesetzt" — auch ein internes Dokument soll sich wie ein Dokument anfühlen, nicht wie eine starre Maske. | UI | offen — **Rückfrage** |
| G2 | „Mir fehlt die **Kalkulationsübersicht** aus dem Editor. Der Chef darf ruhig gleich sehen, wie die Kalkulation aussieht." **Klarstellung 2026-07-21:** Gemeint ist die **Leseansicht** eines Angebots (Dokumente → Angebot anklicken → Übersicht/Positionen). Im **Editor** ist die Kalkulation vorhanden — sie fehlt nur dort, wo man das Angebot nur ansieht. Betrifft **nicht** den Baustellenbericht (siehe B6). | UI | offen |

**G1 ist der einzige Befund dieser Runde, den ich nicht sicher verstehe.**
Sascha sagt selbst „keine Ahnung, wie ich das sagen soll". Vor der Umsetzung
klären: Geht es um die *Optik* (Dokumentansicht statt Tabelle), um
*Bearbeitbarkeit* (Positionen wirken schreibgeschützt), oder um die
*Reihenfolge/Gliederung*?

---

## Querschnitt

1. **Der Dokumentenkonfigurator ist die Regel**, nicht die Ausnahme (siehe oben).
2. **B3 hängt an I11f** (Runde 1): Ohne `auftrag.unit_id` in API und UI kann
   kein Briefkopf die Wohnungsnummer führen.
3. **Deaktivieren statt löschen** — gilt auch für die neuen Dateikategorien (A5);
   der Schutzstandard des Repos kennt kein physisches Löschen.
4. **E5 (Zeitkontrolle)** berührt das Vier-Augen-Prinzip, das es im Repo schon
   gibt (`services/vier_augen.py`) — prüfen, ob es wiederverwendbar ist.

### Offener Wartungspunkt aus dem Slice E (2026-07-22)

„Was ist **mein** Einsatz" steht jetzt an **drei** Stellen in derselben Form:

| Ort | Form |
|---|---|
| `db_core/services/planung.py:445` | Queryset-Filter `assignments__assignee_id=actor` |
| `api/planung.py` (`_guard_own_job`) | Existenzprüfung |
| `api/zeiterfassung.py` (`_guard_eigener_einsatz`) | Existenzprüfung |

Heute stimmen alle drei überein (geprüft). Aber das ist eine **Objektgrenze**,
und die sollte einen Ort haben: Ändert sich die Regel je — etwa „ein LEAD sieht
auch die Einsätze seines Trupps" —, müssen drei Stellen gefunden werden, und die
vergessene fällt still auf die alte Regel zurück. Ein gemeinsamer Helfer in
`db_core/services/planung.py` wäre die saubere Ablage. Kein aktueller Fehler,
deshalb nicht im Slice mitgemacht.
