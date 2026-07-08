# 09 — Buchhaltung (Hero: Buchhaltung)

## Zweck & Hero-Entsprechung

Diese Sektion spiegelt Hero's Sidebar-Bereich **Buchhaltung** — das
buchungsrelevante Nachlager zum Beleg-/Dokumentenbereich (`05`). Sie umfasst die
Rechnungs- und Belegübersicht (offene Posten, Export), die Zahlungserfassung
inkl. Teilzahlung, den GoBD-Kernprozess **Storno/Rechnungskorrektur** (kein
Löschen, nur Folgebeleg), das mehrstufige **Mahnwesen** mit Mahnlauf, die
externen Anbindungen (DATEV, Lexware Office) sowie die buchhalterischen
**Stammdaten** (Buchungskonten, Kostenstellen, USt-Regeln, Nummernkreise).
Wichtig: Wie Hero ist MCN **kein vollwertiges Buchhaltungssystem** — es bereitet
Belege für Steuerberater/externe Systeme auf und führt offene Posten, solange
keine externe Schnittstelle die Führung übernimmt. Baut fachlich auf `05`
(Rechnung/Beleg-Erstellung, Editor) auf und teilt sich mit ihm die
`invoicing`-Tabellen. Globale Prinzipien (KI-first, No-Delete/Audit,
Service-Schicht, Auth, Design) siehe `00-informationsarchitektur.md`.

**Abgedeckte Hero-Quelldateien:**
- `Buchhaltung/Buchhaltung mit HERO Software/Buchhaltung mit HERO Software.txt`
- `Buchhaltung/Buchhaltung mit Lexware Office/Buchhaltung mit Lexware Office.txt`
- `Buchhaltung/DATEV Schnittstelle einrichten/DATEV Schnittstelle einrichten.txt`
- `Buchhaltung/Mahnungen und Zahlungserinnerungen mit HERO erstellen/Mahnungen und Zahlungserinnerungen mit HERO erstellen.txt`
- `Buchhaltung/Prozess für das Stornieren und Korrigieren von Rechnungen (Rechnungskorrektur + Stornorechnung)/Prozess für das Stornieren und Korrigieren von Rechnungen (Rechnungskorrektur + Stornorechnung).txt`
- `Buchhaltung/Rechnungen nachträglich bearbeiten/Rechnungen nachträglich bearbeiten.txt`
- `Buchhaltung/Schnittstelle für Online Banking/Schnittstelle für Online Banking.txt`
- `Buchhaltung/Was kann ich tun, wenn ein Dokument nicht zu Lexware Office übertragen wurde/… .txt`
- `Buchhaltung/Wie kann ich eine Teilzahlung (Anzahlung) erfassen/… .txt`
- `Buchhaltung/Wie kann ich Eingangsrechnungen und Belege erfassen/… .txt`
- `Buchhaltung/Wie verwalte ich meine Nummernkreise/… .txt`
- `Buchhaltung/Wie wird der Zahlungsstatus einer Rechnung bei der Stornierung berücksichtigt/… .txt`
- `Buchhaltung/Wo finde ich alle meine offenen Rechnungen auf einem Blick/… .txt`
- `Buchhaltung/Wo finde ich die Kostenstellen/… .txt`
- `Buchhaltung/Wo finde ich meine Buchungskonten/… .txt`

## Ziel-Navigation & Routen

Sidebar-Punkt **Buchhaltung** (eigenes Icon), gespiegelt auf Hero's
Unterstruktur. Startseiten-Kachel „Buchhaltung" (→ `01`) mit Button „Zum
Mahnwesen" verlinkt auf `/buchhaltung/mahnwesen`.

| Route | Inhalt |
|---|---|
| `/buchhaltung/dokumente` | Ausgangsrechnungen: offene Posten, Zahlungserfassung, Storno/Korrektur, Export |
| `/buchhaltung/dokumente/:id` | Rechnungs-Detail (read-only ab Veröffentlichung) inkl. Referenzbeleg-Anzeige |
| `/buchhaltung/belege` | Ein-/Ausgaben (Eingangsrechnungen/Belege), Belegerfassung, Export |
| `/buchhaltung/mahnwesen` | Mahntabelle mit Tabs (Alle · Überfällig · je Mahnstufe), Mahnlauf |
| `/buchhaltung/einstellungen` | Tab-Seite: Allgemein · Buchungskonten · Kostenstellen · USt-Regeln · Nummernkreise · Mahnwesen |

