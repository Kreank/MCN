# Befunde aus dem Disponenten-Test

Sammelstelle für Saschas Praxistest aus Sicht eines Disponenten (ab 2026-07-20).
Freitext-Notizen liegen weiterhin in `docs/issue.md`; hier steht die technisch
eingeordnete Fassung.

> **Für den Dev-Agenten:** Dieses Dokument ist ein **Arbeitsauftrag** — Sascha hat
> die Punkte selbst gemeldet und die Bearbeitung freigegeben. Die Fundstellen sind
> gegen den Stand `987b517` (main, 2026-07-21) verifiziert. Reihenfolge und
> Zuschnitt der Arbeitspakete stehen am Ende unter *Arbeitspakete*.
>
> **Drei Dinge nicht ohne Rückfrage anfassen:** die als `REGEL` markierten Befunde,
> die unter *Offene Entscheidungen* gelisteten Punkte, und alles, was einen
> Migrationskopf über 0123 hinaus verschiebt, ohne dass die Suite grün ist.

**Einordnung je Befund:**

- **UI** — Datenbank und Regelwerk erlauben es, es fehlt nur Endpunkt/Oberfläche. Billig.
- **MODELL** — Schemaänderung/Migration nötig. Braucht eine fachliche Entscheidung.
- **REGEL** — bewusst so gebaut und per DB-Trigger durchgesetzt. Nur mit Begründung anfassen.

Status: `offen` · `entschieden` · `umgesetzt`

---

## A — Termin setzen dauert zu lange

