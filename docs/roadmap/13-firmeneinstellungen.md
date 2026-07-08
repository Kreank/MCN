# 13 — Firmeneinstellungen (Hero: Firmeneinstellungen)

## Zweck & Hero-Entsprechung

Diese Sektion ist der zentrale Admin-Bereich für Firmen-Stammdaten und
Konfiguration: Firmenprofil (Identität, Kontakt, Bank, Gewerke, Logo, Bundesland,
Standardsprache), E-Mail-Templates & Informationsdokumente (Content-Vorlagen),
die **Zugriffsrechte-Matrix** (Rollen × Rechte), Seitendarstellung/Branding,
Niederlassungen und Quellen. Sie entspricht 1:1 Hero's Bereich
**[Firmeneinstellungen]** (in Hero nur für die Rolle „Geschäftsführer" sichtbar).
Die Zugriffsrechte-Matrix ist das **Herzstück dieser Sektion und Voraussetzung
für praktisch alle Schreib-UIs im gesamten Leitstand** — sie ist im DB-Schema
`security` bereits angelegt (`role`, `role_permission`, `user_role`), muss aber
noch ein Pflege-UI und die App-seitige Durchsetzung bekommen. Für globale
Querschnitts-Prinzipien (KI-first, No-Delete/Audit, Auth, Design/A11y) siehe
`00-informationsarchitektur.md`, Abschnitt „Querschnitts-Prinzipien".

**Abgedeckte Hero-Quelldateien:**
- `Firmeneinstellungen\Wie lassen sich die Daten meiner Firma hinterlegen bzw. anpassen\…docx` (Firmendaten Allgemein/Kontakt/Bank)
- `Firmeneinstellungen\Wie passe ich die Feiertage für mein Bundesland an\…docx`
- `Firmeneinstellungen\Ist es möglich noch weitere Gewerke hinzuzufügen Wie bearbeite ich meine Gewerke\…docx`
- `Firmeneinstellungen\Bezeichnung Geschäftsführer abändern\…docx`
- `Firmeneinstellungen\Eigenes Firmenlogo verwenden\…docx`
- `Firmeneinstellungen\Wie bekomme ich das große HERO-Logo in meinen E-Mails weg\…docx`
- `Firmeneinstellungen\Wie kann ich eigene Email-Vorlagen (Templates) erstellen\…docx`
- `Firmeneinstellungen\Ordner erstellen und anzeigen lassen\…docx` (**Duplikat/Fehlbenennung**, s. u.)
- `Firmeneinstellungen\Sprachauswahl\…docx`
- `Firmeneinstellungen\In welchen Sprachen ist HERO verfügbar\…docx`
- `Firmeneinstellungen\Wie lege ich Zugriffsrechte für meine Mitarbeiter fest\…docx`
- `Firmeneinstellungen\Wie kann ich die Benutzerrechte meiner Mitarbeiter einstellen\…docx` (Querverweis → `12`)
- `Firmeneinstellungen\Wie können meine Monteure Baustellenberichte einsehen\…docx` (Rechte-Beispiel)
- `Firmeneinstellungen\Wie verwalte ich meine Niederlassungen\…docx`
- `Firmeneinstellungen\Warum sehe ich nicht alle Projekte\…docx` (**Fehlzuordnung** → gehört zu `04`)
- `Firmeneinstellungen\Was sind Informationsdokumente\…docx`
- `Firmeneinstellungen\Kann ich meine Quellen ändern\…docx`
- `Firmeneinstellungen\DSGVO - Datenauskunft für Kunden\…docx` (**Fehlzuordnung** → Aktion in `02`)
- `Firmeneinstellungen\Wo finde ich die den Auftragsverarbeitungsvertrag\…docx` (globaler Footer-Link)

## Ziel-Navigation & Routen

Sidebar-Hauptpunkt **„Einstellungen"** (nur sichtbar für Rollen mit
`security`-Schreibrecht bzw. `ADMINISTRATION`/`GESCHAEFTSFUEHRUNG`; Hero:
„Geschäftsführer"-Sichtbarkeit). Unter-Navigation spiegelt Hero:

| Route | Screen | Hero-Unterpunkt |
|---|---|---|
| `/einstellungen/profil` | Firmenprofil (Tabs: `allgemein`, `kontaktdaten`, `bankinformationen`, `gewerke`) | Firmenprofil |
| `/einstellungen/email-templates` | E-Mail-Templates (Liste + `neu`/`:id/bearbeiten` + Modal `einstellungen`) | Email-Templates |
| `/einstellungen/informationsdokumente` | Informationsdokumente (Liste + Upload + Versand-Dialog) | Informationsdokumente |
| `/einstellungen/quellen` | Quellen (Liste + CRUD) | Quellen |
| `/einstellungen/zugriffsrechte` | Zugriffsrechte-Matrix (Rollen × Rechte) | Zugriffsrechte |
| `/einstellungen/niederlassungen` | Niederlassungen (Liste + CRUD) | Niederlassungen |
| `/einstellungen/branding` | Seitendarstellung/Branding (Logo, Design-Tokens) | (in Hero verteilt: Logo im Profil, Mail-Header) |

**Nicht** unter Einstellungen (Querverweise, s. Abschnitt „Abhängigkeiten"):
Mailserver-Konfiguration (per-User → `14`), Rollenzuweisung je Mitarbeiter
(→ `12`), Projekte-Filter (→ `04`), DSGVO-Datenauskunft (Aktion im Kontakt →
`02`), Dokumentlayout/Logo-Platzierung/Nummernkreise/Projekttypen (→ `05`/`04`).
AVV/Datenschutz als **globaler Footer-Link** (nicht in dieser Nav).

## Screens & Komponenten

### Zugriffsrechte-Matrix (zentral)
- **UI-Typ & Aufbau:** Tabellen-/Matrix-Editor. Zeilen = Kombination aus
  **Modul** (`identity, property, management, tenure, billing, workflow,
  invoicing, pricing, content, security, ai`) und **Aktion** (`LESEN, ANLEGEN,
  AENDERN, FREIGEBEN, VERSENDEN, STORNIEREN, EXPORTIEREN, LOESCHEN`); Spalten =
  die **festen Rollen** aus `security.role` (`ADMINISTRATION, GESCHAEFTSFUEHRUNG,
  DISPOSITION, TECHNISCHE_LEITUNG, BUCHHALTUNG, MONTEUR, NUR_LESEN`). Zellwert =
  `allowed` (Ja/Nein-Toggle) plus **Zeilen-Scope** `row_scope` (`ALLE`/`EIGENE`,
  z. B. „Monteur nur eigene Einsätze"). Primäre Aktion [Speichern]; die Matrix
  ist ein **von der GF abzunehmendes Stammdatendokument** (B-36).
- **Wichtig ggü. Hero:** Rollen sind **fest** (keine eigenen Rollen anlegbar,
  wie Hero), aber der **Rollensatz weicht von Hero ab** (Hero:
  Geschäftsführer/Niederlassungsleiter/Buchhaltung/Vertriebler/Monteur). Mapping
  Hero→MCN ist eine Produktentscheidung (s. Offene Punkte). Hero's Rechte sind
  feingranular je Feature/Dokumenttyp; MCN modelliert sie als Modul×Aktion —
  Hero's „Baustellenberichte sichtbar für Monteur" wird zu
  `content`/`workflow` × `LESEN` für Rolle `MONTEUR` (feinere Dokumenttyp-Gates
  ggf. app-seitig).
- **Zustände:** Read-only für Rollen ohne `security`-Schreibrecht; Laden/Leer
  entfällt (Matrix ist vorbefüllt). Statuswechsel-Steuer + Begründung bei
  sicherheitsrelevanten Änderungen. Vier-Augen-Hinweis, wo `four_eyes_action`
  greift (z. B. BANKDATEN, MASSENEXPORT).
- **Wiederverwendet:** Statuswechsel-Steuer, Logbuch/Audit-Feed. **Neu:**
  Matrix-Grid-Editor (spezifisch, nicht die generische Ressourcen-Liste).

### Firmenprofil (Tabs)
- **UI-Typ & Aufbau:** Detail-„Mappe" mit Tabs. `allgemein` (Firmenname,
  Rechtsform, Adresse, **Bundesland** → steuert Feiertage in Planung/`06`,
  Standard-Anzeigesprache, **Geschäftsführer-Bezeichnung** (Freitext, fließt in
  Fußzeilen/Signaturen), **Logo-Upload**), `kontaktdaten` (Telefonnummern),
  `bankinformationen` (IBAN, BIC, Steuernummer, Handelsregisternummer, USt-IdNr.),
  `gewerke` (Multi-Select aus Katalog; Rest über Support in Hero — MCN prüft
  Self-Service, s. Offene Punkte). Read-Ansicht + [Bearbeiten]-Aktion je Tab.
- **Zustände:** Bankdaten-Änderung ist Vier-Augen-pflichtig
  (`four_eyes_action.BANKDATEN`) — Freigabe-Flow statt direktem Speichern.
- **Wiederverwendet:** Detail-Mappe, Anlege-/Bearbeiten-Dialog. **Neu:**
  Logo-Upload-Feld (MinIO), Gewerke-Multi-Select.

### E-Mail-Templates (Liste + Editor + Einstellungen-Modal)
- **UI-Typ & Aufbau:** Ressourcen-Liste + Editor-Formular. Editor-Felder:
  [Kontext] (Verwendungszweck), [Name], [Betreff], [Inhalt] (Freitext-Editor).
  Numerische Sortierung (keine manuelle). Separates Modal [Einstellungen] mit
  Header-Stil-Umschalter **Standard/Minimal** (steuert Logo-Anzeige im Mailkopf).
- **Wiederverwendet:** Ressourcen-Liste, Bearbeiten-Dialog.

### Informationsdokumente (Liste + Upload + Versand)
- **UI-Typ & Aufbau:** Ressourcen-Liste hochgeladener Dokumente (AGB,
  Werbematerial). Aktionen: [+ Informationsdokument] (Upload → MinIO/`content.file`),
  Archivieren (statt Hero's Mülleimer), „Versenden" (Papierumschlag → Dialog mit
  Empfängerauswahl + Nachricht → verschickt als `content.communication` Typ EMAIL,
  Dokument als Anhang).
- **Wiederverwendet:** Ressourcen-Liste, Versand-Dialog (mit E-Mail-Tool geteilt).

### Quellen (Liste + CRUD)
- **UI-Typ & Aufbau:** Ressourcen-Liste kleiner Lookup-Werte (Akquisekanäle:
  Empfehlung, Webseite, …). [+ Quelle], Bearbeiten, Deaktivieren. Wichtig: alte
  Vorgänge behalten die (deaktivierte) Quelle historisch — deckt sich mit
  No-Delete.
- **Wiederverwendet:** Ressourcen-Liste, Bearbeiten-Dialog.

### Niederlassungen (Liste + CRUD)
- **UI-Typ & Aufbau:** Ressourcen-Liste mit Suche. Spalten: Name, Anschrift,
  Radius, zugeteilte Mitarbeiter, Erstellungsdatum. [+ Niederlassung],
  Bearbeiten, Deaktivieren (statt Löschen).
- **Wiederverwendet:** Ressourcen-Liste, Bearbeiten-Dialog.

### Branding / Seitendarstellung
- **UI-Typ & Aufbau:** Einstellungsseite für Logo + Marken-/Design-Tokens.
  Verweist auf die zentralen Brandtokens (Navy/Orange/Salbei/Amber, Light+Dark)
  aus `00`/CLAUDE.md — hier nur firmenspezifische Overrides (Logo, ggf.
  Akzentfarbe). **Kein** freies Theming, das WCAG bricht.

## API-Endpunkte (django-ninja)

| Methode | Pfad | Zweck | Auth | Service-Funktion |
|---|---|---|---|---|
| GET | `/api/security/roles` | Rollen (Spalten der Matrix) | offen (Dev) | `list_roles` |
| GET | `/api/security/permissions` | Rechtematrix (Modul×Aktion×Rolle) | offen | `get_permission_matrix` |
| PUT | `/api/security/permissions` | Matrix-Zelle(n) setzen (`allowed`, `row_scope`) | Session | `set_permission` (business_transaction) |
| GET | `/api/settings/company-profile` | Firmenprofil lesen | offen | `get_company_profile` |
| PUT | `/api/settings/company-profile` | Profil ändern (Bankdaten → Vier-Augen) | Session | `update_company_profile` (business_transaction) |
| POST | `/api/settings/company-profile/logo` | Logo-Upload (MinIO) | Session | `upload_company_logo` (business_transaction) |
| GET/POST/PUT | `/api/settings/email-templates[/:id]` | Templates lesen/anlegen/ändern | GET offen, Schreiben Session | `*_email_template` (business_transaction) |
| GET/POST | `/api/settings/info-documents` | Informationsdokumente lesen/hochladen | GET offen, Schreiben Session | `*_info_document` (business_transaction) |
| POST | `/api/settings/info-documents/:id/send` | per E-Mail versenden | Session | `send_info_document` (business_transaction) |
| GET/POST/PUT | `/api/settings/sources[/:id]` | Quellen CRUD | GET offen, Schreiben Session | `*_source` (business_transaction) |
| GET/POST/PUT | `/api/settings/branches[/:id]` | Niederlassungen CRUD | GET offen, Schreiben Session | `*_branch` (business_transaction) |

Alle schreibenden Endpunkte laufen zwingend über `db_core.db_context.business_transaction`.

## DB-Bezug

- **Vorhanden (`security`):** `security.role` (feste Rollen, No-Delete),
  `security.role_permission` (Matrix: `role_code`, `module`, `action`, `allowed`,
  `row_scope`, UNIQUE(role_code,module,action); UPDATE auditiert), `security.user_role`
  (zeitabhängige Zuordnung, No-Delete/beenden), `security.four_eyes_action`
  (BANKDATEN, MASSENEXPORT, DUBLETTEN_MERGE, …). Migration `0026_rechte_stammdaten.sql`.
  Ehrlichkeitshinweis aus 0026: **Durchsetzung der Matrix erfolgt in der
  App-Schicht** (die App verbindet als technischer DB-User); echte DB-Rollentrennung
  folgt mit Betriebskonzept (C-11). Die Service-Schicht MUSS die Matrix daher
  aktiv prüfen.
- **Content-Vorlagen:** `content.document_template` (`0041`) für Dokumentvorlagen
  (Querverweis `05`); `content.file`/`content.communication` (Typ EMAIL) für
  Informationsdokumente + Versand. **Eine dedizierte E-Mail-Template-Tabelle
  existiert noch nicht** — als Hand-SQL-Migration neu anzulegen (`content`-Schema,
  Schutzstandard erben).
- **Noch nicht vorhanden (neu per Hand-SQL, `managed=False`):** Firmenprofil/
  `company_profile` (inkl. Logo-Referenz, Bundesland, Standardsprache,
  GF-Bezeichnung, Bankdaten), `security.branch` (Niederlassung), Quellen-Lookup,
  Gewerke-Katalog + Firma↔Gewerk-Zuordnung. Schema-Zuordnung von Gewerken/Quellen
  ist offen (s. Offene Punkte). Jede neue Tabelle erbt No-Delete/Audit/No-Truncate.
- **Querverweis-Objekte (fremde Sektionen):** `workflow.number_range`
  (Nummernkreise, `0010`), `workflow.project_category` + Pipeline-Editor (`0042`,
  `0043`, Projekttypen), Dokumentlayout in `content.document_template`/Konfigurator
  (`05`).

## KI-Andockpunkte (`ai.ai_proposal`)

- **Rechtematrix:** KI schlägt Matrix-Anpassungen vor (z. B. „Rolle X fehlt
  `LESEN` auf `content` für Baustellenberichte") — geht durch denselben
  Freigabe-/Vier-Augen-Flow wie ein Mensch; GF nimmt ab.
- **E-Mail-Templates:** KI entwirft Vorlagentexte (Betreff/Inhalt je Kontext) als
  Vorschlag zur Freigabe.
- **Stammdatenpflege:** KI schlägt fehlende/duplizierte Quellen oder Gewerke zur
  Bereinigung vor. Alle Vorschläge nur über die Service-Tore, kein Direktwrite.

## No-Delete/Audit/GoBD-Übersetzung

Hero bietet an mehreren Stellen hartes „Löschen" (Mülleimer) an — bei uns:
- **Niederlassungen, Quellen, E-Mail-Templates, Informationsdokumente:**
  „Löschen" → **Deaktivieren/Archivieren** (Status `INAKTIV`/`ARCHIVIERT`). Bei
  Quellen ausdrücklich: bestehende Vorgänge behalten die deaktivierte Quelle
  (kein kaskadierendes Löschen — deckt sich mit Hero's dokumentiertem Verhalten).
- **Rollen:** nicht löschbar (`trg_role_no_delete`); Änderung nur der
  Rechte-Zellen, auditiert.
- **Rollenzuordnungen (`user_role`):** werden **beendet** (`valid_until`), nie
  gelöscht.
- **Firmenprofil/Bankdaten:** Änderung ist auditiert; Bankdaten-Änderung
  Vier-Augen-pflichtig statt sofortigem Überschreiben.

## Offene Punkte / Entscheidungen

- **Duplikat/Fehlbenennung:** `Ordner erstellen und anzeigen lassen.docx` ist
  textgleich mit der E-Mail-Template-Anleitung — **kein eigenes Feature „Ordner
  erstellen"**; nicht ungeprüft übernehmen (Hero-Exportfehler).
- **Fehlzuordnungen im Quellordner:** `Warum sehe ich nicht alle Projekte` (→ `04`,
  Projekte-Filter Alle/Meine/Mitarbeiter) und `DSGVO - Datenauskunft für Kunden`
  (→ `02`, Aktion im Kontakt-Detail) lagen zwar unter „Firmeneinstellungen",
  gehören funktional aber nicht hierher; hier nur querverwiesen.
- **Rollen-Mapping Hero→MCN:** Hero-Rollen (Geschäftsführer, Niederlassungsleiter,
  Buchhaltung, Vertriebler, Monteur) ≠ MCN-Rollen (ADMINISTRATION,
  GESCHAEFTSFUEHRUNG, DISPOSITION, TECHNISCHE_LEITUNG, BUCHHALTUNG, MONTEUR,
  NUR_LESEN). Explizites Mapping für Wiedererkennung entscheiden (Produkt).
- **Gewerke-Self-Service:** Hero erlaubt keine neuen Gewerke (nur Support). Für
  MCN entscheiden: Admin-Self-Service vs. kuratierter Katalog. Schema-Ort offen.
- **Quellen/Gewerke Schema-Ort:** eigenständige Lookups vs. gemeinsames
  Referenzschema — aus Hero-Texten nicht ableitbar.
- **Mailserver:** In Hero liegt Mailserver-/OAuth-Konfiguration bei „Persönliche
  Daten" (per-User, → `14`), **nicht** in Firmeneinstellungen; eine
  firmenweite Mailserver-Konfiguration ist in der Spec **nicht belegt**. Ob MCN
  einen firmenweiten Absender/Relay braucht, ist offen (Produkt/Betrieb).
- **Bundesland/Feiertage:** Feld im Profil, Wirkung in Planung (`06`) — Kopplung
  dort verankern.
- **Standardsprache/i18n-Umfang:** Zielsprachen-Menge ist MCN-Produktentscheidung
  (Hero-Liste nicht übernehmen). Standard-Anzeigesprache-Feld im Profil belassen.
- **Feldvalidierung/Pflichtfelder** (IBAN/BIC-Format, USt-IdNr., Pflicht bei
  Niederlassung/Quelle): in Hero nicht dokumentiert, zu entscheiden.

## Abhängigkeiten

- **Auth + `security`-Durchsetzung:** Diese Sektion baut die Rechtematrix-UI und
  ist zugleich deren größter Nutznießer — die Matrix ist **Voraussetzung für die
  Schreib-Sichtbarkeit fast aller anderen Sektionen**. Priorität entsprechend hoch.
- **Shared components** (`00`): Ressourcen-Liste, Detail-Mappe, Bearbeiten-Dialog,
  Statuswechsel-Steuer, Logbuch/Audit-Feed, Versand-Dialog (mit E-Mail-Tool).
- **DB-Vorarbeit:** Neue Hand-SQL-Migrationen für company_profile, branch, source,
  gewerk, email_template (Schutzstandard erben, State-only-Migration einchecken).
- **MinIO** für Logo-/Dokument-Upload.
- **Quer:** `12` (Rollenzuweisung je Mitarbeiter), `14` (Mailserver/Signatur),
  `05` (Dokumentlayout/Logo-Platzierung, Nummernkreise), `04` (Projekttypen,
  Projekte-Filter), `02` (DSGVO-Datenauskunft).

## Aufwand & Priorität

| Screen | Größe | Phase (s. `00`) | Reihenfolge |
|---|---|---|---|
| Zugriffsrechte-Matrix | L | Phase 0 (Fundament, vorgezogen) | **1 — zuerst** |
| Firmenprofil (Tabs + Logo) | M | Phase 4 | 2 |
| Niederlassungen | S | Phase 4 | 3 |
| Quellen | S | Phase 4 | 4 |
| E-Mail-Templates (+ Einstellungen) | M | Phase 4 | 5 |
| Informationsdokumente (Upload+Versand) | M | Phase 4 | 6 |
| Branding/Seitendarstellung | S | Phase 4 | 7 |

Empfehlung: **Zugriffsrechte-Matrix vorziehen** (Phase 0), da sie Schreib-UIs
überall freischaltet und die DB-Basis (`security`) bereits steht. Der Rest bleibt
im Verwaltungs-Cluster (Phase 4).

## Screenshots zur Vorlage (Wiedererkennung)

- **Zugriffsrechte-Matrix:** `Wie lege ich Zugriffsrechte für meine Mitarbeiter
  fest\image1.png` (HOCH — prägt das Matrix-Grid).
- **Firmenprofil:** `Wie lassen sich die Daten meiner Firma hinterlegen…\image1–4.png`
  (HOCH — Reiterstruktur Allgemein/Kontakt/Bank).
- **Logo/Dokumentenkonfigurator:** `Eigenes Firmenlogo verwenden\image1–6.png`
  (HOCH — „Gestaltung erste Seite"; primär Vorlage für `05`, hier für Logo-Upload).
- **Niederlassungen:** `Wie verwalte ich meine Niederlassungen\image1–4.png`
  (MITTEL-HOCH — Listen-/Tabellen-Layout).
- **E-Mail-Templates:** `Wie kann ich eigene Email-Vorlagen…\image1–2.png` und
  `Wie bekomme ich das große HERO-Logo…\image1–5.png` (MITTEL — Editor + Standard/Minimal-Modal).
