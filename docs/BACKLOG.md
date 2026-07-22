# Backlog & Gotchas — MCN

> Priorisierte nächste Bereiche. Stand der Priorisierung: 2026-07.

---

## 8. Nächste Bereiche (priorisierter Backlog) + Gotchas

Details je Sektion in `docs/roadmap/01..14`. DB-Befunde in
`docs/roadmap/README.md`.

**Erledigt** (frühere Session): ✔ Auswertungen (Landing + Umsatz-/Projektübersicht
— weitere Dashboards offen), ✔ Aufträge (`workflow.work_order`), ✔ Beleg-
Veröffentlichung (invoice→VEROEFFENTLICHT / quote→VERSENDET, ohne PDF).

**Erledigt** (diese Session):
- ✔ **Einsätze/Planung** (`workflow.service_job`): Models/Service/API + Liste +
  Einsatz-Mappe (read-only). Write-Service `services/einsatz.py` (create/
  set_schedule/advance_status/assign_user/log_time/log_material) getestet, noch
  nicht im UI (kommt mit Auth). **Offen:** Plantafel (Schwimmbahnen + Drag&Drop,
  XL), Kalender, Terminkategorie-/Ressourcen-Schema (fehlen in der DB →
  Migration nötig, siehe `docs/roadmap/06-planung.md`).
- ✔ **Buchhaltung** (`invoicing.payment/dunning_level/dunning_notice`, 0025):
  Models/Service/API + Offene-Posten-Liste + Detail-Mappe (Übersicht/Zahlungen/
  Mahnverlauf), read-only. Write-Service `services/buchhaltung.py` (record_payment/
  reverse_payment/issue_dunning_notice) getestet. Zahlungsstatus/offener Betrag
  sind **abgeleitet** (nicht gespeichert; Vorzeichenkonvention `PAYMENT_SIGN`).
  **Offen:** Storno/Rechnungskorrektur-Flow (invoice_type STORNO/GUTSCHRIFT +
  reference_invoice_id), Belegerfassung (Eingangsrechnungen, neues/erweitertes
  Schema), Stammdaten-CRUD (ledger_account/cost_center fehlen), Mahnwesen-Screen
  (Endpoint `/api/buchhaltung/dunning` existiert + getestet, aber noch kein UI),
  Mahnstufen-Ausbau 3→6, DATEV/Lexware-Export. Details `docs/roadmap/09-buchhaltung.md`.
  Mahnverlauf-Pausieren fehlt im DB-Schema.
- ✔ **Wartung** (`maintenance.*` — **erstes selbst angelegtes Fachschema**,
  Hand-SQL-Migration 0016): `maintenance_contract` (objektzentriert, Statusautomat
  AKTIV↔INAKTIV→ARCHIVIERT per eigenem Trigger, Belegkreis 'W') + append-only
  `maintenance_event`. Write-Service `services/wartung.py` (create/set_status/
  trigger_action; Aktion AUFGABE erzeugt eine workflow.task). Liste + Detail-Mappe
  read-only. Muster für neue Fachtabellen:
  `migrations/0016_maintenance_wartung.py` (RunSQL + Schutzstandard).
  **Die damals notierten Lücken sind inzwischen geschlossen:** Scheduler
  (`wartung_faellige_ausloesen`), Aktionen PROJEKT/AUFTRAG und die Anlege-/Auslöse-UI
  stehen; die **Fälligkeiten-Engine** (0071/0074) hat den Scheduler um Prüffristen und
  Gewährleistung erweitert (siehe Welle 3).
