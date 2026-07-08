# 01 — Übersicht (Hero: Übersicht/Dashboard + Auswertung „Umsatz- und Projektübersicht")

## Zweck & Hero-Entsprechung

Die **Übersicht** ist der Startbereich des Leitstands — eine kuratierte,
kompakte Landing-Page mit Kacheln, die den Nutzer nach dem Login sofort
handlungsfähig macht (Aufgaben, Dokumente, Umsatz-/Projektlage, KI-Vorschläge).
Sie entspricht Hero's Startseite „Übersicht/Dashboard" (Kacheln Aufgaben,
Dokumente, Umsatz) und übernimmt für den Umsatz-/Projektteil die Kachel-Logik
des Hero-Dashboards **„Umsatz- und Projektübersicht"** — das einzige
Auswertungs-Dashboard, das in Hero paketübergreifend (Starter + Pro) verfügbar
und am kompaktesten ist und sich deshalb als Basis für die Startseite eignet.
**Bewusste Abgrenzung:** hier nur die kompakte Landing; die volle
Tiefen-Analytik (7 Dashboards) liegt in `10-auswertungen.md`.

- **Abgedeckte Hero-Quelldateien:**
  - `Auswertungen Dashboards\Auswertung Umsatz- und Projektübersicht\Auswertung Umsatz- und Projektübersicht.txt`
    (Umsatz-/Projekt-Kacheln der Startseite; das volle Dashboard selbst wird in
    `10` behandelt — diese Datei speist beide Sektionen).
  - Aufgaben- und Dokumente-Kacheln stammen aus Hero's „Übersicht/Dashboard"
    (Startseiten-Kacheln, siehe `00-informationsarchitektur.md`, Abschnitt
    „Hero's Informationsarchitektur"); die
    fachliche Tiefe dazu liegt in `07-aufgaben.md` bzw. `05-dokumente.md`.

## Ziel-Navigation & Routen

- Angular-Route: `/` bzw. `/uebersicht` (erster Sidebar-Punkt, oberste Position
  — spiegelt Hero, siehe `00`).
- **Keine Tab-Struktur, keine „Mappe":** Die Übersicht ist eine einzelne
  Landing-Seite mit einem responsiven Kachel-Raster. Kacheln sind reine
  Einstiegs-/Absprung-Widgets; jede Kachel verlinkt in ihre Fachsektion
  (Aufgaben → `07`, Dokumente → `05`, Umsatz/Projekte → `10`).
- Globale Nav-/Sidebar-Konventionen: siehe `00-informationsarchitektur.md`.

## Screens & Komponenten

### Landing-Dashboard (Kachel-Raster)

- **UI-Typ & Aufbau:** Einzelseite mit responsivem Kachel-Raster. Vorgesehene
  Kacheln:
  1. **Aufgaben-Kachel** — offene To-dos des Nutzers (kompakte Liste, Absprung
     nach `07`). Anlege-Absprung `[+ Aufgabe]`. Fachdetail in `07`.
  2. **Dokumente-Kachel** — zuletzt bearbeitete/relevante Dokumente (Absprung
     nach `05`). Fachdetail in `05`.
  3. **Umsatz-Kachel(n)** — Umsatzkennzahlen aus Rechnungen: 2 Kennzahl-Kacheln
     + Zeitstrahl (Umsatzverlauf), übernommen aus „Umsatz- und
     Projektübersicht". Kompakte Darstellung; für Details Absprung nach
     `/auswertungen/umsatz-projektuebersicht`.
  4. **Projektübersicht-Kachel(n)** — Kernkennzahlen: Gewinn (absolute Marge),
     Offene Umsätze, Erstellte Projekte (nach Gewerk), Abgeschlossene Projekte.
     Kompakt; Absprung nach `10`.
  5. **KI-Vorschläge-Kachel** — offene Vorschläge aus `ai.ai_proposal` (siehe
     unten), inline annehmbar/ablehnbar.
- **Zustände:**
  - *Laden:* Skeleton je Kachel (Kacheln laden unabhängig).
  - *Leer:* je Kachel eigener Leerzustand (z. B. „Keine offenen Aufgaben").
  - *Fehler:* Kachel-lokaler Fehlerzustand mit Retry, ohne die ganze Seite zu
    blockieren.
  - *Rollen-Sichtbarkeit:* Umsatz-/Projekt-Kacheln sind kennzahlensensibel und
    an das Auswertungs-Zugriffsrecht gebunden (Hero: „Zugriffsrechte" für den
    Auswertungsbereich → `security`); ohne Recht wird die Kachel ausgeblendet.
    Aufgaben-/Dokumente-Kacheln zeigen nur, worauf der Nutzer Zugriff hat.
- **Wiederverwendete shared components** (siehe `00`): **Dashboard-Kachel**,
  **Diagramm** (Zeitstrahl), **Logbuch/Feed-Muster** (für die
  KI-Vorschläge-Kachel). *Neu:* das kuratierte Landing-Grid-Layout selbst
  (Kachel-Auswahl/-Anordnung) ist eigenständig; die einzelnen Kennzahl-Queries
  werden mit `10` geteilt (kompakte Variante).

## API-Endpunkte (django-ninja)

Die Übersicht ist **rein lesend** (aggregierende Zusammenfassungen); Schreiben
passiert erst in den verlinkten Fachsektionen. KI-Vorschlag-Aktionen laufen über
die Service-Tore der jeweiligen Zielsektion (nicht hier neu definiert).

| Methode | Pfad | Zweck | Auth | Service-Funktion |
|---|---|---|---|---|
| GET | `/api/uebersicht/summary` | Aggregierte Startseiten-Kennzahlen (Umsatz kompakt, Projekt-Kennzahlen) | offen (Dev), kennzahlengated | `auswertungen.umsatz_projektuebersicht_summary` (kompakte Reuse aus `10`) |
| GET | `/api/uebersicht/aufgaben` | Offene Aufgaben des Nutzers (Top-N für Kachel) | Session | (Reuse aus `07`) |
| GET | `/api/uebersicht/dokumente` | Zuletzt relevante Dokumente (Top-N für Kachel) | Session | (Reuse aus `05`) |
| GET | `/api/uebersicht/ki-vorschlaege` | Offene `ai.ai_proposal`-Einträge (Top-N) | Session | `ai.list_open_proposals` |

- Lesend: alle vier Endpunkte. Kennzahl-Endpunkte teilen sich Query-Logik mit
  `10` (dieselben Berechnungsdefinitionen, siehe dort), liefern hier aber nur
  die kompakte Kachel-Teilmenge.
- Schreibend: keine eigenen Writes in dieser Sektion. Annahme/Ablehnung eines
  KI-Vorschlags erfolgt über den Service der Zielsektion und damit **immer über
  `business_transaction`** (siehe `00`).

## DB-Bezug

- **Lesend/aggregierend** über mehrere Schemas (keine eigenen Tabellen):
  - Umsatz: `invoicing` (Rechnungsvolumen; Status „erstellt"/„gesendet" inkl.
    Korrekturen, ohne „storniert"/„Entwurf"/„archiviert" — Statusnamen mit
    `invoicing`-Schema abgleichen, siehe `10` und Offene Punkte).
  - Offene Umsätze: `invoicing`, `workflow` (letzte Auftragsbestätigung sonst
    letztes Angebot minus erstellte Rechnungssumme).
  - Projekte nach Gewerk/Status: `management`, `workflow`.
  - Aufgaben: `workflow` (Detail in `07`).
  - Dokumente: `content`, `invoicing` (Detail in `05`).
  - KI-Vorschläge: `ai.ai_proposal`.
  - Zugriffsrecht Auswertungen: `security`.
- **Statusautomaten/Trigger/Constraints:** Die Übersicht schreibt nicht und muss
  daher primär die **Sichtbarkeits-/Statusfilter** der Kennzahlen respektieren
  (z. B. stornierte/archivierte Belege nie im Umsatz zählen). Alle
  schreibenden Folgeaktionen respektieren die Statusautomaten ihrer Zielsektion.

## KI-Andockpunkte (`ai.ai_proposal`)

- **KI-Vorschläge-Kachel** ist der zentrale, sichtbarste Andockpunkt des ganzen
  Leitstands: offene `ai.ai_proposal`-Einträge werden hier gebündelt angezeigt
  (KI-first: die KI ist primärer Akteur, siehe `00`/Vision).
- Vorschläge sind inline **annehmbar/ablehnbar**; die Ausführung geht durch
  **exakt dieselben Service-Tore** wie eine menschliche Aktion (Statusautomat,
  Freigabe, Vier-Augen, Audit) — kein KI-Sonderweg.
- Die Kachel ist Querschnitt: sie aggregiert Vorschläge aus allen Fachsektionen
  (Angebotspositionen, Aufgaben, Statuswechsel …), führt sie aber nicht selbst
  aus, sondern reicht an den jeweiligen Zielsektion-Service durch.

## No-Delete/Audit/GoBD-Übersetzung

- Diese Sektion ist lesend; es gibt keine eigenen „Löschen"-Aktionen.
- **KI-Vorschlag ablehnen** ist kein Löschen: der `ai_proposal`-Eintrag wird
  status-geändert (abgelehnt/erledigt), nicht entfernt — Audit-/Append-only-Trail
  bleibt erhalten (siehe `00`).
- Kachel-Absprünge in `05`/`07` erben deren No-Delete-/GoBD-Regeln.

## Offene Punkte / Entscheidungen

- **Kachel-Auswahl & Reihenfolge der Startseite:** verbindlicher Satz an Kacheln
  und deren Priorisierung ist eine Produktentscheidung (Vorschlag: Aufgaben,
  KI-Vorschläge, Umsatz, Projektübersicht, Dokumente). Entscheidbar mit
  Produkt/User.
- **Personalisierung:** Sind Kacheln pro Nutzer konfigurierbar (Anordnen/
  Aus-/Einblenden) oder fix? Vorschlag für v1: fix. Entscheidbar.
- **Kennzahl-Gating:** Übernommen aus Hero „Zugriffsrechte" — Umsatz-/
  Projekt-Kacheln nur mit Auswertungs-Recht (`security`). Bestätigen, sobald die
  Rechtematrix steht.
- Umsatz-Statusfilter (welche `invoicing`-Status zählen) ist mit `10` gemeinsam
  zu klären (siehe dort, „Offene Punkte").

## Abhängigkeiten

- **Auth/Session + `app_user`** (für nutzerbezogene Aufgaben-/Dokumente-/
  KI-Kacheln) und **Rechtematrix** (`security`) für Kennzahl-Gating — Phase 0.
- **Shared components:** Dashboard-Kachel, Diagramm, Feed-Muster (siehe `00`).
- **`10-auswertungen.md`:** liefert die Kennzahl-Query-Logik (Umsatz/Projekt),
  die hier kompakt wiederverwendet wird — `10` sollte für den Umsatzteil zuerst
  (oder parallel) stehen.
- **`07-aufgaben.md`** und **`05-dokumente.md`:** liefern die Fachdaten für die
  jeweiligen Kacheln.
- **`ai.ai_proposal`-Layer:** muss existieren, damit die KI-Kachel Daten hat.

## Aufwand & Priorität

- **Empfohlene Phase:** Phase 3 (siehe `00`) — nach Auswertungen (`10`), deren
  Kennzahl-Queries die Startseite wiederverwendet. Die Startseite ist ein
  Aggregat bestehender Slices und lohnt sich erst, wenn diese stehen.
- **Aufwand:** Landing-Dashboard **M** (Grid-Layout + 5 Kacheln, überwiegend
  Wiederverwendung; Neuentwicklung v. a. Kachel-Auswahl und KI-Kachel-Inline-
  Aktionen). Ohne die zugelieferten Sektionen wäre es **L** — der Aufwand hängt
  fast vollständig an den Abhängigkeiten.
- **Reihenfolge:** zuletzt in Phase 3, sobald `10`/`07`/`05` und der
  KI-Layer stehen.

## Screenshots zur Vorlage (Wiedererkennung)

- `Auswertung Umsatz- und Projektübersicht` **image1.png**, **image2.png** —
  Kachel-Layout der Umsatz-/Projektkennzahlen (2 Umsatz-Kacheln + Zeitstrahl,
  4 Projekt-Kacheln). HOCH-Wiedererkennung; prägt das Umsatz-/Projekt-Segment
  der Startseite. Die übrigen Kachel-Muster (Aufgaben/Dokumente) siehe die
  jeweiligen Fachsektions-Docs.
