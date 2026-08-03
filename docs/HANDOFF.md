# HANDOFF — MCN Leitstand (für die nächste Session)

Dieses Dokument macht eine frische Session sofort handlungsfähig: **Stand, offene
Punkte, Wegweiser.** Mehr steht hier bewusst nicht — alles Dauerhafte liegt in
eigenen Dateien (unten).

---

## 📍 Stand

| | |
|---|---|
| **Live** | `mitra.tech-artist.de`, deployt **2026-08-02** aus `main` @ `712f93c` — verifiziert: HTTP 200, `/api/docs` 200, alle Container healthy, `bezug` im ausgelieferten OpenAPI |
| **Migrationskopf live** | **0146** (`0146_angebotsentwurf_loeschbar`) — vollständig ausgerollt |
| **Branches** | `develop` = `main` = `origin` = `b57985c` — alles deckungsgleich |
| **Nutzung** | **Testbetrieb** — der Server dient dem Probelauf durch Chef/Kollegen. Echte Kundendaten und ~2 Mio Artikel liegen drauf, aber es hängt kein Tagesgeschäft daran. `MCN_SEED=0`. |
| **Backup** | Dienst läuft (nächtlich 02:30 + `MCN_BACKUP_RUN_ON_START=1`); manuelle Dumps in `backups-manuell/` |
| **Testsuite** | **grün** (2026-08-03): `4571 passed, 21 skipped` Backend + `333` Frontend, gegen frisch gebaute Test-DB |
| **Migrationskopf lokal** | **0149** (`0149_automatische_nummernvergabe`) — noch **nicht** ausgerollt |

> **⚠️ Zwei volle Suiten gleichzeitig zerstören sich gegenseitig.** Beide bauen
> `test_mitra_crm_test` mit `--create-db` neu — der eine Lauf zieht dem anderen die
> Datenbank weg. Ergebnis: „32 failed, 3776 errors", die nichts mit dem Code zu tun
> haben. Immer nur **eine** Suite laufen lassen (`pgrep -f bin/pytest` vorher).