- ✔ **„Kein-neues-Schema"-Ausbau** (auf vorhandenem Fachschema, User-Wunsch
  „erst das, dann Schema+Login") — **komplett abgearbeitet**:
  - **Mahnwesen-Screen** (UI zu `/buchhaltung/dunning`).
  - **Plantafel + Kalender** (read-only Board/Monatsansicht auf `service_job`,
    Endpoint `/planung/plantafel`, Subnav).
  - **Storno/Rechnungskorrektur** (STORNO/GUTSCHRIFT-Folgebelege, `beleg.py`
    create_cancellation/create_correction, `POST /buchhaltung/invoices/{id}/cancel`|
    `/correction`; Detail zeigt Ursprung/Folgebelege). **Invariante:** create_invoice
    lehnt Credit-Typen ab — Folgebelege nur über die dedizierten Funktionen (immer
    negativ). Umsatz-Aggregation entsprechend gefixt (Summe über alle Belege).
  - **Kunden-Dashboard** (`/auswertungen/kunden`, Umsatz je primärem Schuldner).
  - **VK-Kalkulation** (`/pricing/articles/{id}/kalkulation`, Formel Basis
    EK/Listenpreis × Auf-/Abschlag; Models SalePriceGroup/ArticleSalePrice/
    ArticleSupplierReference; Artikel-Detail-Tab).
  - **Beleg-PDF** (`GET /invoicing/invoices/{id}/pdf`, on-the-fly via **fpdf2**,
    nur veröffentlicht; Link auf Rechnung-Detail). Persistente MinIO-Archivierung
    (content.document + file_link) noch offen.
  **Noch offen (kleinere Reste):** weitere Dashboards (Projekte/Artikel/Mitarbeitende),
  DATANORM-Import-Wizard (Schreib-Flow, mit Auth), Beleg-PDF-Archivierung (MinIO).
  Danach: **Schema-Bereiche** (Belegerfassung, Ressourcen/Terminkategorien,
  Firmeneinstellungen) + **Auth/Login** + alle Anlege-Formulare.
- ✔ **Mitarbeiter/HR** (`hr.*` — **zweites selbst angelegtes Fachschema**, Hand-SQL
  0019). Grundsatzentscheidung: eigenes Schema statt `security` erweitern —
  `security` beantwortet „darf dieser Account etwas?", `hr` „welche
  arbeitsrechtliche Beziehung besteht?". Personendaten werden **nicht** dupliziert:
  `hr.employee` ankert per FK auf `security.app_user` (Login) und
  `identity.person` (Stammdaten), beide 1:1.
  - `hr.employee` — Personalnummer `MA-00001` aus **eigener Sequenz** (kein
    GoBD-Belegkreis!), Statusautomat AKTIV↔INAKTIV→AUSGETRETEN (final;
    Wiedereintritt = neuer Personalsatz).
  - `hr.employment_contract` — versioniert, **überlappungsfrei** je Mitarbeiter
    (EXCLUDE über `daterange`). Beginn, Sollstunden-Raster (Mo–So),
    Urlaubsanspruch und Lohngruppe sind nach dem INSERT **physisch
    unveränderlich** (Trigger) — Arbeitszeitänderung = Folgevertrag, der den
    laufenden automatisch am Vortag beendet.
  - `hr.absence` — Statusautomat ENTWURF→EINGEREICHT→GENEHMIGT|ABGELEHNT
    (+ZURUECKGEZOGEN); Ablehnung begründungspflichtig (CHECK). Überlappungsfrei
    für ENTWURF/EINGEREICHT/GENEHMIGT.
  - `hr.vacation_budget` — Anspruch/Übertrag/Anpassung je Jahr. **Verbrauch ist
    nicht gespeichert**, sondern aus genehmigten URLAUB-Abwesenheiten abgeleitet
    (gleiche Konvention wie der offene Betrag in der Buchhaltung).
  - **Kernregel:** `days_count` einer Abwesenheit berechnet der Service aus dem
    Sollstunden-Raster des am jeweiligen Tag gültigen Vertrags — Wochenenden und
    0-Stunden-Tage zählen nicht, halbe Randtage ziehen 0,5 ab. Der Client liefert
    `days_count` nie selbst.
  - **Bewusste Lücken:** die Urlaubstage-Zählung kennt **keinen Feiertag** (Feiertage
    zählen als Arbeitstage, wenn der Wochentag ein Soll hat — `hr.holiday` gibt es seit
    0068, aber nur für Zeiterfassung/Plantafel, siehe Invariante); jahresübergreifende
    Urlaube werden komplett dem Startjahr zugerechnet; unterjähriger Eintritt kürzt den
    Anspruch nicht automatisch (dafür ist die begründungspflichtige Anpassung da) —
    Hero verhält sich genauso.
  - **Zeitwirtschaft ist inzwischen gebaut** (0066–0068): `hr.time_category`,
    `hr.break_rule`, `hr.holiday`, `workflow.work_day` — siehe „Welle 2" oben.
    **Weiterhin ausgeklammert:** Steuer-/Bankdaten (DSGVO Art. 9/32;
    `security.four_eyes_action` kennt bereits 'BANKDATEN', app-seitig nicht
    durchgesetzt), Niederlassung. **Stundenausgleich, Resturlaubs-Übertrag und
    Attest-Upload sind gebaut** (0072/0075, siehe Welle 3) — damit ist der HR-Block
    bis auf Steuer/Bank vollständig.
  - **DSGVO-Merkposten:** `GET /api/hr/absences` ist der **einzige** Lese-Endpunkt
    mit `auth=django_auth` (Krankheitsdaten über den ganzen Bestand).
    `GET /api/hr/employees/{id}` liefert ebenfalls Krankheitshistorie und ist
    noch offen — beim Auth-Slice zuerst absichern.

