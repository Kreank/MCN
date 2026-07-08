# 03 — Liegenschaften (Hero: Kontakte → Objektadressen, gehoben)

## Zweck & Hero-Entsprechung

Liegenschaften ist die MCN-eigene Hebung dessen, was Hero nur als
„Objektadressen" innerhalb der Kontaktmappe führt. Weil MCN auf
Handwerk/**Gebäudeservice** zielt, ist das Objekt (Liegenschaft → Gebäude →
Einheit) eine erstklassige Entität mit Eigentums-, Verwaltungs- und
Nutzungsbezug. Hero hat dafür keinen eigenen Bereich; die fachliche Tiefe
(Eigentümergemeinschaft, Verwaltungsmandat, Belegung) stammt aus dem MCN-
DB-Schema, nicht aus Hero.

**Abgedeckte Hero-Quelldateien:** keine direkte 1:1-Datei — der Objektbezug ist
in Hero über die Kontakt-Kategorie „Objektadressen" verstreut (siehe `02`,
Reiter „Objektadressen"). Diese Sektion ist überwiegend MCN-Eigenleistung auf
Basis der DB-Schemas `property`/`tenure`/`management`.

## Ziel-Navigation & Routen

- `/liegenschaften` — Liste (**bereits gebaut**: Suche, Typ-Segmentfilter,
  Pagination, Objektnummer, Ort).
- `/liegenschaften/:id` — Detail-„Mappe" (noch zu bauen), Tabs:
  - **Übersicht** (Stammdaten, Adresse, Status)
  - **Struktur** (Gebäude → Einheiten, Baum)
  - **Beteiligte** (Party-Rollen: Eigentümergemeinschaft/Eigentümer/Betrieb/
    Hausmeisterei + Verwaltungsmandat/Verwalter)
  - **Eigentum** (tenure: Eigentumsperioden/-anteile je Einheit)
  - **Belegung** (tenure: Mieter/Nutzer je Einheit)
  - **Dokumente** / **Aufgaben** (Querverweise → `05`, `07`)
- Verlinkung aus dem Kontakt-Detail (`02`) und umgekehrt (Party ↔ Rolle).

## Screens & Komponenten

### Liste (fertig)
- Ressourcen-Liste (shared, siehe `00`). Status: ✅ live.

### Detail-Mappe (offen)
- **Kopf:** Objektnummer, Name, Typ, Status; Aktionen (Bearbeiten, Status).
- **Struktur-Tab:** Baum Gebäude→Einheit; Anlegen über `add_building`/`add_unit`
  (Service existiert bereits). Einheiten-Typen als Codeliste.
- **Beteiligte-Tab:** aktuelle vs. historische Rollen (`is_current`,
  bereits im Detail-API), plus Verwaltungsmandat (`management.management_mandate`
  — Verwalter, mandate_type, Laufzeit). Anlegen über `add_party_role` (existiert).
- **Eigentum/Belegung-Tabs:** lesen `tenure.ownership_period/-interest` bzw.
  `tenure.occupancy/-party`. Append-only, zeitscheibenbasiert.

### Anlegen/Bearbeiten
- Liegenschaft anlegen (Adresse + Property) — Service + `POST /api/property/properties`
  **bereits gebaut**. Bearbeiten (Name/Status) noch offen.

## API-Endpunkte (django-ninja)

| Methode | Pfad | Zweck | Auth | Service |
|---|---|---|---|---|
| GET | `/api/property/properties` | Liste | offen | — (ORM) |
| GET | `/api/property/properties/{id}` | Detail (Adresse, Gebäude/Einheiten, Rollen) | offen | — |
| POST | `/api/property/properties` | Anlegen | Session | `create_property` |
| — | (offen) Gebäude/Einheit/Rolle anlegen als Endpoints | | Session | `add_building`/`add_unit`/`add_party_role` |
| — | (offen) Mandat, Eigentum, Belegung lesen/anlegen | | | neu |

Die ersten drei sind **live**. Service-Funktionen für Gebäude/Einheit/Rolle
existieren, aber ohne API-Endpoint/UI.

## DB-Bezug

- `property.property/building/unit/property_party_role` (Migration 0004),
  `identity.address` (0003).
- `tenure.*` (0005): Eigentum/Belegung, append-only + Audit + No-Merged.
- `management.management_mandate/-responsibility/party_authority` (0006):
  Verwaltung. Statusautomat `management_mandate.status` (ACTIVE/ENDED).
- Schutz-Trigger (0009): No-Delete/Audit/No-Merged auf Rollen, tenure, management.

## KI-Andockpunkte (`ai.ai_proposal`)

- KI schlägt bei neuem Vorgang die betroffene Liegenschaft/Einheit vor.
- KI erkennt aus eingehenden Dokumenten (Verwalterschreiben) neue
  Eigentümer/Verwalter und schlägt Rollen/Mandate zur Freigabe vor.

## No-Delete/Audit/GoBD-Übersetzung

- Liegenschaft „deaktivieren" = `status=INACTIVE` (kein Delete).
- Rollen/Eigentum/Belegung: **append-only**, Korrektur = neue Zeitscheibe mit
  `valid_until`; DB-Trigger verbieten DELETE bereits physisch.

## Offene Punkte / Entscheidungen

- Verwalter (management_mandate) im Detail: in der ersten Slice-Runde bewusst
  zurückgestellt — jetzt nachziehen.
- Liegenschaft als eigener Nav-Punkt (aktuell) vs. Reiter in Kontakten (Hero) —
  Entscheidung in `00` (Empfehlung: eigener Punkt).
- Bearbeiten von Property-Stammdaten: kein Statusautomat/kein Löschverbot auf
  `property.property` — Update ist frei; UI muss trotzdem über
  `business_transaction`/Service laufen.

## Abhängigkeiten

- Auth/Rechte (Phase 0) für Schreib-Tabs.
- Shared „Detail-Mappe"-Komponente (`00`).
- Kontakte-Detail (`02`) für die Party-Verlinkung.

## Aufwand & Priorität

- Detail-Mappe (Übersicht+Struktur+Beteiligte): **M**, Phase 0/1.
- Eigentum/Belegung-Tabs: **M**, Phase 1 (tenure-Lesepfade).
- Verwaltungsmandat-Tab: **S–M**, Phase 1.

## Screenshots zur Vorlage (Wiedererkennung)

Keine Hero-Vorlage (MCN-eigen). Orientierung am bestehenden Kontakte/
Liegenschaften-Listendesign und an der Kontaktmappe (`02`) für die Tab-Struktur.
