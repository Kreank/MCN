# Kunden-Terminbuchung per Link — Implementierungsplan

> **Status: ENTWURF, nicht umgesetzt.** Stand 2026-07-31. Dieses Dokument ist ein
> Bauplan zum späteren Abarbeiten, kein Stand. Die offenen Fragen am Ende sind
> bewusst offen — sie werden vor dem ersten Commit entschieden, nicht währenddessen.

## 1. Worum es geht

Beim bisherigen CRM (HERO) existiert ein selbstgebautes Tool: Der Disponent gibt
Zeitfenster frei, der Kunde bekommt einen Link und sucht sich darin einen Termin.
Das Tool spricht dafür von außen die Kalender-Endpunkte von HERO an und pflegt die
Freigabefenster selbst.

Hier sitzen wir am Quelltext. Damit entfällt der ganze Umweg: Die Verfügbarkeit
muss nicht gepflegt werden, sie **fällt aus dem heraus, was ohnehin schon in der
Datenbank steht**. Der Disponent gibt nur noch den Rahmen vor.

## 2. Bereits getroffene Entscheidungen (2026-07-31)

| Frage | Entscheidung |
|---|---|
| Spielart | **Persönlicher Link zum Auftrag.** Der Token hängt an einem konkreten Auftrag; gebucht wird auf den dort schon vorhandenen ungeplanten Einsatz. Kein öffentliches Formular, keine Kundenanlage von außen. |
| Was der Slot bindet | **Ein konkreter Mitarbeiter.** Der Slot ist an einen bestimmten Monteur gebunden, nicht an „irgendwen aus dem Gewerk". |

⚠️ **Zur zweiten Entscheidung gehört ein Preis, der vor dem Bau bewusst
angenommen werden muss** (siehe auch Frage F2 unten):

* Die Verfügbarkeit **eines** Monteurs ist dünn. Fällt er aus, ist der gebuchte
  Termin nicht einfach umbesetzbar — der Kunde hat *diesen* Menschen gebucht.
* Sobald dem Kunden der **Name** angezeigt wird, gibt der Link Personalstruktur
  nach außen. Aus dem Slot-Raster eines benannten Monteurs lässt sich seine
  Arbeitszeit, seine Auslastung und (über die Lücken) seine Abwesenheit ableiten.
  Das ist kein theoretisches Risiko: derselbe Fehler wurde intern schon einmal
  gefunden und behoben (`BoardAbsenceOut` liefert bewusst **keine** Abwesenheitsart,
  weil die nach DSGVO Art. 9 eine besondere Kategorie ist).
* **Ausweg, der beides erfüllt:** der Slot bindet intern hart den Mitarbeiter,
  die öffentliche Antwort nennt ihn aber **nicht** (oder nur mit Vornamen, wenn
  der Betrieb das will). Der Kunde sieht Uhrzeiten, das System weiß, wer dahinter
  steht. Das ist der Vorschlag dieses Plans, solange F2 nicht anders entschieden wird.

## 3. Was schon da ist (nichts davon muss neu gebaut werden)

| Baustein | Ort |
|---|---|
| Belegte Zeiten, Board-Daten, Doppelbelegungs-Prüfung | `db_core/services/planung.py` (`board_daten`, `belegungs_warnungen`) |
| Sollstunden je Wochentag | `hr.employment_contract.hours_monday…hours_sunday` |
| Genehmigte Abwesenheiten | `hr.absence` (nur Status `GENEHMIGT` zählt) |
| Feiertage | `hr.holiday` (`region` NULL = bundesweit) |
| Gewerk + Zuordnung Mitarbeiter→Gewerk | `company.trade`, `EmployeeTrade` |
| Übliche Dauer je Termintyp | `workflow.appointment_category.default_duration_minutes` |
| Termin anlegen/umplanen/zuweisen in EINER Transaktion | `planung_service.create_termin` / `update_termin`, `einsatz_service.set_schedule` |
| Betriebszeitzone (Europe/Berlin ≠ `settings.TIME_ZONE`) | `db_core/betriebszeit.py` — **`Betriebszeitpunkt` ist Pflicht an jedem Eingabefeld** |
| Endpunkt ohne Login, per Token gesichert | Muster: `POST /api/lieferant/warenkorb-return/{token}`, `auth=None` |
| Token nur als SHA-256 in der DB | Muster: `pricing.punchout_session`, `security.device_token` |
| CSRF-Prüfung an `auth=None`-Endpunkten | `api/auth.py::_require_csrf` |
| Drosselung nach Fehlversuchen (DB-gestützt) | `db_core/services/login_schutz.py` |
| Internes Postfach für Benachrichtigungen | `notify.notification` (Migration 0137/0138) |