Empfohlene nächste Reihenfolge:

1. **Auswertungen ausbauen**: die übrigen Dashboards (Projekte/Kunden/Artikel/
   Mitarbeitende/Umsätze-Details/Projektkarte). **Marge** braucht die EK-Ebene
   (`pricing.article_supplier_reference.last_purchase_price`, noch kein Model)
   und ist aus Belegzeilen NICHT ableitbar — ggf. über den `billing_snapshot`.
   Startseite `01` kann jetzt die Umsatz-Kennzahlen aus `/auswertungen` ziehen.
2. **Beleg-PDF (optional)**: PDF-Ausfertigung + `content.file_link`
   (`link_category='BELEG_PDF'`, Einmaligkeits-Index 0032) — reine Ausgabe,
   nicht Voraussetzung der Veröffentlichung.
4. **VK-Kalkulation/DATANORM**: Verkaufspreis ist eine Formel über
   `sale_price_group`/`article_sale_price` (nicht ein Feld). DATANORM-Import-Wizard.
6. **Buchhaltung**: Zahlungen (0025), **Mahnwesen** (`dunning_level` seedet nur
   3 Stufen, Hero braucht 6 → ausbauen), DATEV/Lexware-Export. Baut auf Rechnungen.
7. **Wartung**: kein Schema vorhanden → neues `maintenance.*` (Hand-SQL) nötig
   (siehe `docs/roadmap/11`).
8. **Einstellungen · Profil**: `security.role/role_permission` (0026) existiert
   (Rechtematrix, app-seitig durchzusetzen). HR-Kern ist mit `hr.*` (0019)
   erledigt; offen bleiben Steuer-/Bankdaten, Zeitwirtschaft und Niederlassung.
9. **Auth/Login + alle Anlege-Formulare** — ganz zum Schluss (siehe Entscheidung).

- ✔ **Datei-Ablage im UI** (`shared/dateien`, `core/datei.service.ts`): Upload per
  Klick und Drag&Drop (tastaturbedienbar), Fortschritt, Download über Blob
  (nicht `window.open` — Auth-Cookie/CSRF), Verknüpfung lösen hinter Bestätigung
  (die Datei selbst bleibt). Verdrahtet in **neun Mappen**: Projekt, Kontakt,
  Liegenschaft, Angebot, Rechnung, Offener Posten, Vorgang, Auftrag, Einsatz.
  **Offen:** `unit_id`/`asset_id` — beide haben (noch) keine eigene Detail-Mappe.
- ✔ **Artikel bearbeiten / Historie / Stamm-Übernahme** im UI: Reiter
  Informationen · Kalkulation · Historie (der alte `preis`-Tab war reine
  Dopplung und ist in Kalkulation aufgegangen). Bearbeiten-Dialog, GTIN mit
  Prüfziffer (Client spiegelt `artikel.py::_gtin_gueltig` — beide müssen
  synchron bleiben), Statuswechsel (Deaktivieren hinter Bestätigung), und das
  Häkchen im Angebotseditor (siehe Invariante oben).

Kleinere offene Enden: Objekt-Bilder; ISO-Datums-Formatierung im UI (aktuell teils
roh). **Das Stil-Budget ist wieder eingehalten** — das Frontend baut seit `f1ed9d9`
erstmals **ohne Budget-Warnung** (8/10 kB). Wer ein Stylesheet über die Grenze
treibt: **auslagern, nicht das Budget lockern.**

