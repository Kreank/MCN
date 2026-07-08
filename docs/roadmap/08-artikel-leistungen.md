# 08 — Artikel & Leistungen (Hero: Artikelstamm)

## Zweck & Hero-Entsprechung

Diese Sektion ist der **Stammdaten-Zulieferer** des Leitstands: Artikel
(Material/Produkte), Leistungen (Stücklisten aus Material + Lohn), Verkaufspreis-
Formeln und die Lieferanten-Importe (DATANORM, IDS-Connect). Sie entspricht 1:1
Heros Hauptmenüpunkt **„Artikelstamm"** mit den Unterpunkten Artikel, Leistungen,
Verkaufspreise, DATANORM/Rohstoffzuschläge und Lager. Kern-Nutzen: gepflegte
Artikel/Leistungen werden im Dokumenten-Editor (`05`) per Drag & Drop zu
Belegpositionen — die Kalkulationswerte (EK, VK-Gruppe, Listenpreis) werden dort
**eingefroren** (Snapshot). MCN weicht bewusst an einer Stelle grundlegend von
Hero ab: die DB führt **keine Bestandsführung** (Beschluss B-26, Migration 0028),
d. h. Heros komplettes „Lager"-Subsystem hat aktuell **kein DB-Fundament** — das
ist der zentrale offene Punkt dieser Sektion (siehe unten).

**Abgedeckte Hero-Quelldateien:**
- `Artikelstamm\Wie kann ich einen Artikel hinzufügen, erstellen oder bearbeiten\…txt`
- `Artikelstamm\Wie kann ich Leistungen hinzufügen, erstellen und bearbeiten\…txt`
- `Artikelstamm\Wie verwalte ich meine Verkaufspreise\…txt`
- `Artikelstamm\Anleitung für Datanorm Importe\…txt`
- `Artikelstamm\Was sind Datanorm Artikel\…txt`
- `Artikelstamm\Welche Möglichkeiten gibt es, Materialpreise zu aktualisieren\…txt`
- `Artikelstamm\Preise aus dem Artikelstamm aktualisieren\…txt` (Editor-Querverweis → `05`)
- `Artikelstamm\Wie kann ich die Rohstoffzuschläge ändern\…txt`
- `Artikelstamm\Wie kann ich meine Suchergebnisse mithilfe von Suchoperatoren verbessern\…txt`
- `Artikelstamm\Wie lösche ich den Artikelstamm eines Lieferanten\…txt`
- `Artikelstamm\Lagerverwaltung\Lagerverwaltung.txt`

## Ziel-Navigation & Routen

Neuer Sidebar-Punkt **„Artikel & Leistungen"** (Hero: „Artikelstamm"). Interne
Reiter/Routen spiegeln Heros Unterpunkte:

- `/artikel` — Artikel-Übersicht (Tabelle, Spaltenauswahl, Gruppenaktionen,
  Suchoperatoren, Filter „Artikelstämme"/Lieferant)
  - `/artikel/neu`, `/artikel/:id` — Anlegen/Bearbeiten mit Tab **„Kalkulation"**
- `/leistungen` — Leistungen-Übersicht
  - `/leistungen/neu`, `/leistungen/:id` — Editor mit Drag&Drop-Bereich
    „Enthaltene Positionen" (Artikel + Lohngruppen)
- `/verkaufspreise` — VK-Gruppen-Übersicht (Formeln bearbeiten)
- `/import` — Import-Einstieg (Dropdown wie Hero: **DATANORM**, **Lieferantenartikel
  / IDS-Connect**, Excel [nur Support-Hinweis], Manuell anlegen)
  - Reiter **„Rohstoffzuschläge"** innerhalb des DATANORM-Bereichs
  - `/import/lieferanten` — Anbindungs-Registry (`supplier_connection`)
- `/lager` — **nur als Platzhalter/„Modul folgt"** (kein DB-Fundament, siehe Offene
  Punkte). NICHT in Phase 2 bauen, bevor die Bestandsführungs-Entscheidung fällt.