## 4. Datenmodell — drei neue Tabellen

Hand-SQL in einer Django-Migration mit `RunSQL` (Fachschema!), plus die
State-only-Migration für die Models (`makemigrations db_core`). Nummer beim Bau
am tatsächlichen Stand prüfen — nach 0138 wären es 0139 (SQL) und 0140 (State).

Alle drei erben den **vollen Schutzstandard**: Audit-Trigger, No-Delete,
No-Truncate (CLAUDE.md). Keine Ausnahme — siehe Begründung im Kopf von 0137.

### 4.1 `workflow.booking_offer` — der Rahmen, den die Disposition freigibt

```sql
CREATE TABLE workflow.booking_offer (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- An WELCHEN Einsatz gebucht wird. Der Einsatz existiert bereits und ist
    -- UNGEPLANT (Rückstand). Die Buchung plant ihn — sie legt keinen neuen an.
    service_job_id          uuid NOT NULL REFERENCES workflow.service_job (id),
    -- Wessen Kalender das Raster erzeugt (Entscheidung 2: konkreter Mitarbeiter).
    assignee_id             uuid NOT NULL REFERENCES security.app_user (id),
    -- Rahmen: Zeitraum, in dem überhaupt Slots entstehen dürfen.
    window_from             date NOT NULL,
    window_to               date NOT NULL,
    -- Tagesfenster in Ortszeit (Europe/Berlin), z. B. 08:00–16:00.
    day_start               time NOT NULL,
    day_end                 time NOT NULL,
    slot_minutes            integer NOT NULL CHECK (slot_minutes BETWEEN 15 AND 480),
    -- Vorlauf: frühester buchbarer Slot = now() + lead_hours.
    lead_hours              integer NOT NULL DEFAULT 48 CHECK (lead_hours >= 0),
    status                  text NOT NULL DEFAULT 'AKTIV'
                            CHECK (status IN ('AKTIV', 'EINGELOEST', 'GESCHLOSSEN')),
    created_by              uuid NOT NULL REFERENCES security.app_user (id),
    version                 integer NOT NULL DEFAULT 1,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    CHECK (window_to >= window_from),
    CHECK (day_end > day_start)
);
```

Der Statusautomat ist bewusst schmal: `AKTIV` → `EINGELOEST` (der Kunde hat
gebucht) oder `AKTIV` → `GESCHLOSSEN` (die Disposition zieht die Freigabe zurück).
Beides final.

### 4.2 `workflow.booking_link` — der Link selbst

```sql
CREATE TABLE workflow.booking_link (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    offer_id      uuid NOT NULL REFERENCES workflow.booking_offer (id),
    -- NUR der SHA-256-Hex-Hash. Der Klartext verlässt den Server genau einmal:
    -- in der Antwort auf „Link erzeugen". Muster: security.device_token (0116),
    -- pricing.punchout_session (0056). Ein DB-Leak gibt keine nutzbaren Links.
    token_hash    text NOT NULL UNIQUE CHECK (btrim(token_hash) <> ''),
    expires_at    timestamptz NOT NULL,
    used_at       timestamptz,
    revoked_at    timestamptz,
    created_by    uuid NOT NULL REFERENCES security.app_user (id),
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
```

Token: `secrets.token_urlsafe(32)`. Widerruf über `revoked_at` (stilllegen statt
löschen — die Tabelle trägt ohnehin den No-Delete-Schutz).

### 4.3 `workflow.booking_hold` — die Reservierung während des Ausfüllens

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;  -- ggf. schon vorhanden, prüfen

