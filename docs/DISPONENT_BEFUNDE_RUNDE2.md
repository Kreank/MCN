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
| E2 | „Frontend gefällt mir nicht wirklich." | UI | offen |
| E3 | „Merkt sich keine bereits eingetragenen Zeiten, um bei der nächsten Buchung da weiterzumachen." | UI | offen |
| E4 | „Zeitenübersicht gefällt mir nicht, zu unübersichtlich." | UI | offen |
| E5 | „Eingetragene Arbeitszeiten sollten von einem anderen bestätigt werden (**Zeitkontrolle**)." | MODELL | offen |
| E6 | Unter diesem Reiter zusätzlich: **Urlaubsanträge, Krankmeldungen, Überstundenausgleich beantragen**. | UI/MODELL | offen |
| E7 | **Umbenennen**: nicht „Meine Zeiten", sondern „Persönlicher Bereich"/„Mein Bereich" — „darin wird dann alles gebündelt, was diesen Angestellten betrifft." | UI | offen |

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
| G2 | „Mir fehlt die **Kalkulationsübersicht** aus dem Editor. Der Chef darf ruhig gleich sehen, wie die Kalkulation aussieht." | UI | offen |

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
