# 11 — Wartungsverträge (Hero: Modul „Kundendienst")

## Zweck & Hero-Entsprechung

Wartungsverträge verwalten wiederkehrende Wartungs-/Prüfaufgaben (z. B.
Thermenwartung, TÜV) und lösen bei Fälligkeit automatisiert eine Folgeaktion
aus. Entspricht Hero's Menüpunkt „Wartungsverträge" (im Text auch
„Wartungsaufträge") aus dem Modul „Kundendienst". Für den Gebäudeservice ein
Kernfeature (planbarer, wiederkehrender Umsatz).

**Abgedeckte Hero-Quelldateien:**
- `Wartungsverträge und Aufträge\Wie verwalte ich meine Wartungsverträge oder wiederkehrende Aufgaben\…txt`

## Ziel-Navigation & Routen

- Sidebar: **Wartung** (Entscheidung in `00`: eigener Punkt oder Unterbereich
  von Vorgängen `04`).
- `/wartung` — Liste (Filter, `[+ Wartungsvertrag]` oben rechts, Zeilen-Aktionen:
  Bearbeiten, Aktivieren/Deaktivieren, Archivieren).
- `/wartung/:id` (bzw. `/neu`) — Formular mit Tabs (Hero-Struktur):
  **Details** (Projekt/Kunde, Name, Start, Laufzeit, Intervall) → **Erinnerung**
  (Fälligkeits-Aktion + Vorlauf) → **Dokumente**.

## Screens & Komponenten

### Liste
- Ressourcen-Liste (shared). Zeilen-Status Aktiv/Inaktiv (Toggle), Archivieren
  nur nach Deaktivierung. Archivieren ist in Hero **nicht reversibel** →
  Bestätigungsdialog Pflicht.

### Formular (Tabs)
- **Details:** Zuordnung Projekt **oder** Kunde (Hero: nur eines), Name,
  Start-Datum, Laufzeit, Intervall (jährlich/monatlich/wöchentlich/Tage/festes
  Datum).
- **Erinnerung:** Fälligkeits-Aktion (4 Optionen, s. u.) + optionaler
  Vorlauf/Erinnerung.
- **Dokumente:** relevante Dokumente hinterlegen (→ `05`/`content`).
- **Aktion auslösen:** Button im Bearbeiten-Modus zum vorzeitigen manuellen
  Auslösen.

### Fälligkeits-Aktionen (der Kern)
Bei Fälligkeit erzeugt der Vertrag automatisch eines von:
1. **Projekt/Vorgang anlegen** (Pipeline-Stufe „Neu – Erstkontakt") → `04`/`workflow`.
2. **Auftrag anlegen** → `workflow` (Auftrag/Einsatz).
3. **Aufgabe anlegen** → `07`/`workflow.task` (noch anzulegen, siehe `07`).
4. **Benachrichtigung** → Notification/Feed.

## API-Endpunkte (django-ninja)

| Methode | Pfad | Zweck | Auth | Service |
|---|---|---|---|---|
| GET | `/api/maintenance/contracts` | Liste/Filter | offen | — |
| GET | `/api/maintenance/contracts/{id}` | Detail | offen | — |
| POST | `/api/maintenance/contracts` | Anlegen | Session | neu |
| PATCH | `…/{id}` (Status/Archivieren) | Statuswechsel | Session | neu (Statusautomat) |
| POST | `…/{id}/trigger` | Aktion manuell auslösen | Session | neu |

## DB-Bezug

- **Kein bestehendes Schema deckt den Wartungsvertrag direkt ab** — OFFEN.
  Kandidaten: `management` (wiederkehrende Vertrags-/Vorgangsdefinition) oder ein
  neues Konzept, da Hero „Projekt oder Kunde" (nicht Liegenschaft/Mandat)
  verknüpft. Wahrscheinlich **neue Hand-SQL-Migration** `maintenance.*` nötig
  (Vertrag, Intervall/Fälligkeitsregel, ausgelöste-Aktionen-Historie).
- Fälligkeits-Job (Scheduler) erzeugt Folgeobjekte über die jeweiligen
  Service-Tore (kein Direktweg).

## KI-Andockpunkte (`ai.ai_proposal`)

- KI schlägt aus Vertragshistorie/Objektdaten neue Wartungsintervalle vor.
- KI entwirft bei Fälligkeit das Angebot/den Auftrag (Positionen aus letzter
  Wartung) zur Freigabe.

## No-Delete/Audit/GoBD-Übersetzung

- „Deaktivieren" = Status; „Archivieren" = finaler Status (kein Row-Delete),
  Bestätigung Pflicht. Ausgelöste Aktionen werden auditiert (Nachweis, welche
  Fälligkeit welches Objekt erzeugt hat).

## Offene Punkte / Entscheidungen

- Begriffs-/Struktur-Inkonsistenz in Hero („Wartungsverträge" vs.
  „Wartungsaufträge"; „Kundendienst" als Modul vs. Menüpunkt) — einheitlichen
  Begriff wählen (Empfehlung: „Wartung"/„Wartungsverträge").
- Ziel-Schema (neues `maintenance` vs. `management`) — Grundsatzentscheidung.
- Fälligkeits-Scheduler (wo/wie: DB-Job, Cron, Backend-Worker) — Architektur.
- Bindung an **Liegenschaft/Einheit** statt nur Projekt/Kunde (MCN-Domäne) —
  sinnvoll, weicht bewusst von Hero ab.

## Abhängigkeiten

- `07` (workflow.task) für die Aktion „Aufgabe anlegen".
- `04` (Vorgang/Pipeline) für „Projekt/Auftrag anlegen".
- Scheduler-Infrastruktur.

## Aufwand & Priorität

- Vertrag CRUD + Liste: **M**, Phase 3.
- Fälligkeits-Automatik + Aktions-Erzeugung: **L**, Phase 3 (setzt `04`/`07` voraus).

## Screenshots zur Vorlage (Wiedererkennung)

- Quelldatei oben, image1–image7: Liste, Anlegen-Formular mit Reitern
  Erinnerung/Dokumente, `[Aktion auslösen]` — HOCH (Kundendienst-Alltag).