CREATE TABLE workflow.booking_hold (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    offer_id     uuid NOT NULL REFERENCES workflow.booking_offer (id),
    assignee_id  uuid NOT NULL REFERENCES security.app_user (id),
    slot         tstzrange NOT NULL,
    expires_at   timestamptz NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    -- Zwei gleichzeitige Kunden dürfen nicht denselben Slot desselben Monteurs
    -- halten. Die Sperre gilt nur für noch NICHT abgelaufene Holds.
    EXCLUDE USING gist (
        assignee_id WITH =, slot WITH &&
    ) WHERE (expires_at > now())
);
```

⚠️ **`WHERE (expires_at > now())` funktioniert so NICHT** — ein partieller Index
darf keine volatile Funktion enthalten. Beim Bau daher eine der beiden Varianten
wählen und im Migrationskopf begründen:

* **(a)** Statusspalte `active boolean` statt Zeitvergleich; ein abgelaufener Hold
  wird beim nächsten Zugriff auf `false` gesetzt (lazy expiry). `EXCLUDE … WHERE (active)`.
* **(b)** Kein partieller Index; abgelaufene Holds werden beim Buchungsversuch
  innerhalb derselben Transaktion gelöscht (No-Delete-Schutz beachten → dann doch (a)).

Empfehlung: **(a)**. Holds sind kurzlebig, aber die Tabelle trägt den
No-Delete-Schutz — löschen geht gar nicht, stilllegen ist ohnehin das Hausmuster.

## 5. Nebenläufigkeit — der eigentlich heikle Punkt

Zwei Kunden, ein Slot. Ohne Sperre entstehen zwei Buchungen.

**Die Doppelbelegung ist in MCN heute bewusst eine *weiche* Invariante**: Die
Plantafel warnt (`belegungs_warnungen`), die Datenbank verbietet nichts — der
Notdienst am Sonntag soll nicht an einem gesperrten Board scheitern (Migration 0025).

Für die Selbstbuchung muss sie **hart** sein. Aber **nur dort**:

1. Der Buchungsservice nimmt zu Beginn `SELECT … FROM workflow.booking_offer
   WHERE id = %s FOR UPDATE`. Damit serialisieren alle Buchungsversuche auf
   dieselbe Freigabe. Durchsatz ist irrelevant (ein Kundenlink, kein Ticketshop).
2. Danach wird die Verfügbarkeit **neu** berechnet — unter der Sperre, gegen den
   committeten Stand. Ist der Slot inzwischen weg: `409` mit „Der Termin wurde
   gerade vergeben, bitte wählen Sie einen anderen" und frischer Slotliste.
3. Erst dann `planung_service` aufrufen.

Betriebsannahme aus `db/README.md` gilt unverändert: READ COMMITTED, `SET LOCAL`,
**Retry-Pflicht** bei Serialisierungsfehlern.

Die Plantafel bleibt weich. Das ist kein Widerspruch, sondern die Trennung
zwischen „ein Mensch entscheidet bewusst" und „ein Automat schreibt blind".

## 6. Der Schreibpfad — kein Sonderweg an den Triggern vorbei

Die Buchung ist ein fachlicher Write und läuft daher über
`db_core.db_context.business_transaction` und die vorhandenen Services
(`planung_service` / `einsatz_service`). Sie ruft keine eigene SQL-Abkürzung auf.

Dafür braucht es einen **Systemakteur**: einen `security.app_user`
(z. B. `Online-Terminbuchung`) mit einer eigenen Rolle, die genau
`workflow`/`AENDERN` trägt — nicht mehr. Anlage in derselben Migration.

Konsequenzen, die so gewollt sind:

* Der Audit-Trail zeigt den Systemakteur als Schreiber; **wer** gebucht hat, steht
  über `booking_link.id` daneben (die Buchung vermerkt den verwendeten Link).
* Der Statuswechsel läuft durch dieselben Tore wie bei einem Menschen. Genau das
  ist die Vision aus CLAUDE.md — der Automat geht durch die Tür, nicht durchs Fenster.

Was die Buchung konkret tut, in einer Transaktion:

1. Hold prüfen/setzen
2. `set_schedule` auf den Einsatz (UNGEPLANT → GEPLANT)
3. `assign_user` mit dem Mitarbeiter aus dem Offer
4. Offer auf `EINGELOEST`, Link auf `used_at`
5. `notify.notification` an die Disposition („Kunde hat Termin gewählt")

## 7. API

### Öffentlich (`auth=None`, Token in der URL)

| Endpunkt | Zweck |
|---|---|
| `GET /api/buchung/{token}` | Kontext + freie Slots. Setzt das csrftoken-Cookie. |
| `POST /api/buchung/{token}` | Slot buchen. CSRF-Prüfung wie `api/auth.py::_require_csrf`. |
| `POST /api/buchung/{token}/absage` | Absage (falls F3 „ja") |

**Was `GET` liefern darf — und was nicht.** Nur:
Auftragstitel und -nummer, Adresse des Einsatzortes (die kennt der Kunde),
Terminart, Dauer, und eine **flache Liste freier Startzeitpunkte**.

Nicht: Mitarbeitername (solange F2 offen), belegte Zeiten, Abwesenheiten,
Begründungen, andere Termine, andere Kunden, Preise, interne Notizen,
`access_instructions`. Die Antwort ist eine Positivliste, kein gefiltertes
Innenleben — sie wird explizit zusammengebaut, nie aus einem bestehenden
`…Out`-Schema abgeleitet.

### Intern (Session-Auth, `require(request, "workflow", "AENDERN")`)

| Endpunkt | Zweck |
|---|---|
| `POST /api/planung/einsaetze/{id}/buchungslink` | Offer + Link anlegen, Klartext-URL **einmalig** zurückgeben |
| `DELETE …/buchungslink/{link_id}` | Widerrufen (`revoked_at`) |
| `GET …/buchungslinks` | Welche Links sind offen? |

## 8. Verfügbarkeitsberechnung (`db_core/services/buchung.py`)

Reine Funktion, gut testbar, ohne HTTP:

```
freie_slots(offer) ->
    Raster aus window_from…window_to × day_start…day_end, Schritt slot_minutes
    MINUS Tage, an denen der Vertrag 0 Sollstunden hat (hours_<wochentag> = 0)
    MINUS Feiertage (hr.holiday, region NULL oder passend)
    MINUS genehmigte Abwesenheiten (hr.absence, Status GENEHMIGT; Halbtage beachten)
    MINUS bestehende Einsätze des Mitarbeiters, die den Slot überlappen
    MINUS aktive Holds
    MINUS alles vor now() + lead_hours