| # | Befund | Art | Fundstelle | Status |
|---|---|---|---|---|
| A1 | Termin-Dialog selbst ist schlank (1 Pflichtfeld, 3 Klicks, ein API-Call). Der Zeitfresser sitzt davor: der Auftrag. | — | `backend/api/planung.py:1308` | analysiert |
| A2 | Auftrags-Freigabe verlangt Beauftragungsnachweis + bestätigte Zuständigkeit + Auftraggeber. Der Disponent wird durch die ganze Kette geschickt. | REGEL | `db/migrations/0013_auftrag.sql:161-179` | offen |
| A3 | **Für Terminieren ist die Freigabe gar nicht nötig** — die DB erlaubt GEPLANT/BESTAETIGT auf nicht freigegebenem Auftrag. Erst UNTERWEGS verlangt sie. Wenn die UI trotzdem blockt, ist das ein UI-Fehler. | UI | `db/migrations/0014_einsatz.sql:84` | **widerlegt 2026-07-21** |
| A3a | **Es gibt keine UI-Blockade.** Alle Terminanlage-Pfade (Auftrag-Detail `auftrag-detail.ts:114`, Plantafel `plantafel.ts:1817-1855`, Einsätze-Liste `einsaetze.ts:157`, Anruf-Dialog) gaten ausschließlich auf das **Recht** `workflow/ANLEGEN`, nie auf `WorkOrderStatus`. Die Auftragsauswahl im Termin-Dialog ist ungefiltert. DB, Service (`services/einsatz.py`, `api/planung.py:1308`) und UI sind konsistent. | — | — | geklärt |
| A3b | Wahrscheinlichere Ursachen der Testnacht: (1) der Termin ging, aber `UNTERWEGS` scheiterte — das ist das gewollte Tor B-01/A-23; (2) **fehlendes Recht** sieht in der Oberfläche exakt aus wie eine Statussperre (`row_scope = EIGENE` blendet „+ Termin" ganz aus, Server antwortet 403); (3) der Auftrag existierte schlicht noch nicht → das ist A1. | — | `0014_einsatz.sql:81-88` | **offen — Sascha: Klickpfad** |
| A4 | Vorschlag: Freigabe-Checkliste am Auftrag — drei Zeilen Haken/Kreuz, jede Lücke **inline** nachtragbar statt in drei Masken. | UI | `auftrag-detail.{ts,html,scss}` | **umgesetzt 2026-07-21** |

**Offene Frage an Sascha:** konkreter Klickpfad der Testnacht (über „Neuer Auftrag" oder aus der Plantafel-Zelle?).

---

## B — Pflichtfelder

| # | Befund | Art | Fundstelle | Status |
|---|---|---|---|---|
| B1 | Vorname ist Pflichtfeld — soll optional werden. Vierfach abgesichert: Frontend-Validator, API-Schema, Service-Guard, DB-CHECK `NOT NULL CHECK (btrim <> '')`. | MODELL | `kontakte.ts:103`, `api/identity.py:141`, `services/identity.py:74`, `0002_...sql:99` | offen |
| B2 | Betroffen sind vier weitere Formulare: Kontakt-Detail, Anruf-Dialog, Schnellerfassung, `identity.py:578`. | UI | — | offen |
| B3 | Nachname soll Pflicht bleiben (Vorschlag, Bestätigung offen). | — | — | offen |
| B4 | Geburtsdatum ist **bereits überall optional** — kein Handlungsbedarf, evtl. nur Feld-Optik missverständlich. | — | `0002_...sql:101` | geklärt |

---

## C — Plantafel: Kategorie-Farbe

| # | Befund | Art | Fundstelle | Status |
|---|---|---|---|---|
| C1 | Kategoriefarbe erscheint nur als kleiner Badge, soll die ganze Kachel färben. | UI | `plantafel.html:617-621` | **umgesetzt 2026-07-21** |
| C2 | Farben liegen als Tokens in 8 CSS-Zeilen — Umstellung ist billig. | UI | `styles.scss:135-142` | **umgesetzt** (Klasse wandert auf die Kachel, `--kat-hue` vererbt) |
| C3 | Kollision: der linke Kachelrand zeigt heute den **Status**. Vorschlag: Fläche = Kategorie (getönt), linker Rand = Status. Volle Sättigung geht nicht (Textkontrast/WCAG). | UI | `plantafel.scss:470-493` | **umgesetzt** (Status-Hintergründe entfernt, Rand bleibt Status) |
| C4 | **Beim Umsetzen aufgefallen — WCAG-Falle, behoben:** `--ink-hint`/`--ink-faint` sind exakt auf `--surface` gerechnet (5,48:1 hell / 5,21:1 dunkel). Auf der getönten Kachel fielen sie unter AA — auf `kat-amber` dunkel bis **3,99:1**, und zwar ausgerechnet auf `.tile__statuskurz`, dem Textersatz für die Statusfarbe. `.tile__statuskurz` und `.tile__adresse` tragen jetzt `--ink-muted` (schlechtester Fall 5,08:1). **Merksatz: wer eine Fläche tönt, muss die Textstufen darauf neu rechnen.** | UI | `styles.scss` `.tile__statuskurz`, `plantafel.scss` `.tile__adresse` | **behoben 2026-07-21** |
| C6 | **Rest unter Schwelle, bewusst gelassen:** Die Rahmen von `.tile__frei`/`.tile__serie` (`--line-strong`) und der Kachel-Außenrahmen (`--line`) fallen auf `kat-navy` hell von 3,76:1 auf **2,92:1**. Beides ist nicht-interaktiv und nicht-textuell, der Text der Badges trägt `--ink-muted` — WCAG 1.4.11 greift hier nicht zwingend. Falls es doch stört: `border-color: color-mix(in srgb, var(--line-strong) 100%, var(--ink) 15%)`. | — | `plantafel.scss:401` | akzeptiert |
| C5 | **Grenze der Lösung, bewusst akzeptiert:** Im **dunklen** Theme kollabieren die acht Töne bei 16 % Tönung auf sehr ähnliche Werte (Orange `#3e3e46` vs. Amber `#3b4345`); minimale CIE76-Distanz dE ≈ 4,3. Hellere Dark-Varianten wurden durchgerechnet: sie bringen nur dE 4,4 und kosten Kontrast (bei 20 % unter AA). Der Engpass ist strukturell — eine Tönung, die den Text lesbar lässt, kann auf dunklem Grund keine acht Kategorien tragen. Kein Rückschritt (vorher gab es gar keine Tönung), aber im Dunkeln bleibt der **Chip mit Namen** der eigentliche Träger. | — | — | akzeptiert |

---

## D — Navigationsleiste unübersichtlich

| # | Befund | Art | Fundstelle | Status |
|---|---|---|---|---|
| D1 | 24 flache Einträge ohne Gruppierung. | UI | `frontend/src/app/app.ts:40-154` | **umgesetzt 2026-07-21** |
| D2 | Vorschlag Gruppen: Tagesgeschäft · Stammdaten · Kaufmännisch · Freigaben · Personal · System. | UI | `app.ts` `NAV_GRUPPEN` | **umgesetzt, 7 statt 6 Gruppen** |
| D3 | Abweichung vom Vorschlag: **eigene Gruppe „KI"** (KI-Vorschläge, KI-Assistent) statt Anhängsel am Tagesgeschäft — begründet aus CLAUDE.md („KI + CRM, nicht CRM + KI": die KI ist eigener Akteur). Zusätzlich sortiert: Projekte und Wartung ins Tagesgeschäft, Auftragsfreigabe zu den Freigaben (beides Entscheidungs-Warteschlangen). | — | — | umgesetzt |
| D4 | **Falle beim Umbau:** Die dekorative „Messkante"-Marke rechnete ihren Weg als `activeIndex × 3rem` über eine **flache, lückenlose** Liste. Gruppenköpfe dazwischen hätten sie dauerhaft verschoben. Gelöst über eine zweite Zählgröße `activeHeads × --nav-head-h` mit **fixer** Kopfhöhe (1,9 rem, `box-sizing: border-box`, Rand innen) — die Kopfhöhe darf nie aus dem Inhalt folgen. | UI | `app.scss` `.messkante__mark` | umgesetzt |

---

## E — Status-System / zu viele Vorbedingungen

| # | Befund | Art | Fundstelle | Status |
|---|---|---|---|---|
| E1 | Übergangstabellen sind sauber und liegen in der DB; das Problem ist nicht das Regelwerk, sondern dass die UI den Nutzer **suchen** lässt, was fehlt. | — | `services/auftrag.py:96-118`, `services/einsatz.py:69-79` | analysiert |
| E2 | Fehlende Angaben werden erst beim Scheitern sichtbar, Nachtragen erzwingt Maskenwechsel. → siehe A4. | UI | — | offen |
| E3 | Notfall-Ausnahme existiert bereits (`is_emergency` hebt Zuständigkeit + Auftraggeber auf, Beauftragungsnachweis bleibt). Im UI vermutlich zu wenig sichtbar. | UI | `0013_auftrag.sql:167` | **umgesetzt 2026-07-21** (in der Checkliste erklärt) |
| E4 | **Beim Umsetzen von E3 aufgefallen:** `is_emergency` ist nur bei der **Anlage** setzbar (`api/auftrag.py:396`), es gibt keinen PATCH. Stellt sich erst nach dem Anlegen heraus, dass Gefahr im Verzug ist, lässt sich der Auftrag nicht mehr umstellen. Die Checkliste sagt das jetzt ehrlich; behoben ist es nicht. | UI | `api/auftrag.py:396` | **offen — neu** |

---

## F — Kontakte: Anlage-Fluss

| # | Befund | Art | Fundstelle | Status |
|---|---|---|---|---|
| F1 | Anlage-Dialog kennt nur Namensfelder. Telefon/Mail und Adresse erst danach, in **zwei verschiedenen Reitern** der Kontaktmappe. | UI | `kontakte.html:224-269`, `kontakt-detail.ts:150-168` | offen |
| F2 | Nach dem Anlegen wird **nicht** in die Mappe navigiert — der Kontakt muss in der Liste wiedergefunden werden. | UI | `kontakte.ts:376-386` | offen |
| F3 | Zwei Endpunkte können Person + Telefon + Mail + Adresse bereits atomar: `POST /workflow/quick-intake` und `POST /planung/anruf`. Beweis, dass der kombinierte Weg fachlich zulässig ist. | UI | `api/projekt.py:1204`, `api/telefonauftrag.py:129` | offen |
| F4 | Einschränkung: beide erzeugen Kontaktwege, aber **keine `party_address`** — die Adresse landet nur an der Liegenschaft. | UI | `services/telefonauftrag.py:269-283` | offen |
| F5 | Reiter heißt „Objektadressen", zeigt aber Kontaktadressen (`party_address`). Irreführend. | UI | `kontakt-detail.ts:153` | offen |

---

## G — Adressen: Vererbung Kontakt ↔ Liegenschaft

| # | Befund | Art | Fundstelle | Status |
|---|---|---|---|---|
| G1 | 90 % der Kontakte gehören zu einer Liegenschaft und sollten die Objektadresse erben können (in beide Richtungen). | UI | — | offen |
| G2 | **Das Schema erlaubt es schon**: `identity.address` ist ein gemeinsamer Topf, `party_address.address_id` und `property.address_id` könnten dieselbe Zeile referenzieren. | UI | `0003_...sql:9-20`, `0004_property.sql:18` | offen |
| G3 | Kein Codepfad tut es — jeder Service legt **immer** eine neue Adresszeile an, kein Dedup-Lookup, kein Endpunkt zum Referenzieren einer bestehenden `address_id`. Folge: Dubletten. | UI | `services/identity.py:378-386`, `services/property.py:78-88` | offen |
| G4 | Es gibt bereits einen Dubletten-**Report**, aber keine Zusammenführung. | UI | `api/property.py:305` | offen |

---

## H — Nichts lässt sich nachträglich bearbeiten

**Kernbefund: auf Kontaktdaten existiert genau EINE echte Sperre.** Alles andere ist fehlende API-Oberfläche — kein Trigger, kein Regel-Kürzel, keine Begründung im Code.

| # | Befund | Art | Fundstelle | Status |
|---|---|---|---|---|
| H1 | Adress-**Inhalt** (Straße/PLZ/Ort) ist append-only, UPDATE und DELETE per Trigger verboten. Korrektur = neue Adresse. | **REGEL** | `0003_...sql:23-25` | bewusst |
| H2 | Telefon/E-Mail nicht korrigierbar — `contact_point` hat **null Trigger**, es fehlt `PUT /contact-points/{id}`. Ersatzkonvention „beenden + neu" ist dokumentiert, aber nicht erzwungen. | UI | `api/identity.py:464-508` | offen |
| H3 | Adress**zuordnung** weder korrigierbar noch beendbar noch entfernbar — `party_address` hat außer POST **keine einzige** Schreiboperation. Adressliste im UI hat deshalb keinen einzigen Aktions-Button. | UI | `api/identity.py:513-547`, `kontakt-detail.html:238-249` | offen |
| H4 | Personen-/Organisationsnamen nicht änderbar (Heirat, Umfirmierung). Kein Trigger, kein Endpunkt. | UI | `api/identity.py` | offen |
| H5 | Liegenschaftsadresse nicht änderbar — `api/property.py` hat **kein einziges** PATCH/PUT. | UI | `api/property.py:527-660` | offen |
| H6 | **DSGVO trägt als Begründung nicht** — Art. 16 verlangt das Recht auf Berichtigung. Die Unveränderlichkeitsregeln (WF-01/F-02) betreffen die Auftragswelt, nicht Kontaktstammdaten. | — | `0015_workflow_schutz.sql:61-78` | geklärt |
| H7 | Keine Audit-Trigger auf `address`, `party_address`, `contact_point`, `person`, `organization`, `party`, `property` — es gibt keinen DB-Nachweis, wer wann was geändert hat. Beim Nachrüsten der Endpunkte mitziehen. | UI | `0009_...sql:142-163` | offen |
| H8 | Historie ist gespeichert (`valid_from`/`valid_until`), aber über keine Route abrufbar — `include_ended` existiert im Service, wird von der API nie durchgereicht. | UI | `services/identity.py:338-343` | offen |

---

## I — Liegenschaft: Struktur, Räume, Anlagen

| # | Befund | Art | Fundstelle | Status |
|---|---|---|---|---|
| I1 | Gebäude/Einheiten/Anlagen/Räume nachträglich nicht änderbar. **`building` und `unit` haben null Trigger** — reine API-Lücke, `PATCH` wäre ohne Schemaänderung nachrüstbar. | UI | `api/property.py` (nur POST) | offen |
| I2 | Beim Nachrüsten fehlen bei `building`/`unit` auch Audit-/No-Delete-/No-Truncate-Trigger — der Schutzstandard aus CLAUDE.md wurde hier nie angewandt. Muster: `0086_raumaufmass.py:200-212`. | MODELL | — | offen |
| I3 | Räume **sind** änderbar (`PATCH /rooms/{id}`) — das Raummodul ist das am weitesten ausgebaute UI der Objektwelt. Anlagen ebenfalls (`PATCH /assets/{id}`). Falls es sich anders anfühlt: UI-Auffindbarkeit prüfen. | — | `api/raum.py:645`, `api/anlage.py:327` | prüfen |
| I4 | „Doppelte Namen verboten" — **`building.name` hat KEIN UNIQUE.** Vorderhaus/Seitenflügel/Hinterhaus sind erlaubt; das Feld heißt „Bezeichnung" mit genau diesem Beispiel im Hinweistext. | — | `liegenschaft-detail.html:283` | geklärt |
| I5 | `UNIQUE (property_id, unit_number)` — Einheitsnummern sind pro Liegenschaft eindeutig, nicht pro Gebäude. Beschluss A-09. **Zurückgestuft 2026-07-21:** Sascha sieht die Hierarchie als korrekt umgesetzt an; erst anfassen, wenn im Praxistest wirklich eine Nummernkollision auftritt. | MODELL | `0004_property.sql:91` | zurückgestellt |
| I6 | **Echter Blocker 2:** `room_dublette` enthält `unit_id`, aber **nicht `building_id`**. „Treppenhaus EG" geht nur einmal pro Liegenschaft. | MODELL | `0086_raumaufmass.py:191` | offen |
| I7 | **Echter Blocker 3:** Wurde ein Gebäude ohne Bezeichnung angelegt, ist es nie wieder benennbar → dauerhaft „Gebäude 1/2/3". Folge aus I1. | UI | `liegenschaft-detail.html:107` | offen |
| I8 | Eigene Ebene „Gebäudeteil" zwischen building und unit. **Verworfen 2026-07-21:** Sascha bestätigt die Hierarchie Gebäude → Einheit → Raum als richtig. Vorderhaus/Hinterhaus bleiben eigene `building`-Zeilen. | — | — | verworfen |
| I12 | **Die Einheit hat kein Etagen-Feld.** „Wohnung X liegt auf Etage Y" ist nicht abbildbar — `storey` hängt am *Raum* (`property.room`), nicht an der Wohnung. Fachlich verkehrt herum: die Wohnung liegt auf der Etage, die Räume liegen in der Wohnung. Ein Feld `unit.storey` (Freitext wie beim Raum, wegen Souterrain/Hochparterre). | MODELL (klein) | `0004_property.sql:78-95` vs. `0086_raumaufmass.py:141` | offen |
| I13 | **Kernbefund Struktur:** Modell und Funktionen sind vollständig da — Gebäude anlegen, Einheit hinzufügen, Raum erstellen und zuordnen. Aber der zusammenhängende Vorgang ist über **3–7 Reiter** verstreut. „Wirkt, als solle der User möglichst viel klicken statt zu arbeiten." Kein Modellproblem. Vorschlag: **ein** Struktur-Screen mit Baum (Gebäude ▸ Einheit ▸ Raum), in dem alle drei Ebenen ohne Reiterwechsel angelegt, benannt und zugeordnet werden. | UI | `liegenschaft-detail.html` | **offen — Kernstück** |
| I9 | Mehr Infos am Raum gewünscht. Vorhanden sind bereits: Etage, Raumtyp, Fläche, Höhe, Volumen (generiert), Umfang, Innentemperatur, Luftwechsel, Heizlast-Kennwert, Steigleitungsabstand, Notiz. **Keine Raumnummer.** Konkretisieren, was fehlt. | — | `0086_raumaufmass.py:136-192` | **offen — Sascha** |
| I10 | Räume sollen Belegungs-Infos mit anzeigen (heute nur über Belegung erreichbar). | UI | — | offen |
| I11 | Mehr Logik bei Struktur-/Raumzuweisung gewünscht — noch zu konkretisieren. | — | — | **offen — Sascha** |

---

## J — Verwaltung / Eigentümer / Mieter

| # | Befund | Art | Fundstelle | Status |
|---|---|---|---|---|
| J1 | Diese drei Angaben müssen für den Disponenten **sofort sichtbar** sein, nicht klein in einer Ecke. Vorschlag: Kopfzeile der Liegenschaft. | UI | — | offen |
| J2 | Ebenfalls in die Kopfzeile: wer bis zu welchem Betrag beauftragen darf (`management_authority`, Rollen ORDER/APPROVAL/EMERGENCY_ORDER mit `amount_limit`). Für den Dispo mindestens so wichtig. | UI | `0006_management.sql:215-244` | offen |
| J3 | **Mieter**: Modell + UI fertig und angebunden. | — | `0005_tenure.sql:211-279` | läuft |
| J4 | **Verwaltung**: Modell + UI fertig, inkl. Pflicht-Ansprechpartner, Zuständigkeiten mit Eskalationsreihenfolge, Teilmandate. | — | `0006_management.sql:11-63` | läuft |
| J5 | **Eigentum**: Tabellen seit Migration 0005 vollständig gebaut (Bruchanteile, exakte LCM-Anteilsprüfung, Quellennachweis, Bestätigung) — aber **kein einziger API-Endpunkt, keine UI**. Frontend gibt es selbst zu: „sobald die Lesepfade angebunden sind (Roadmap 03)". | UI | `0005_tenure.sql:12-86`, `liegenschaft-detail.html:183` | **offen — groß** |
| J6 | **Modelllücke:** `ownership_period.unit_id` ist NOT NULL — ein Eigentümer kann nur **Einheiten** besitzen, kein ganzes Gebäude/Grundstück. Für „Gebäude 52 gehört Herrn X" muss heute jede Wohnung einzeln erfasst werden. Ersatz `property_party_role` = PROPERTY_OWNER kennt keine Anteile, keine Quelle, keine Bestätigung. | **MODELL** | `0005_tenure.sql:14`, `0004_property.sql:35-51` | **offen — groß** |
| J7 | Mehrere Eigentümer an einem Objekt: **geht bereits** über `ownership_interest` mit Bruchanteilen (SOLE/CO_OWNER). Fehlt nur die Anbindung (J5). | — | `0005_tenure.sql:65-86` | geklärt |
| J8 | Mieter UND Eigentümer gleichzeitig an einer Einheit: **geht bereits**, zwei getrennte Tabellenstränge ohne Konflikt. | — | — | geklärt |
| J9 | `property_party_role` lässt sich anlegen, aber nicht bearbeiten oder beenden — kein Endpunkt, kein UI-Knopf. | UI | `api/property.py:660` | offen |

---

## Das rote Thema hinter allen Befunden

Sascha hat es selbst auf den Punkt gebracht:

> „Wirkt so, als wenn der User hauptsache viel klicken soll, anstelle zu arbeiten."

Das zieht sich durch **A** (Termin), **E** (Status), **F** (Kontakt anlegen) und **I**
(Struktur) — und es ist in keinem dieser Fälle ein Datenmodell-Problem. Die
Funktionen sind da. Sie sind nur über zu viele Orte verteilt:

**Die Oberfläche ist entlang der Tabellen gebaut, nicht entlang der Arbeitsvorgänge.**
Pro Datensatz ein Reiter, statt pro Vorgang ein Screen. Wer eine Liegenschaft
aufnimmt, wandert durch 3–7 Reiter für einen einzigen zusammenhängenden Vorgang.

Das ist die gute Version des Problems: fast alles davon ist ohne Migration zu
beheben. Die Arbeitspakete unten sind entsprechend geschnitten — **nach
Arbeitsvorgang, nicht nach Tabelle.**

---

## Arbeitspakete

### AP1 — Struktur-Screen Liegenschaft *(größter Hebel)*

Ein Screen statt Reiterwanderung: Baum **Gebäude ▸ Einheit ▸ Raum**, in dem alle
drei Ebenen angelegt, benannt, umbenannt und zugeordnet werden — ohne den Screen
zu verlassen. Deckt ab: **I13, I1, I7, I10, I12**.

Dazu nötig (Backend, existiert noch nicht):
- `PATCH /api/property/buildings/{id}` — mindestens `name`, `building_number`, `address_id`
- `PATCH /api/property/units/{id}` — mindestens `unit_number`, `unit_type`, **neu `storey`**
- Migration: `property.unit.storey` text NULL, `CHECK (btrim(storey) <> '')` — Freitext wie `room.storey` (Souterrain/Hochparterre), **keine** Codeliste
- **Schutzstandard mitziehen** (I2): Audit-, No-Delete-, No-Truncate-Trigger für `building` und `unit` nach Muster `backend/db_core/migrations/0086_raumaufmass.py:200-212`. Diese beiden Tabellen unterlaufen ihn bis heute vollständig.

### AP2 — Kopfzeile Liegenschaft: Verwaltung / Eigentümer / Mieter

Deckt ab: **J1, J2, J9**. Sascha wörtlich: *„das sind Daten, die der Dispo schnell
wissen will"* — sofort sichtbar, nicht klein in einer Ecke.

- Verwaltung (`management_mandate`) und Mieter (`tenure.occupancy`) sind fertig angebunden, müssen nur nach oben.
- Mit anzeigen: **wer bis zu welchem Betrag beauftragen darf** (`management_authority`, Rollen ORDER/APPROVAL/EMERGENCY_ORDER mit `amount_limit`) — für den Dispo am Telefon die entscheidende Angabe.
- Eigentümer: siehe AP5, bis dahin Platzhalter mit ehrlichem Hinweis statt leerer Kachel.
- `property_party_role` braucht Bearbeiten/Beenden (J9), heute nur POST.

### AP3 — Kontakt in einem Rutsch anlegen

Deckt ab: **F1, F2, F3, F4, B1, B2, B3**.

- Anlage-Dialog um Telefon, E-Mail und Adresse erweitern. `POST /workflow/quick-intake` und `POST /planung/anruf` beweisen, dass das fachlich zulässig ist — die Muster dort übernehmen.
- Nach dem Anlegen **in die Kontaktmappe navigieren** (heute: nur Liste neu laden, Kontakt muss wiedergefunden werden).
- **Vorname optional** — vier Ebenen plus Migration: `kontakte.ts:103`, `api/identity.py:141`, `services/identity.py:74`, DB-CHECK `0002_...sql:99`. Nachname bleibt Pflicht. Mitziehen: `kontakt-detail.ts:474`, `anruf-dialog.ts:156`, `schnellerfassung.ts:107`, `api/identity.py:578`.
- Reiter „Objektadressen" umbenennen (F5) — er zeigt Kontaktadressen, nicht Objektadressen.

### AP4 — Daten korrigieren können

Deckt ab: **H2, H3, H4, H5, H7, H8**. Keiner dieser Fälle ist per Trigger gesperrt,
es fehlen schlicht die Endpunkte.

- `PUT /identity/parties/{id}/contact-points/{cp_id}` — Telefon/E-Mail korrigieren
- `PUT`/`DELETE` bzw. `/beenden` für `party_address` — hat heute **außer POST keine einzige** Schreiboperation
- `PATCH /identity/parties/{id}/person` bzw. `/organization` — Namensänderung (Heirat, Umfirmierung)
- `PATCH /property/properties/{id}` — Liegenschaftsadresse umhängen
- **Audit-Trigger nachrüsten** (H7) für `party_address`, `contact_point`, `person`, `organization`, `party`, `property` — sonst wächst mit jedem neuen Endpunkt die Nachweislücke.
- `include_ended` durchreichen (H8): existiert im Service, wird von der API nie angeboten.

⚠️ **Nicht antasten:** der Adress-**Inhalt** bleibt append-only (`db/migrations/0003_...sql:23-25`, H1). Korrektur = neue Adresszeile + Zuordnung umhängen. Das ist die einzige echte Regel in diesem Paket.

### AP5 — Eigentum anbinden *(größtes Paket, eigene Welle)*

Deckt ab: **J5, J6**.

- `tenure.ownership_period` / `ownership_interest` sind seit Migration 0005 vollständig gebaut — Bruchanteile, exakte LCM-Anteilsprüfung, Quellennachweis, Bestätigung. **Kein einziger Endpunkt, keine UI** greift darauf zu. Frontend sagt es selbst: `liegenschaft-detail.html:183` „sobald die Lesepfade angebunden sind (Roadmap 03)".
- ~~**Modelllücke J6**~~ — **entschieden 2026-07-21: keine Schemaänderung.** `ownership_period.unit_id` bleibt NOT NULL, Eigentum mit Anteilen/Quelle/Bestätigung bleibt auf Einheitsebene. Für „Gebäude 52 gehört Herrn X" wird die anteilslose Rolle `property_party_role` = PROPERTY_OWNER genutzt. Das Paket ist damit reine Anbindung (J5) und braucht keine Migration.

### AP6 — Sichtbares mit wenig Aufwand ✅ **umgesetzt 2026-07-21**

- **C1–C3:** Kategoriefarbe auf die ganze Plantafel-Kachel. Fläche getönt (16 %) = Kategorie, linker Rand bleibt Status; die früheren Status-Hintergründe sind entfallen, weil sie die Tönung überdeckt hätten. Siehe **C4** (Kontrastfalle, behoben) und **C5** (Grenze im Dunkeln, akzeptiert).
- **D1/D2:** Nav-Gruppen statt 24 flacher Einträge — Tagesgeschäft · Freigaben · KI · Stammdaten · Kaufmännisch · Personal · System. Siehe **D3** (Abweichung) und **D4** (Messkante-Falle).

### AP7 — Auftrag/Termin entzerren ✅ **umgesetzt 2026-07-21** (A3 widerlegt, A4/E3 gebaut)

Deckt ab: ~~A3~~, **A4, E2, E3**.

- ~~**A3 zuerst prüfen**~~ — **erledigt 2026-07-21, Befund widerlegt.** Es gibt keine UI-Blockade; alle Terminanlage-Pfade gaten nur auf das Recht `workflow/ANLEGEN`. Siehe A3a/A3b. Der erhoffte schnellste Gewinn entfällt; der Zeitfresser ist tatsächlich der Auftrag davor (A1) bzw. ein fehlendes Recht.
- **A4:** Freigabe-Checkliste am Auftrag — drei Zeilen Haken/Kreuz (Nachweis · Zuständigkeit · Auftraggeber), jede Lücke **inline** nachtragbar statt in drei Masken.
- **E3:** Notfall-Weg sichtbarer machen — `is_emergency` hebt zwei der drei Tore auf. **Umgesetzt** als Erklärtext in der Checkliste. Dabei ist **E4** aufgefallen: Das Flag ist nur bei der Anlage setzbar, es gibt keinen PATCH — die Checkliste sagt das ehrlich, behoben ist es nicht.

---

## Offene Entscheidungen (nur Sascha)

| # | Frage | Konsequenz |
|---|---|---|
| ~~J6~~ | ~~Soll Eigentum **oberhalb der Einheit** möglich werden?~~ **Entschieden 2026-07-21: nein.** Die anteilslose Rolle `property_party_role` = PROPERTY_OWNER reicht für „Gebäude 52 gehört Herrn X". | **Keine Schemaänderung an `ownership_period`.** AP5 reduziert sich auf das Anbinden des bestehenden Modells (J5) auf Einheitsebene; oberhalb der Einheit bleibt es bei der Rolle ohne Anteile/Quelle/Bestätigung. |
| I9 | Welche Raum-Infos fehlen konkret? Vorhanden sind Etage, Raumtyp, Fläche, Höhe, Volumen, Umfang, Innentemperatur, Luftwechsel, Heizlast-Kennwert, Steigleitungsabstand, Notiz. **Eine Raumnummer gibt es nicht.** | Ohne Antwort nicht umsetzbar. |
| I11 | „Mehr Logik bei Struktur-/Raumzuweisung" — was genau? | Ohne Antwort nicht umsetzbar. |
| I5 | Einheitsnummern pro Gebäude statt pro Liegenschaft eindeutig? | Dreht Beschluss A-09. **Zurückgestellt** — erst anfassen, wenn im Praxistest wirklich eine Kollision auftritt. |
| — | `docs/issue.md`: Aufmaß-Werkzeug soll wie „Bosch Measure" funktionieren (manuelle Eingabe reicht). Noch nicht eingeordnet. | Eigene Analyse nötig. |

---

## Querschnitt: bei jedem Paket mitziehen

1. **Audit-Trigger** für jede Tabelle, die einen neuen Schreibpfad bekommt (H7, I2). Der Schutzstandard aus `CLAUDE.md` ist bei `building`, `unit`, `party_address`, `contact_point` bis heute nie angewandt worden — mit jedem neuen PATCH wächst sonst die Nachweislücke.
2. **WCAG 2.2 AA** ist laut `CLAUDE.md` nicht verhandelbar: Status nie nur über Farbe, Kontrast ≥ 4,5:1, Tastaturbedienung, Fokuszustände.
3. **Fachschema nur als Hand-SQL** (`RunSQL`), Models `managed = False`.
4. **Migrationsköpfe:** aktuell 0123. `0120` war schon einmal dreifach vergeben — Kollisionen mit einer leeren Merge-Migration lösen, **nicht** durch Umbenennen (Django führt Migrationen über den Namen; Umbenennen desynchronisiert `django_migrations` auf der Live-Instanz).
5. **Review-Pflicht** (max. 4 Runden) und Verifikation end-to-end, nicht nur Unit-Test.