> **⚠️ Demo-Passwort-Automatik ist ABGESCHALTET** (2026-08-01). Bis dahin setzte der
> Entrypoint bei **jedem** Containerstart für **alle** aktiven Konten dasselbe
> Passwort aus `MCN_DEMO_PASSWORD` — ohne `--nur-ohne-passwort`, also wurden
> individuell vergebene Passwörter überschrieben (belegt: „6 gesetzt, 0
> übersprungen"). Der Befehl sichert sich mit zwei Schaltern ab, aber auf diesem
> Server waren **beide** gesetzt. Jetzt: `MCN_DEMO_INSTANZ=0`, `MCN_DEMO_PASSWORD`
> auskommentiert; Sicherungskopie `deploy/.env.bak-vor-demo-abschaltung-2026-08-01-1926`.
>
> **Folge für einen Neu-Seed:** Frisch geseedete Konten bekommen ohne diesen Schritt
> ein *unbenutzbares* Passwort — niemand käme hinein. Wer neu seedet, setzt die zwei
> Zeilen kurz zurück, startet einmal, und schaltet sie wieder ab.

> **Zwei parallele `0139`.** Der AZUBI-Strang (über `main` ausgeliefert) und der
> Material-Strang sind unabhängig auf `0138` aufgesetzt. `0143_merge_azubi_und_material`
> führt sie zusammen; `0139_rolle_azubi` behält bewusst ihren Namen, weil Django
> angewendete Migrationen über den **Namen** führt — nach einer Umbenennung liefe
> sie auf dem Server ein zweites Mal. Graph verifiziert: ein Blatt, kein Zyklus.

**Deploy-Ablauf und die zwei scharfen `.env`-Schalter stehen in `CLAUDE.md`**
(Abschnitt „Betrieb, Branches & Deployment"). Kurzform: DB sichern → `develop`→`main`
→ aus losgelöstem Worktree bauen → `up -d --no-build`. **Nie `up --build`.**

---

## 🧭 Wo was steht

| Datei | Inhalt | Wann lesen |
|---|---|---|
| **`CLAUDE.md`** | Regeln: Vision, Architektur, Betrieb/Deploy, Review-Pflicht, Autonomie | immer zuerst |
| **`docs/INVARIANTEN.md`** | Fachliche Regeln, die man **nicht versehentlich vereinfachen** darf | vor jeder Änderung an Abrechnung, Berichten, Preisen, Rechten, Planung |
| **`docs/ENTWICKLUNG.md`** | Umgebung, Dev-DB, Konventionen, Frontend-Muster, Slice-Rezept | beim Loslegen |
| **`docs/ENTSCHEIDUNGEN.md`** | Fixierte Festlegungen samt Begründung (inkl. Deployment/Backup, RAG) | wenn du etwas „besser machen" willst |
| **`docs/BACKLOG.md`** | Priorisierte nächste Bereiche + Gotchas | bei der Frage „was als Nächstes" |
| **`docs/roadmap/`** | Informationsarchitektur, Fachkonzept | für den fachlichen Rahmen |
| **`docs/archiv/chronik-2026-07.md`** | Session- und Wellenberichte bis 2026-07-22 | nur zum Nachschlagen, **nicht** als Stand |
| **`docs/archiv/chronik-2026-08.md`** | Erledigtes aus Anfang August 2026 | dito |

> **Warnung zum Archiv:** Die Chronik widerspricht sich in Teilen selbst (mehrere
> Migrationsköpfe, „Backup nicht gebaut" neben „Backup gebaut", erledigte TODOs als
> offen). Sie ist als Beleg dafür wertvoll, *warum* etwas so gebaut wurde — nicht
> dafür, *was gerade gilt*. Im Zweifel: `git log`, `ls backend/db_core/migrations/`,
> `docker ps`.

---

## 🔴 Offene Punkte

**Betrieb / Ops**
1. **`MCN_BACKUP_DIR` liegt auf derselben Platte.** Für GoBD-Ernstfall: zweites Ziel
   off-box (rsync/S3) + **Restore-Probelauf** auf Wegwerf-Server + MinIO-Versioning.
   Ein Backup, das nie zurückgespielt wurde, ist eine Hoffnung.
2. **Die sechs Login-Passwörter sind alle gleich** — Nachwehe der abgeschalteten
   Automatik (siehe Kasten oben). Sie sind gültig und niemand ist ausgesperrt, aber
   jeder sollte sich einmal anmelden und im Produkt sein eigenes setzen; ab jetzt
   bleibt es erhalten. Der Reset-per-Mail-Weg funktioniert dafür **nicht**, solange
   `MCN_EMAIL_BACKEND` auf Konsole steht.

**Als Nächstes: offen — der Backlog entscheidet.** Die zuletzt benannten Punkte
(Sammelrechnung, Protokoll-Maske) sind erledigt; siehe `docs/BACKLOG.md` für die
nächsten Bereiche.

**Erledigt am 2026-08-03**
- **Nummern vergibt jetzt die Datenbank** (Migration 0149). Vier Kreise waren
  Handarbeit geblieben: Artikel-, Leistungs-, Gebäude- und Einheitsnummer. Bei
  3–5 gleichzeitigen Erfassern hieß das: Der Zweite bekommt einen UNIQUE-Verstoß
  ins Gesicht und tippt neu, mitten im Angebot. **Leer lassen genügt** → `ART-`/
  `LEI-#####` bzw. je Liegenschaft `1,2,3` / `01,02,03`; eine eingetragene Nummer
  bleibt unangetastet (jeder Import gibt seine eigene vor). Gezogen wird im
  **BEFORE-INSERT-Trigger**, nicht beim Öffnen der Maske — ein vorbelegter
  Vorschlag hätte das Rennen nur verschoben. Nebenläufigkeit mit zwei echten
  Sitzungen nachgewiesen (die zweite wartete 3,2 s auf die Sperre).
  **Nicht automatisiert:** Sachkonto und Kostenstelle (Kontenrahmen SKR03/04) —
  da darf keine Software raten.
- **Leistungen sind fertig bedienbar.** Vorher: Kopf anlegen, Positionen nur
  *anhängen*, Stammdaten read-only, kein Preis. Jetzt: Stammdaten bearbeiten,
  Aktiv/Inaktiv, Positionen ändern/entfernen/umsortieren (`PUT .../components`
  ersetzt die ganze Liste — bei umsortierten Nummern ist ein Teil-Update nicht
  eindeutig), und nach dem Anlegen geht es direkt in die Leistung statt zurück
  in die Liste. Der Hinweis „folgt, sobald der Positions-Editor bereitsteht" war
  seit Längerem schlicht falsch und ist weg.
- **Die Leistung rechnet sich selbst** (`GET /assemblies/{id}/kalkulation`).
  Material läuft über **denselben** VK-Vorschlag wie ein einzeln ins Angebot
  gezogener Artikel — sonst hätte dieselbe Ware zwei Preise. Der Angebots-Editor
  setzt den Preis jetzt ein, statt „Bitte Einzelpreis ergänzen" zu sagen.
  **Zwei Ehrlichkeits-Flaggen:** `vollstaendig=false` (einem Material fehlt der
  VK → die Teilsumme zieht NICHT als Preis in den Editor) und
  `kosten_vollstaendig=false` (ein EK oder Lohn-Kostensatz fehlt → **keine
  Marge**, sie wäre zu hoch). **Offen geblieben:** der § 35a-Anteil — die
  Kalkulation kennt ihn je Einheit, aber `labour_net_amount` gilt für die ganze
  Position und skaliert nicht mit der Menge; automatisch gesetzt wäre er ab der
  zweiten Einheit falsch.
- **Artikelmaske vervollständigt:** `gtin` fehlte beim **Anlegen** (nur nachträglich
  setzbar) — jetzt in Maske, Schema und Service. Die GTIN-Prüfung sitzt in einer
  Stelle (`_gtin`) statt zweimal.

*Was davor an diesem Tag fertig wurde (Plantafel-Auslastung, Plantafel-Bedienung,
Protokoll-Maske, Sammelrechnung) steht in `docs/archiv/chronik-2026-08.md`.*

**Fachlich — bewusst vertagt (Zusage an den User, 2026-07-20)**
4. **`building.address_id` ist über die API nicht befüllbar.** Spalte und
   Anzeige-Fallback existieren, aber `BuildingIn` kennt kein Adressfeld und
   `add_building` übergibt kein `address_id`. **Folge:** Die WEG-über-mehrere-Adressen-
   Struktur lässt sich im UI gar nicht erfassen; die `GEBAEUDE`-Trefferart der
   Dublettenprüfung greift nur bei per SQL entstandenen Datensätzen.
5. **`quick_intake` kennt keine Mieter.** Der Melder wird bei neuer Liegenschaft immer
   als `PROPERTY_OWNER` eingetragen (`backend/api/projekt.py`), `tenure.occupancy` wird
   nie angefasst. Zieht nach sich: `tenure.ownership_period`/`ownership_interest` haben
   **kein ORM-Modell und keinen Endpoint** (Eigentums-Ansicht ist ein Platzhalter).

**Aus dem Aufgaben-Slice 2026-07-31 — bewusst offen gelassen**
5a. **Das Postfach trägt heute nur Aufgaben.** `notify.notification` ist
   bereichsübergreifend gebaut (weiches Ziel `target_type`/`target_id`), gefüttert
   wird sie aber allein vom Aufgaben-Service. Naheliegende nächste Absender:
   Termin zugewiesen, Vier-Augen-Antrag wartet, KI-Vorschlag liegt vor. Jede neue
   Art kostet eine Migration (`kind` ist ein geschlossenes CHECK) — Absicht, siehe
   `ENTSCHEIDUNGEN.md`.
5c. **Keine Mail-Zusammenfassung.** Die Glocke ist In-System. Ein Tagesauszug per
   Mail hinge am Scheduler (`deploy/scheduler-entrypoint.sh`, läuft nächtlich) —
   und daran, dass `MCN_EMAIL_BACKEND` je scharfgeschaltet wird. Solange es auf
   Konsole steht, wäre er wirkungslos.

**Aus dem Praxistest 2026-07-28 — entschieden, aber nicht gebaut**
5b. **Reiter der Liegenschaftsmappe zusammenlegen (11 → 6).** Recherche mit
   Mapping, Aufwand und Reihenfolge: `docs/roadmap/liegenschaft-reiter-verschlankung.md`.
   Der erste Schritt (Gebäudeansicht statt Reiterwechsel) ist gebaut; die
   Zusammenlegung selbst braucht eine Entscheidung des Users.

**KI-Ausbau**
6. **Live-Durchklick der KI-Strecken steht aus** (Vorschläge, Assistent) — braucht
   laufenden Stack **und** gesetztes `MCN_AI_PROFILES`, sonst greift der Fallback.
7. **Kein echtes ASR-Gerät angebunden** (`manage.py ki_tool register` + `MCN_CRED_KEY`),
   damit ist der Pfad Sprachmemo→Bericht nicht end-to-end nachgewiesen.
8. Optional: EK für Vaillant-/Bosch-Ersatzteile via passender RAB nachziehen.

---

Viel Erfolg. Halte dich an das Slice-Rezept (`docs/ENTWICKLUNG.md`), verifiziere
end-to-end statt nur per Typecheck, und lass jeden substanziellen Slice von einem
Opus-Reviewer prüfen.