```

**Zeitzonenfalle:** `day_start`/`day_end` sind Ortszeit (Europe/Berlin),
`settings.TIME_ZONE` ist bewusst UTC. Das Raster wird in `BETRIEBS_TZ` gebaut und
erst danach nach UTC gerechnet — sonst verschiebt sich das Fenster mit der
Sommerzeit um eine Stunde. `db_core/betriebszeit.py` lesen, bevor hier eine Zeile
entsteht; die Falle ist dort ausführlich dokumentiert.

## 9. Frontend

* Eigene Route **außerhalb** des Leitstand-Shells: `/termin/:token`. Keine
  Navigation, kein Auth-Guard, kein Sidebar-Layout. Derselbe Angular-Build.
* Ansicht: Auftragskontext oben, darunter Tage als Spalten/Liste, Slots als
  echte `<button>`. Bestätigungsschritt, Erfolgsseite.
* **WCAG 2.2 AA ist nicht verhandelbar:** freie/belegte Slots nie nur über Farbe
  (Text + Zustand), volle Tastaturbedienung, sichtbarer Fokus, `aria-pressed` am
  gewählten Slot, `prefers-reduced-motion`, Light + Dark.
* `#ef804e` auf Weiß erreicht nur ≈ 2,7:1 — für Slot-Beschriftungen die
  abgedunkelte Token-Variante, Reinform nur für Flächen.
* Auf der Erfolgsseite: **ICS-Download** für den Kalender des Kunden. Braucht
  keinen Mailversand und ist deshalb sofort möglich.
* Disponentenseite: Button „Buchungslink erzeugen" am Einsatz, Klartext-URL
  einmalig mit Kopieren-Knopf. Danach nicht mehr abrufbar (nur Hash gespeichert) —
  das muss im UI deutlich stehen.

## 10. Sicherheits-Checkliste (vor dem Merge abhaken)

- [ ] Token ≥ 32 Byte urlsafe, nur SHA-256-Hex in der DB, Vergleich mit `secrets.compare_digest`
- [ ] `expires_at` gesetzt (Vorschlag: 14 Tage) und serverseitig geprüft
- [ ] Drosselung je IP auf `GET/POST /api/buchung/*` nach dem Muster `login_schutz.py`;
      ohne Bremse ist der Link ein Slot-Scraper
- [ ] Unbekannter/abgelaufener/widerrufener Token → **immer dieselbe** Antwort
      (kein Timing- und kein Wortlaut-Orakel; `_ZU_VIELE` in `api/auth.py` ist das Vorbild)
- [ ] CSRF am POST geprüft (`_require_csrf`-Muster)
- [ ] Öffentliche Antwort explizit als Positivliste gebaut, kein bestehendes Out-Schema
      wiederverwendet
- [ ] Kein Mitarbeitername, keine Abwesenheit, kein Abwesenheitsgrund in der
      öffentlichen Antwort (solange F2 nicht anders entschieden)
- [ ] Der Link erlaubt **nur** Buchen (und ggf. Absagen) — kein Lesen fremder Daten,
      kein Ändern von Adresse/Auftrag/Kontakt
- [ ] Schreibpfad über `business_transaction` + Systemakteur mit minimalem Recht
- [ ] Rate-Limit-Tabelle trägt Schutzstandard, keine personenbezogene IP-Speicherung
      über das Nötige hinaus

## 11. Reihenfolge der Umsetzung