Globale Muster (Liste, Mappe, Dialog, Statuswechsel, Logbuch) siehe
`00-informationsarchitektur.md`; hier nur Sektionsspezifisches.

## Screens & Komponenten

### Artikel-Übersicht (Liste)
- **UI-Typ & Aufbau:** Ressourcen-Liste (shared). Spalten Artikelnummer,
  Beschreibung, Hersteller, Warengruppe (`product_group`), Einheit, Positionsart
  (`line_type`), Listenpreis, Status. Suchleiste **mit Suchoperatoren** (`+` UND,
  `|` ODER, `*` Platzhalter). Spaltenauswahl (Burger-Menü). Filter **„Artikelstämme"**
  = Herkunftsfilter über `article_supplier_reference.source_namespace`/Lieferant.
  Aktionen oben rechts `[+ Artikel]` (Dropdown = Import-Einstieg). Zeilen-Aktionen:
  Bearbeiten `[Stift]`, Kopieren, **Deaktivieren** (nicht Löschen). Feld
  `[Gruppenaktion]` für Massenoperationen (Präfix/Suffix, Suchen&Ersetzen,
  Deaktivieren eines Lieferanten-Stamms).
- **Zustände:** Leerzustand („noch keine Artikel — importieren oder anlegen");
  INAKTIVE Artikel ausgegraut + Textbadge (Status nie nur über Farbe). Große
  Datenmengen (Doku: 285k Artikel) → serverseitige Pagination + Trigramm-Suche
  (`idx_article_description_trgm`).
- **Wiederverwendet:** Ressourcen-Liste, Statuswechsel-Steuer. **Neu:**
  Suchoperator-Parser, Gruppenaktion-Panel, „Artikelstämme"-Herkunftsfilter.

### Artikel-Detail / Anlegen-Bearbeiten (Dialog/Route)
- **UI-Typ & Aufbau:** Formular. Kopf: Artikelnummer (Pflicht, unique),
  Beschreibung (Pflicht), Langbeschreibung, GTIN, Hersteller/-nummer, Einheit,
  Positionsart, Warengruppe. Tab **„Kalkulation"**: `list_price` (Listenpreis) +
  je Artikel eine oder mehrere **VK-Varianten** (`article_sale_price`): entweder
  VK-Gruppe (`sale_price_group_id`, Formel) **oder** Festpreis (`fixed_price`) —
  genau eines; genau **eine** `is_standard`-Variante. Der Verkaufspreis wird aus
  der gewählten Formel berechnet und live angezeigt (EK/Listenpreis + Auf-/Abschlag
  % oder Betrag). Buttons `[Speichern]` / `[Speichern und neu]`.
- **Zustände:** Validierung der XOR-Constraints (Gruppe vs. Festpreis) im
  Frontend spiegeln; EK-Feld ist read-only-Anzeige aus Lieferantenreferenz
  (`last_purchase_price`), nicht frei am Artikel editierbar.
- **Wiederverwendet:** Anlege-/Bearbeiten-Dialog. **Neu:** Kalkulations-Tab mit
  Live-VK-Vorschau, VK-Varianten-Verwaltung.

### Leistungen-Übersicht + Editor
- **UI-Typ & Aufbau:** Liste (Nummer, Name, Einheit, Status). Editor: Name,
  interner Name, Einheit, Beschreibung + Bereich **„Enthaltene Positionen"**
  (`assembly_component`): per Drag&Drop entweder **Material** (Artikel + Menge)
  **oder** **Lohn** (Lohngruppe + Minuten) — nie beides je Position (DB-CHECK).
  Positionsreihenfolge (`position`). `[Speichern]`.
- **Zustände:** kalkulierter Gesamtpreis (Material-VK + Lohn über
  `wage_group.hourly_rate`) live; Lohnanteil fließt später in Soll/Ist des Projekts.
- **Wiederverwendet:** Liste, Dialog. **Neu:** Stücklisten-Editor mit typisierten
  Drag&Drop-Zeilen (Material/Lohn), Kalkulationsvorschau.

### Verkaufspreise (VK-Gruppen)
- **UI-Typ & Aufbau:** Liste der `sale_price_group`. Bearbeiten: Name, Basis
  (`calc_basis` EK/LISTENPREIS), Operator (AUFSCHLAG/ABSCHLAG), % **oder** Betrag
  (XOR), Status. Hero sagt hier „löschen" → MCN: **deaktivieren** (INAKTIV), da
  Belegpositionen die Gruppe referenzieren/eingefroren haben.
- **Zustände:** Doku sehr knapp → Screen-Detailtiefe **OFFEN** (siehe unten).
- **Wiederverwendet:** Liste, Dialog.

### DATANORM-Import (Wizard) + Rohstoffzuschläge
- **UI-Typ & Aufbau:** Mehrstufiger Modal-Wizard. Schritt 1: Lieferant wählen
  (bestehende `identity.party` mit Kategorie „Lieferant") oder anlegen — bzw.
  eine `supplier_connection` (Namespace). Schritt 2: DATANORM-ZIP wählen
  (`[+ DATANORM-Datei auswählen]`), Schritt 3: `[Hochladen]` → Import. Serverseitig:
  Preissemantik (Migration 0037) — Preiskennzeichen 1 = Listenpreis (→ EK = Liste ×
  (1 − Rabattgruppe)), 2 = Nettopreis (= EK); Preiseinheit 0/1/2/3 = je 1/10/100/1000
  (Migration 0039) wird auf „je Stück" umgerechnet. Rabattgruppen aus `.RAB` →
  `supplier_discount_group`. **Der Kunden-VK wird NIE aus DATANORM gesetzt** (nur
  VK-Gruppen-Formeln). Reiter **„Rohstoffzuschläge"**: vor dem (Re-)Upload zu
  erfassen, wirkt beim nächsten Upload — konkrete Felder **OFFEN**.
- **Zustände:** Voraussetzungen prüfen/anzeigen: ZIP ohne Unterordner, nur
  DATANORM-Formate (.001–.999, .RAB, .WRG), min. Artikelstamm-Datei; **DATANORM v3
  nicht unterstützt** → Hinweis; Fortschritt/Ergebnisbericht (X neu / Y aktualisiert).
- **Wiederverwendet:** Wizard-Muster. **Neu:** DATANORM-Parser (Backend-Job),
  Import-Ergebnis-Report, Rohstoffzuschlag-Editor.

### IDS-Connect / Anbindungen (Lieferanten-Registry)
- **UI-Typ & Aufbau:** Liste/Verwaltung der `supplier_connection` (Label,
  Namespace, source_system, shop_url, Status, `last_import_at`). Anlegen neuer
  Anbindungen (G.U.T., Vaillant, Reisser, Viessmann …). Zugangsdaten liegen **nur
  als Verweis** (`credential_reference`) auf den Secret-Store — **niemals das Secret
  in DB/UI anzeigen** (CLAUDE.md). Der eigentliche Warenkorb-Rückfluss (IDS-Connect
  Shop-Roundtrip) ist App-Schicht/Backend.
- **Zustände:** Anbindung deaktivierbar (INAKTIV), nicht löschbar; Namespace
  unveränderlich (Artikelreferenzen hängen daran).

### Lager (Platzhalter)
- **UI-Typ & Aufbau:** Vorerst **nur „Modul folgt"-Platzhalter.** Heros
  Lagerartikel/Buchungen/Lagerbuch/FIFO haben **kein DB-Fundament** (B-26). Erst
  nach fachlicher Entscheidung + eigener DB-Migration (`inventory`-Schema)
  spezifizieren. Layout-Vorlage (bei späterem Bau): 3-Bereiche-Detailseite,
  Buchungs-Wizard, Positionstabelle, chronologisches read-only Lagerbuch.

## API-Endpunkte (django-ninja)

Lesen offen (Dev-Phase), Schreiben Session + `app_user`, **immer** über
`business_transaction`. Details/Globales siehe `00`.

| Methode | Pfad | Zweck | Auth | Service-Funktion |
|---|---|---|---|---|
| GET | `/api/pricing/articles` | Artikel-Liste (Filter, Suchoperatoren, Herkunft, Pagination) | offen | `list_articles` |
| GET | `/api/pricing/articles/{id}` | Artikel-Detail inkl. VK-Varianten | offen | `get_article` |
| POST | `/api/pricing/articles` | Artikel anlegen | Session | `create_article` (bt) |
| PATCH | `/api/pricing/articles/{id}` | Artikel bearbeiten | Session | `update_article` (bt) |
| POST | `/api/pricing/articles/{id}/deactivate` | Deaktivieren (statt Löschen) | Session | `deactivate_article` (bt) |
| POST | `/api/pricing/articles/bulk-action` | Gruppenaktion (Präfix/Suffix, Deaktivieren, Lieferantenstamm) | Session | `bulk_article_action` (bt) |
| GET/POST/PATCH | `/api/pricing/sale-price-groups[/{id}]` | VK-Gruppen (Formeln) lesen/anlegen/ändern | tw. Session | `*_sale_price_group` (bt) |
| GET/POST/PATCH | `/api/pricing/assemblies[/{id}]` | Leistungen inkl. Komponenten | tw. Session | `*_assembly` (bt) |
| GET/POST/PATCH | `/api/pricing/wage-groups[/{id}]` | Lohngruppen (auch von `12`) | tw. Session | `*_wage_group` (bt) |
| GET/POST/PATCH | `/api/pricing/supplier-connections[/{id}]` | IDS-/Import-Anbindungen | tw. Session | `*_supplier_connection` (bt) |
| POST | `/api/pricing/imports/datanorm` | DATANORM-ZIP-Import (Job) | Session | `import_datanorm` (bt) |
| GET/PUT | `/api/pricing/raw-material-surcharges` | Rohstoffzuschläge (OFFEN) | Session | `*_raw_material_surcharge` (bt) |

Preis-aktualisieren-Endpunkt lebt fachlich im Beleg/Editor (`05`), zieht aber
Artikelstamm-Preise (`article_sale_price`, `list_price`).

## DB-Bezug

Schema **`pricing`** (Migrationen 0028, 0029, 0033, 0034, 0037, 0038, 0039, 0040):
- `article` — Stammartikel; `status` ∈ {AKTIV, INAKTIV}; `line_type` ∈ {MATERIAL,
  ARBEITSZEIT, PAUSCHALE, FREMDLEISTUNG, FAHRT, ZUSCHLAG}; `list_price`; `version`.
  Trigger: `set_updated_at`, `audit_row_update`, **No-Delete** (`forbid_mutation`),
  No-Truncate. GTIN unique; Trigramm-Indizes für Suche.
- `article_supplier_reference` — Lieferantenbezüge (source_system/namespace/
  supplier_article_number, `last_purchase_price` = EK, `list_price`). **Identität
  unveränderlich** (`protect_supplier_ref`); Beendigung via `valid_until`, kein Löschen.
- `article_sale_price` — VK-Varianten je Artikel (Gruppe XOR Festpreis, genau eine
  `is_standard`).
- `sale_price_group` — VK-Formel (calc_basis EK/LISTENPREIS, AUFSCHLAG/ABSCHLAG,
  % XOR Betrag).
- `assembly` / `assembly_component` — Leistungen als Stückliste (Material XOR Lohn
  je Zeile, DB-CHECK).
- `wage_group` (+`cost_rate` aus 0034) — Lohngruppen (LOHN/MASCHINE), `hourly_rate`
  = Verrechnungssatz.
- `supplier_connection` — Anbindungs-Registry; Namespace unveränderlich
  (`protect_supplier_connection`), No-Delete; `credential_reference` (Secret-Store).
- `supplier_discount_group` — DATANORM-Rabattgruppen (`.RAB`).
- Suche: `pg_trgm`-GIN-Indizes (0038); Preiseinheit (0039), Anbindungsart (0040).

**Constraints, die die UI respektieren muss:** XOR bei VK-Variante (Gruppe/Festpreis)
und Leistungskomponente (Material/Lohn); genau eine Standard-VK-Variante; Artikel-/
Anbindungs-/Referenz-Identität ist unveränderlich; **kein DELETE** (nur INAKTIV bzw.
`valid_until`). **Kein Lager-/Bestands-Schema vorhanden** (B-26).

## KI-Andockpunkte (`ai.ai_proposal`)

Die KI schlägt über dieselben Service-Tore vor wie ein Mensch (siehe `00`):
- **Neuanlage/Anreicherung** von Artikeln (fehlende Langbeschreibung, GTIN,
  Warengruppe, Hersteller aus Import-/Gerätewissen ergänzen).
- **VK-Gruppen-Zuordnung**: passende Aufschlagsgruppe je Warengruppe vorschlagen.
- **Leistungs-Stücklisten**: aus wiederkehrenden Belegpositionen eine neue
  `assembly` vorschlagen (Material + Lohn).
- **Preis-Update-Vorschläge** nach DATANORM-Import (welche offenen Belege betroffen
  sind → Übergabe an `05`).
- **Dublettenerkennung** (gleicher Artikel aus mehreren Lieferanten) und
  Deaktivierungsvorschläge — nie automatisches Löschen, immer Proposal.

## No-Delete/Audit/GoBD-Übersetzung

Hero bietet „löschen" an Artikeln, Verkaufspreisen und ganzen Lieferanten-
Artikelstämmen — MCN übersetzt durchgehend:
- **Artikel „löschen"** → `status = INAKTIV` (`deactivate_article`). Der DB-Trigger
  `trg_article_no_delete` verbietet DELETE physisch. INAKTIVE Artikel bleiben für
  historische Belege referenzierbar, verschwinden aber aus Auswahl/Drag&Drop.
- **Verkaufspreis/VK-Gruppe „löschen"** → `status = INAKTIV` (Belege haben Werte
  ohnehin eingefroren; Formel bleibt für Nachvollziehbarkeit erhalten).
- **Lieferanten-Artikelstamm „löschen"** (Heros Gruppenaktion) → **Bulk-Deaktivierung**
  aller Artikel des gefilterten `source_namespace` **plus** Beendigung der
  `article_supplier_reference` über `valid_until` (kein Löschen — EK-/Import-Historie
  bleibt, `protect_supplier_ref`). UI muss klar „deaktivieren/ausblenden" statt
  „löschen" benennen.
- **Anbindung „entfernen"** → `supplier_connection.status = INACTIVE`; Namespace
  bleibt (Referenzen hängen daran).
- Alle Änderungen laufen über `audit_row_update` (Audit-Trail); Import-Preise
  historisiert über `article_supplier_reference` statt Überschreibung.

## Offene Punkte / Entscheidungen

1. **Lager/Bestandsführung — Grundsatzentscheidung (BLOCKIEREND für `/lager`).**
   DB-Beschluss **B-26 (0028): keine Bestandsführung**, Artikelstamm ist reine
   Stammdaten. Heros komplettes Lager-Subsystem (Lagerartikel, Buchungen, FIFO-EK,
   unveränderliches Lagerbuch) hat damit **kein Fundament**. Entweder Feature
   streichen/aufschieben **oder** neues Schema `inventory` (Lagerartikel, Buchung
   append-only als Lagerbuch, FIFO-Bewertung) beschließen und migrieren. **Fachliche
   Entscheidung nötig** — bis dahin `/lager` nur Platzhalter.
2. **Verkaufspreise-Screen** in Hero extrem knapp dokumentiert (nur Bearbeiten/
   Löschen) — Felder/Verhältnis zur Kalkulationsformel unklar. OFFEN (Spec).
3. **Rohstoffzuschläge**: konkrete Einstellfelder im Reiter nicht dokumentiert.
   OFFEN (Spec) — DB-Ablage (eigene Tabelle vs. Erweiterung `supplier_discount_group`)
   festzulegen.
4. **„Artikelstämme" (Herkunfts-Teilmengen) vs. einzelne Artikel**: in MCN über
   `article_supplier_reference.source_namespace` abgebildet — als Filter-/
   Gruppierungskonzept in der UI zu bestätigen.
5. **Excel-Import**: in Hero nur „Support kontaktieren", kein Self-Service —
   für MCN vermutlich nicht 1:1; als reiner Support-/Backend-Prozess behandeln.
6. **DATANORM v3**: nicht unterstützt (nur v4/v5); v3 nur via Konvertierung — beim
   Parser-Bau berücksichtigen (klare Fehlermeldung).
7. **Korrekturbuchung** (Reduzierung teilverbuchter Mengen): nur relevant, falls
   Lager gebaut wird — hängt an Punkt 1.

## Abhängigkeiten

- **Vorher nötig:** Auth/Session + Rechtematrix (`security`) für Schreib-UIs;
  shared Ressourcen-Liste + Anlege-/Bearbeiten-Dialog (`00`, Phase 0);
  `identity.party` mit Kategorie „Lieferant" (`02`) für DATANORM/IDS.
- **DB vorhanden:** `pricing`-Schema komplett (Migr. 0028–0040) außer Lager.
- **Liefert an:** Dokumenten-Editor (`05`) — Artikel/Leistungen/VK-Gruppen sind
  Positionsquelle; Preis-aktualisieren-Dialog dort. Lohngruppen geteilt mit `12`.
- **Backend neu:** DATANORM-Parser-Job, IDS-Connect-Client (Secret-Store-Anbindung),
  Suchoperator→SQL-Übersetzung (Trigramm/ILIKE).

## Aufwand & Priorität

Empfohlene Phase: **Phase 2** (Zulieferer für Beleg-Editor `05`), kann laut `00`
vorgezogen werden, da `05` darauf aufbaut. Reihenfolge:

| Screen | Größe | Hinweis |
|---|---|---|
| Artikel-Übersicht + Suchoperatoren | M | zuerst (Drag&Drop-Quelle für `05`) |
| Artikel-Detail/Kalkulation + VK-Varianten | M | Live-VK-Vorschau |
| VK-Gruppen | S | Formel-CRUD (Screen-Detail OFFEN) |
| Leistungen-Editor (Stückliste) | L | typisiertes Drag&Drop, Kalkulation |
| DATANORM-Import-Wizard + Parser | XL | Backend-Job, Preissemantik, Reports |
| IDS-Connect-Anbindungen | M | Secret-Store, Shop-Roundtrip Backend |
| Rohstoffzuschläge | S | OFFEN — nach Klärung |
| Gruppenaktion / Lieferantenstamm-Deaktivierung | M | No-Delete-Übersetzung |
| Lager | — | BLOCKIERT bis B-26-Entscheidung |

## Screenshots zur Vorlage (Wiedererkennung)

- `Wie kann ich einen Artikel hinzufügen…` image1–image11 (HOCH): Übersichtstabelle,
  Erstellungsdialog mit **Kalkulationsreiter**, Gruppenaktion-Fenster — layoutprägend.
- `Wie kann ich Leistungen hinzufügen…` image1–image8 (HOCH): Leistungs-Editor mit
  Bereich „Enthaltene Positionen" (Drag&Drop) — Vorlage für Stücklisten-Editor.
- `Anleitung für Datanorm Importe` image1–image3 (HOCH): Lieferantenauswahl +
  Datei-Upload — Vorlage für Import-Wizard.
- `Preise aus dem Artikelstamm aktualisieren` image1–image5 (Editor-Kontext `05`).
- `Lagerverwaltung` image1–image21 (HOCH) — nur als Referenz, falls Lager-Modul
  beschlossen wird (3-Bereiche-Detailseite, Buchungs-Wizard, Positionstabelle).