Schnittstellen-Konfiguration (DATEV, Lexware Office) liegt bereichsübergreifend
unter `/einstellungen/schnittstellen` (siehe `13`), wird aber aus der
Buchhaltung heraus verlinkt.

Tab-Reihenfolge und Begriffe von Hero übernehmen (Wiedererkennung): Dokumente →
Belege → Mahnwesen → Einstellungen.

## Screens & Komponenten

### Dokumente — Rechnungsliste (offene Posten)
- **UI-Typ & Aufbau:** Ressourcen-Liste (shared). Spalten: Belegnummer,
  Belegart, Kontakt/Schuldner, Betrag (brutto), Fälligkeit, **Zahlungsstatus**
  (Offen/Teilzahlung/Bezahlt — abgeleitet, nie nur Farbe: Text+Icon),
  Dokumentenstatus (Entwurf/Veröffentlicht/Storniert), **Exportdatum**
  (spaltenweise ein-/ausblendbar). Filter-Segmente: Zahlungsstatus (Default-Ziel
  „offen" = Hero „offene Rechnungen auf einen Blick"), Zeitraum, Belegart.
  Primäraktion oben rechts: **[Rechnungen herunterladen] / [Export]** → Untermenü
  CSV / DATEV-CSV (Dialog: Kontenrahmen, Mandantennummer, Beginn Wirtschaftsjahr,
  Beraternummer, Zeitraum, Checkboxen Belege/Rechnungen/„bereits exportierte
  erneut"). Zeilenaktionen: **Geldschein-Icon** (Zahlung erfassen), Kontextmenü
  (drei Punkte): **Stornieren**, **Rechnungskorrektur**, **Zahlung löschen**
  (→ Historie), Info-Icon (Übertragungsstatus extern).
- **Zustände:** Laden (Skeleton-Liste), Leer („keine offenen Rechnungen"),
  Fehler. Storno/Korrektur/Zahlung nur mit Session+Recht; Lesen offen (Dev).
- **Wiederverwendet:** Ressourcen-Liste, Statuswechsel-Steuer, Export-Menü
  (dataviz-konform). **Neu:** Zahlungs-Dialog, Storno/Korrektur-Flow, Export-Dialog.

### Dokumente — Zahlungs-Dialog (Teilzahlung/Anzahlung)
- **UI-Typ & Aufbau:** Modal am Geldschein-Icon. Eingabe **Betrag + Bezahldatum**
  → Speichern; offener Betrag reduziert sich automatisch. Bei erneutem Öffnen:
  Liste bereits erfasster (Teil-)Zahlungen. Bei vollständig bezahlten Rechnungen
  Historie über Kontextmenü → „Zahlung löschen" einsehbar (Hero-Muster:
  Fenster mit Abbrechen schließen). Zahlungen sind **append-only** —
  „Zahlung löschen" ist eine Storno-Buchung, keine physische Löschung.
- **Zustände:** Deaktiviert, solange Rechnung nicht veröffentlicht (B-23) oder
  eine aktive externe Schnittstelle die Zahlungserfassung führt (dann Hinweis
  „Zahlungen werden in DATEV/Lexware erfasst").

### Dokumente — Storno / Rechnungskorrektur
- **UI-Typ & Aufbau:** Kontextmenü-Aktion → Bestätigungs-/Editor-Flow. **Storno**
  erzeugt Folgebeleg `invoice_type = 'STORNO'` mit invertierten Positionen
  (negativ) und `reference_invoice_id` auf den Ursprung; **Rechnungskorrektur**
  erzeugt ebenfalls einen Storno-/Gutschriftbeleg, Positionen werden auf die zu
  korrigierenden Posten reduziert. Ursprungsbeleg als Referenzdokument im Kopf
  angezeigt. Entscheidungshilfe (aus Spec) als Info-Panel: Entwurf → direkt
  ändern; festgeschrieben/versendet/bezahlt + §14-UStG-/Inhaltsfehler →
  Stornieren & neu; Minderleistung/Rücksendung/Teilstorno/Mängel/Rabatt →
  Korrektur. Der Folgebeleg durchläuft denselben Editor wie `05`.
- **Zustände:** Aktion nur auf veröffentlichten Belegen; Ursprung muss
  veröffentlicht sein (Trigger B-21). Rollen-Sichtbarkeit wie Rechnungsfreigabe.
- **Wiederverwendet:** Dokumenten-Editor (`05`), Statuswechsel-Steuer.

### Belege — Ein-/Ausgaben
- **UI-Typ & Aufbau:** Liste mit Filter Datum/Status; **[+ Beleg erfassen]** →
  Auswahl Einnahme/Ausgabe → Formular mit Datei-Upload (Bild/PDF), Reiter
  „einfach"/„erweitert", **[+ Position]**, Reiter „Allgemein" und
  „Buchungskonten" (Konto-/Kostenstellen-/USt-Zuordnung), Speichern **oder als
  Entwurf**. Export (CSV/DATEV-CSV) wie Rechnungsliste.
- **Zustände:** Entwurf vs. erfasst; Pflichtfelder inkl. manueller Fälligkeit.
- **Wiederverwendet:** Ressourcen-Liste, Anlege-/Bearbeiten-Dialog (Tabs).
  **Neu:** Belegerfassungs-Formular mit Positionsraster + Datei-Upload (MinIO).

### Mahnwesen — Mahntabelle + Mahnlauf
- **UI-Typ & Aufbau:** Tabellenübersicht mit **Tabs**: [Alle],
  [Rechnung überfällig] und je aktivierter Mahnstufe ein Tab
  („1. Zahlungserinnerung", „2. Mahnung" …). Spalten: aktuelle Mahnstufe
  (Stapel-Icon → Mahnverlauf), nächste Stufe (Tage), letzte Erinnerung (Datum),
  Zahlungsstatus (Offen/Teilzahlung), letzte Notiz, Kunde/Projekt/Betrag.
  Zeilenaktionen: **[Zahlungserinnerung erstellen]**, **[Mahnung erstellen]**
  (öffnet Editor mit definiertem Dokumententyp, offener Rechnung +
  Teilzahlungen als Positionen, Restbetrag, optional QR), **[Manuelles
  Erfassen]** (Notiz, optional Stufenwechsel), **[Mahnverlauf pausieren/
  fortsetzen]**. Oben rechts **[Mahnlauf starten]** → Liste mahnfähiger
  Rechnungen (abhängig vom Tab) mit Ausschluss-Checkboxen → **[x Mails
  verschicken]**; Ergebnisspalten E-Mail/Mahndokument/Status.
- **Zustände:** Mahnung nur auf veröffentlichte, **fällige** Rechnung; Stufen
  **lückenlos aufsteigend** (Trigger erzwingt nächste Stufe). Pausierte
  Rechnungen ausgeschlossen und markiert.
- **Wiederverwendet:** Ressourcen-Liste mit Tabs, Logbuch (Mahnverlauf =
  audit-/`dunning_notice`-gespeist), Statuswechsel-Steuer, Export-/Sammelaktion.
  **Neu:** Mahnlauf-Sammeldialog.

### Einstellungen — Tab-Seite
- **UI-Typ & Aufbau:** Tab-Container.
  - **Allgemein:** Kontenrahmen (SKR03/04), Checkbox „buchungsrelevante
    Dokumente nach Erstellung nicht festschreiben" (GoBD-Ausnahmeweg, siehe
    unten).
  - **Buchungskonten / Kostenstellen / USt-Regeln / Nummernkreise:** je eine
    Liste mit **[+ …]**, Stift (bearbeiten). USt-Regeln bilden `invoicing.tax_code`
    ab (DE_19/DE_7/DE_0/DE_13B, STB-Vorbehalt). Nummernkreise zählen **nur
    aufwärts, kein Reset** (Hinweis in der UI); Zuordnung zu Dokumententypen im
    Dokumentenkonfigurator (`05`/`13`).
  - **Mahnwesen:** bis zu **6 Stufen** (3 Zahlungserinnerungen, 3 Mahnungen),
    je Stufe [Bearbeiten]: Name, E-Mail-Template, ab Mahnstufe zusätzlich
    Dokumententyp, Intervall (Tage bis nächste Stufe), Aktivierungs-Checkbox.
- **Zustände:** CRUD nur mit Recht; „Löschen" von Stammdaten → **archivieren**
  (siehe No-Delete unten), nicht physisch löschen (Hero zeigt Mülleimer).

## API-Endpunkte (django-ninja)

Alle schreibenden Endpunkte laufen über `db_core.db_context.business_transaction`
(Session + `app_user`); lesende sind in der Dev-Phase offen (siehe `00`).

| Methode | Pfad (`/api/…`) | Zweck | Auth | Service-Funktion |
|---|---|---|---|---|
| GET | `/buchhaltung/invoices` | Rechnungsliste, Filter Zahlungs-/Dok.status, Zeitraum | offen | `list_invoices` |
| GET | `/buchhaltung/invoices/{id}` | Rechnungs-Detail inkl. Referenzbeleg, Zahlungen | offen | `get_invoice` |
| POST | `/buchhaltung/invoices/{id}/payments` | (Teil-)Zahlung erfassen (Betrag, Datum) | Session | `record_payment` |
| POST | `/buchhaltung/invoices/{id}/payments/{pid}/reverse` | Zahlung stornieren (Storno-Buchung, kein Delete) | Session | `reverse_payment` |
| POST | `/buchhaltung/invoices/{id}/cancel` | Stornorechnung erzeugen (invert. Positionen) | Session | `create_cancellation` |
| POST | `/buchhaltung/invoices/{id}/correction` | Rechnungskorrektur (Storno-Basistyp, red. Positionen) | Session | `create_correction` |
| POST | `/buchhaltung/invoices/export` | CSV/DATEV-CSV-Export (Parameter s. Dialog) | Session | `export_bookings` |
| GET | `/buchhaltung/receipts` | Belegliste (Ein-/Ausgaben), Filter Datum/Status | offen | `list_receipts` |
| POST | `/buchhaltung/receipts` | Beleg erfassen (Positionen, Datei, Entwurf) | Session | `create_receipt` |
| GET | `/buchhaltung/dunning` | Mahntabelle (Tab-/Stufenfilter) | offen | `list_dunning` |
| POST | `/buchhaltung/invoices/{id}/dunning` | Mahnstufe erzeugen (Erinnerung/Mahnung, Dokument) | Session | `issue_dunning_notice` |
| POST | `/buchhaltung/invoices/{id}/dunning/pause` | Mahnverlauf pausieren/fortsetzen | Session | `set_dunning_paused` |
| POST | `/buchhaltung/dunning/run` | Mahnlauf: Sammel-Erzeugung/-Versand (Ausschlussliste) | Session | `run_dunning_batch` |
| GET/POST/PUT | `/buchhaltung/settings/ledger-accounts` | Buchungskonten CRUD (archivieren statt löschen) | Session | `*_ledger_account` |
| GET/POST/PUT | `/buchhaltung/settings/cost-centers` | Kostenstellen CRUD | Session | `*_cost_center` |
| GET/POST/PUT | `/buchhaltung/settings/tax-rules` | USt-Regeln (`tax_code`, versioniert) | Session | `*_tax_rule` |
| GET/POST/PUT | `/buchhaltung/settings/number-ranges` | Nummernkreise (nur aufwärts) | Session | `*_number_range` |
| GET/PUT | `/buchhaltung/settings/dunning-levels` | Mahnstufen (bis 6) konfigurieren | Session | `update_dunning_level` |

Externe Schnittstellen (DATEV/Lexware: Autorisieren, Konfigurieren, Kontaktimport,
Status, Löschen) werden in `13` (Einstellungen/Schnittstellen) spezifiziert; hier
nur konsumiert (Zahlungserfassung ggf. gesperrt, Übertragungsstatus je Beleg).

## DB-Bezug

Betroffene Schemas: **`invoicing`** (Kern), **`billing`**, `content` (Mahn-/
Belegdokumente), `workflow` (Nummernkreise), `audit`.

Bestehende Tabellen (aus `db/migrations/*.sql`), deren Regeln die UI respektieren
muss:
- **`invoicing.invoice`** (`0019`): `status` ENTWURF→VEROEFFENTLICHT.
  **Veröffentlichung = Festschreibung**: danach unveränderlich (Trigger B-21,
  „Korrektur nur über Gutschrift/Storno"). `invoice_type` inkl.
  `GUTSCHRIFT`/`STORNO`; diese **erfordern** `reference_invoice_id` auf einen
  veröffentlichten Ursprungsbeleg (CHECK + Trigger P3-06: Schuldner müssen
  übereinstimmen). `invoice_number` fortlaufend, existiert **erst ab
  Veröffentlichung**, Belegkreis RE-… bzw. GS-… je Belegart. `due_date` treibt
  Fälligkeit/Mahnwesen. Zahlungsstatus (Offen/Teilzahlung/Bezahlt) ist **nicht
  gespeichert**, sondern aus `gross_total` vs. Summe der Zahlungen abgeleitet.
- **`invoicing.invoice_line`**, **`invoicing.invoice_party`** (Rolle
  `INVOICE_DEBTOR` u. a.), **`invoicing.tax_code`** (`0016`; USt-Regeln,
  versioniert, STB-Vorbehalt).
- **`invoicing.payment`** (`0025`): **append-only** (UPDATE/DELETE/TRUNCATE
  gesperrt). `payment_type` ∈ ZAHLUNG/TEILZAHLUNG/UEBERZAHLUNG/RUECKERSTATTUNG/
  STORNO_BUCHUNG. Zahlung nur auf veröffentlichte Rechnung (B-23).
  `UNIQUE(import_source, external_reference)` → idempotenter Rückimport aus DATEV/
  Lexware. „Zahlung löschen" der UI ⇒ Insert `STORNO_BUCHUNG`.
- **`invoicing.dunning_level`** (`0025`): **derzeit nur 3 Stufen geseedet**
  (Zahlungserinnerung, Mahnung 1, Mahnung 2); `fee`/`interest_note` unter
  STB-/GF-Vorbehalt (B-22). **`invoicing.dunning_notice`**: append-only,
  `UNIQUE(invoice_id, level)`, Trigger erzwingt veröffentlichte **fällige**
  Rechnung und **lückenlos aufsteigende** Stufen; `document_id → content.document`.
- **`workflow.number_range`** + `workflow.next_number()` (`0010`): Nummernkreise;
  aktuell Prefixe V/P/AU/E — **Belegkreise RE/GS „folgen in Phase 3"** (im Code
  vermerkt), sind also Teil dieser Sektion.
- **`billing.billing_instruction` / `responsibility_rule`** (`0007`):
  Abrechnungsvorschrift/Kostenträger — Grundlage für Buchungs-/Kostenstellen-
  Zuordnung.

**Neue DB-Objekte nötig** (aus Spec ableitbar, noch nicht vorhanden — als eigene
Hand-SQL-Migrationen, Schutzstandard erben):
- `invoicing.ledger_account` (Buchungskonto, Kontenrahmen SKR03/04),
  `invoicing.cost_center` (Kostenstelle) — jeweils mit Archiv-Flag statt Delete.
- Beleg-/Einnahmen-Ausgaben-Erfassung (Eingangsrechnung) inkl.
  Buchungskonto-/Kostenstellen-/USt-Zuordnung und Entwurfsstatus; Prüfen, ob
  auf `invoicing.invoice` (mit Richtung) oder eigene `receipt`-Tabelle.
- Export-/Übertragungs-Historie je Beleg (**Exportdatum**, extern.
  Übertragungsstatus + Fehlercode für DATEV/Lexware).
- RE/GS-Belegkreise in `workflow.number_range` (Prefix-CHECK erweitern) bzw.
  Mahn-Dokumententyp-Nummernkreis.
- Ausbau `dunning_level` auf 6 Stufen inkl. Aktivierung/Intervall/Template-Bezug.

Die konkrete Schema-Zuordnung (`invoicing` vs. `billing`) der
Stammdaten/Schnittstellenkonfiguration ist laut Spec **OFFEN** und in der
DB-Mapping-Phase zu entscheiden.

## KI-Andockpunkte (`ai.ai_proposal`)

Die KI schlägt vor, geht aber durch dieselben Service-Tore (siehe `00`):
- **Mahnlauf-Vorschlag:** „Diese N Rechnungen sind mahnfähig, Stufe X" — inkl.
  vorbereitetem Erinnerungs-/Mahntext; Mensch bestätigt Versand (Vier-Augen).
- **Zahlungszuordnung:** KI schlägt Zuordnung eingehender Zahlungen/Teilzahlungen
  zu offenen Posten vor (Betrag/Referenz-Match).
- **Storno/Korrektur-Empfehlung:** aus Fehlerart (z. B. §14-UStG-Fehler,
  Minderleistung) die passende Aktion (Storno vs. Korrektur) vorschlagen.
- **Beleg-Kontierung:** bei Belegerfassung Buchungskonto/Kostenstelle/USt-Regel
  vorschlagen (OCR/Kontext), Freigabe durch Buchhaltung.
- **Export-/Übertragungs-Diagnose:** bei rotem Übertragungsstatus KI-Vorschlag
  zur Ursache/Behebung (Fehlercode-Katalog aus Spec).

## No-Delete/Audit/GoBD-Übersetzung

GoBD ist hier **harte Leitplanke** — die DB setzt sie bereits physisch durch:
- **„Rechnung bearbeiten" (Hero-Ausnahmeweg):** In MCN ist eine veröffentlichte
  Rechnung unveränderlich (Trigger). Der Standardweg ist **Stornieren + neue
  Rechnung**. Der Hero-Ausnahmeweg („Festschreiben abwählen") wird als bewusste,
  audit-pflichtige **Allgemein-Einstellung** abgebildet und nur ohne aktive
  externe Schnittstelle wirksam — nicht als stiller Edit. UI zeigt Warnhinweis.
- **„Rechnung löschen":** existiert nicht → **Storno-/Gutschriftbeleg** mit
  Referenz. Zahlungsstatus-Logik bei Storno (Spec) als Statusautomat:
  Offen+Storno → beide „Bezahlt" (aus offenen Posten raus); Teilzahlung/Bezahlt+
  Storno → Ausgangsstatus bleibt, Stornobeleg „Offen" (Prüf-/Rückerstattungs-
  bedarf).
- **„Zahlung löschen":** keine physische Löschung → `payment` ist append-only,
  Korrektur über `STORNO_BUCHUNG`/`RUECKERSTATTUNG`. UI-Historie bleibt vollständig.
- **Stammdaten „löschen" (Buchungskonto/Kostenstelle/Nummernkreis):** Hero zeigt
  Mülleimer → MCN **archiviert** (Archiv-Flag), damit historische Belege ihre
  Zuordnung behalten. Nummernkreise sind ohnehin nicht rücksetzbar.
- **Mahnverlauf/Zahlungshistorie:** audit-/append-only-gespeist → vollständiger,
  unveränderlicher Nachweis (Logbuch-Komponente).

## Offene Punkte / Entscheidungen

Aus der Spec übernommen:
- **Schema-Zuordnung** Buchungskonto/Kostenstelle/USt-Regel/Nummernkreis und
  Schnittstellenkonfig zwischen `invoicing` und `billing` — OFFEN, in DB-Mapping
  klären.
- **Zwei sich ausschließende externe Wege** (DATEV vs. Lexware Office). Beide
  ändern das Verhalten grundlegend (Festschreibung, Zahlungserfassung nur extern).
  Entscheidung: 1:1 nachbilden **oder** vereinheitlichte Buchhaltungsschnittstelle.
  Empfehlung: eine abstrahierte „Buchhaltungs-Anbindung" mit Adaptern.
- **Kontenrahmen** nur SKR03/04 (Altsystem-Limit); für Nicht-DE flexibler lösen?
- **„Online Banking"** ist in Hero nur ein Verweisartikel (kein eigenes Feature)
  — in MCN nicht als eigene Bank-API bauen, nur über Anbindung.

Eigene, entscheidbar formuliert:
- **Mahnstufen 3 → 6:** DB seedet nur 3. Für Hero-Parität (6) `dunning_level`
  erweitern und je Stufe Aktivierung/Intervall/Template/Dokumententyp ergänzen.
- **Belegerfassung (Eingang):** eigene `receipt`-Tabelle oder gerichtete
  `invoice`? Entscheiden vor Belege-Screen.
- **Payment-Import-Idempotenz vs. manuelle Erfassung:** manuelle Erfassung
  braucht synthetische `import_source`/`external_reference` (z. B. `MANUAL`/UUID) —
  Konvention festlegen.

## Abhängigkeiten

- **`05` (Belege/Rechnungen)** — Rechnungs-/Beleg-Erstellung, Editor,
  Dokumentenkonfigurator (Dokumententyp „Mahnung", Nummernkreis-Zuordnung).
  Storno/Korrektur nutzt denselben Editor.
- **`08` (Artikel & Leistungen)** — Positionsdaten für Belege/Korrekturen.
- **Auth + Rechtematrix** (`security`) für alle schreibenden UIs.
- **Shared Components** (`00`): Ressourcen-Liste, Detail-Mappe, Anlege-/
  Bearbeiten-Dialog, Statuswechsel-Steuer, Logbuch, Export-Menü.
- **DB:** RE/GS-Nummernkreise, `ledger_account`/`cost_center`/Beleg-/Export-
  Historien-Migrationen (s. o.) müssen vor den zugehörigen Screens stehen.
- **`13`** für die Schnittstellen-Konfigurationsscreens (DATEV/Lexware).

## Aufwand & Priorität

Empfohlene Phase: **Phase 2 — Belegwesen** (nach `05`), siehe `00`. Reihenfolge
innerhalb der Sektion:

| Screen / Baustein | Größe | Reihenfolge |
|---|---|---|
| Dokumente-Liste + offene Posten (Filter/Export-Menü) | M | 1 |
| Zahlungs-Dialog (Teilzahlung/Historie) | S | 2 |
| Storno / Rechnungskorrektur-Flow (+ Statusautomat) | L | 3 |
| Mahnwesen-Tabelle + Mahnlauf | L | 4 |
| Einstellungen (Buchungskonten/Kostenstellen/USt/Nummernkreise) | M | 5 |
| Belege (Ein-/Ausgaben-Erfassung, Upload) | M | 6 |
| DATEV/Lexware-Anbindung (großteils in `13`) | XL | 7 |

Storno/Korrektur und Mahnwesen sind die wiedererkennungs- und
compliance-kritischen Kernstücke (Hero-Wiedererkennung HOCH) und sollten zuerst
belastbar stehen.

## Screenshots zur Vorlage (Wiedererkennung)

Layout-prägend, beim Bau als visuelle Vorlage heranziehen (HOCH):
- **Mahnwesen:** `Mahnungen und Zahlungserinnerungen …` image1–16 — Mahntabelle
  mit Tabs/Spalten, Mahnstufen-Einstellungsfenster, Mahnlauf-Übersicht.
- **Storno/Korrektur:** `Prozess für das Stornieren und Korrigieren …` image1–9 —
  Stornorechnung-Editor mit Referenzdokument, Entscheidungstabelle.
- **Belege:** `Wie kann ich Eingangsrechnungen und Belege erfassen` image1–4 —
  Belegübersicht mit Filtern, Belegerfassungsformular mit Reitern.
- **Überblick/Export:** `Buchhaltung mit HERO Software` image1–16 —
  Beleg-Erfassungsfenster, DATEV-Export-Dialog, Konfigurator-Buchungsrelevanz.
- **Teilzahlung:** `Wie kann ich eine Teilzahlung … erfassen` image1 —
  Geldschein-Dialog.
- Ergänzend (MITTEL): Lexware-Konfigurationsfenster (Startdatum/Status/
  Kontaktimport) und die Info-Icon-Zustände (blau/rot) für Übertragungsfehler.