| # | Schritt | Fertig, wenn |
|---|---|---|
| 1 | Migration: 3 Tabellen + Schutzstandard + Systemakteur/Rolle | `manage.py migrate` gegen Wegwerf-DB grün, `makemigrations --check` sauber |
| 2 | State-only-Migration + Models (`managed = False`) | `manage.py check` grün |
| 3 | `services/buchung.py`: `freie_slots` (reine Funktion) | Unit-Tests inkl. Sommerzeitwechsel, Feiertag, Halbtags-Abwesenheit |
| 4 | `services/buchung.py`: `buche_slot` mit `FOR UPDATE` | Nebenläufigkeitstest gegen Wegwerf-DB: 2 parallele Buchungen → 1 Erfolg, 1× 409 |
| 5 | Interne API: Link erzeugen/widerrufen/auflisten | API-Tests, Recht `workflow/AENDERN` fail-closed geprüft |
| 6 | Öffentliche API: GET/POST mit Token, CSRF, Drosselung | API-Tests inkl. abgelaufen/widerrufen/unbekannt → gleiche Antwort |
| 7 | Angular: `/termin/:token` | manuell durchgeklickt, Tastaturbedienung geprüft |
| 8 | Angular: Link-Erzeugung am Einsatz | `ng build` grün |
| 9 | Opus-Review (Review-Pflicht, max. 4 Runden) | Review sauber |

Verifikation wie immer: `uv run python manage.py check`, `uv run pytest`,
`ng build`. Nebenläufigkeitstest **nur** gegen eine Wegwerf-DB.

## 12. Was dieser Plan bewusst NICHT enthält

* **Öffentlicher Buchungslink (Calendly-Stil)** für die Website. Braucht ein
  öffentliches Kontaktformular, Dublettenprüfung gegen `identity.party` und
  Spam-Schutz — eigener Slice, setzt auf denselben Unterbau auf.
* **Bestätigungs-Mail an den Kunden.** `MCN_EMAIL_BACKEND=…console.EmailBackend`
  ist laut CLAUDE.md die einzige verbleibende Sperre gegen echten Mailversand auf
  dem Produktivsystem. Solange sie steht, geht keine Mail raus. Bis dahin:
  Bestätigungsseite + ICS-Download + interne Benachrichtigung. Das reicht
  praktisch, weil der Disponent den Link ohnehin selbst per Mail/WhatsApp
  verschickt — er kopiert ihn aus dem Leitstand.
* **Erinnerung vor dem Termin.** Hängt am selben Mailschalter.
* **Umbuchen durch den Kunden.** Siehe F3.

## 13. Offene Fragen — vor dem ersten Commit klären

**F1 — Was passiert, wenn der gebuchte Monteur ausfällt?**
Der Kunde hat einen konkreten Menschen gebucht. Krankmeldung heißt: Termin
umbesetzen (Kunde merkt nichts) oder absagen und neu anbieten? Ersteres
widerspricht der Zusage, Letzteres ärgert den Kunden. → beeinflusst, ob der Name
überhaupt angezeigt wird.

**F2 — Sieht der Kunde den Namen des Monteurs?**
Dieser Plan sagt vorerst nein (intern gebunden, extern anonym). Wenn ja: welcher
Name (Vorname? Vor- und Zuname?) und ist bewusst akzeptiert, dass sich aus dem
Slot-Raster die Arbeitszeit und die Abwesenheit dieses Mitarbeiters ableiten lässt?

**F3 — Darf der Kunde über denselben Link absagen oder umbuchen?**
Wenn ja: bis wann (z. B. bis 24 h vorher), und wird der Einsatz dann `AUSGEFALLEN`
(begründungspflichtig) oder zurück in den Rückstand (`UNGEPLANT`)? Ohne Absageweg
ruft der Kunde an — was in Ordnung ist, aber dann soll der Link auch nicht so tun,
als ginge es online.

**F4 — Gültigkeitsdauer und Einmal-/Mehrfachnutzung des Links.**
Vorschlag: 14 Tage, einmal einlösbar, danach nur noch Anzeige des gebuchten
Termins. Bei F3 „ja" muss er länger nutzbar bleiben.

**F5 — Wer darf Buchungslinks erzeugen?**
Vorschlag: `workflow`/`AENDERN` (Disposition und Leitung), Monteure nicht.

**F6 — Gilt der Link auch für Wartungsverträge?**
Der interessanteste Massenfall: „Ihre Jahreswartung steht an, wählen Sie einen
Termin." Das wären viele Links auf einmal — und ohne Mailversand müsste jeder
einzeln kopiert werden. Frühestens sinnvoll, wenn der Mailschalter fällt.
Siehe `docs/roadmap/` zum Wartungsvertrag.
