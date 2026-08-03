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
| **Testsuite** | **grün** (2026-08-03): `4534 passed, 21 skipped` Backend + `322` Frontend, gegen frisch gebaute Test-DB |
| **Migrationskopf lokal** | **0148** (`0148_arbeitszeitfenster`) — noch **nicht** ausgerollt |

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
- **Plantafel: Auslastung rechnete die Wanduhr.** Ein Einsatz über vier Tage
  stellte den Monteur mit **185 % ausgelastet** auf die Tafel — Nächte und Pausen
  zählten als Arbeitszeit. Aus Saschas Praxisblick heraus gefunden, in zwei
  Review-Runden fanden sich **vier weitere Fehler derselben Klasse**: Wochenenden
  zählten im Zähler, aber nicht im Nenner (Do–Di = 120 %); die Pause fiel je
  *Einsatz* statt je *Arbeitstag* an (07–12 plus 12–16 = 9 h statt 8 h); die
  Nachtlücke am Fensterrand blieb stehen (derselbe Einsatz = 31 h oder 24 h, je
  nach Lage der Woche); ein ausgelaufener Vertrag leerte den Zähler ganz. Die
  ganze Regel steht jetzt in `INVARIANTEN.md` Abschnitt 11 samt Schadensbild.
  **Arbeitsbeginn/Feierabend/Pause sind Firmenprofil-Felder** (Migration 0148,
  Vorgabe 07:00–16:00 / 60 min); die Pausen*schwelle* bleibt Gesetz (§ 4 ArbZG).
  **Offen:** Der **Notdienst** braucht eine eigene Behandlung (vertagt, Sascha).
- **Plantafel-Bedienung nach Saschas HERO-Vergleich.** Steuerleiste von drei
  Bändern auf **eine Zeile** (~92 → ~40 px); der Rückstand klappt zur **Seite**
  wie die Navigation statt nach oben (Board gewinnt gut 16 rem — daran hängt, ob
  eine Woche ohne Scrollen in den Schirm passt) und bleibt eingeklappt
  Ablageziel; die Kachel-Aktionen hängen nicht mehr an einem 31 px hohen
  Hover-Streifen, sondern an einem festen **⋯-Griff** (Klick oder Rechtsklick,
  bleibt offen, 24 px nach WCAG 2.5.8). Nebengewinn: Vorher lagen je Kachel drei
  unsichtbare Tabstopps im DOM — bei 200 Kacheln 600 Stück.
- **Protokoll-Maske: der Entwurf IST die Maske.** „Neues Protokoll" legt den
  Bericht sofort an und zeigt ihn als bearbeitbares Blatt — der vorgeschaltete
  Formular-Dialog ist ersatzlos weg, „Bearbeiten" ebenfalls. Direkt danach die
  Startwahl **„aus welchem Angebot — oder leer?"**, aber nur, wenn es etwas zu
  übernehmen gibt (am freien Termin also nie). Das Feld *Material (Notiz)* ist aus
  der Maske raus (Material gehört in die Positionen); alte Notizen bleiben
  sichtbar und gehen beim Speichern nicht verloren. Im Reiter *Zeiten & Material*
  ist der Erfassungsweg für Material geschlossen — **bestehende Buchungen bleiben
  sichtbar und abrechenbar**, und der Endpunkt lebt weiter (die App bucht darüber).
  **Warum das erst jetzt ging:** Ein Klick, der sofort anlegt, setzt voraus, dass
  der Fehlklick folgenlos ist — Berichtsentwürfe sind erst seit `0145` löschbar.
  **Kein Backend-Eingriff**: `gebuchte_zeiten` (je Lohngruppe, abgeleitet) und
  `vorbelegen_aus_angebot` gab es bereits. 8 neue Frontend-Tests (322 gesamt grün).
- **Sammelrechnung gebaut und live** — „drei Bäder, alle drei Wohnungen gehören
  Herrn Meier": mehrere Rechnungs**entwürfe** werden zu **einem** Beleg, je
  Quellentwurf eine Rubrik mit dem Wohnungsbezug als Titel. Dienst
  `abrechnung.sammelrechnung`, Endpunkt `POST /invoicing/invoices/sammelrechnung`,
  Auswahl im Belegregister (Mehrfachauswahl → Bestätigungsdialog).
  **Keine Migration, kein Freigabetor angefasst**: Bindungen lösen → Entwürfe
  verwerfen (0147) → neue Rechnung an EINEM Auftrag → Quellen neu binden, alles
  in einer Transaktion. Der Beleg hängt weiter an genau einem Auftrag (B-08).
  **Dabei ein Loch geschlossen, das erst dadurch entstand:** Die
  quellenübergreifende Doppelabrechnungssperre fragte über den **Beleg**
  (`invoice.work_order_id`). Da eine Sammelrechnung an einem Auftrag hängt, aber
  die Quellen mehrerer bindet, verlören alle anderen beteiligten Aufträge ihre
  Klammer. Sie fragt jetzt über die **Herkunft der Quelle**
  (`_bindungen_des_auftrags`). 22 neue Tests, volle Suite grün (4519).

**Erledigt am 2026-08-02**
- **Entwürfe löschbar** — Bericht (`0145`) und Angebot (`0146`), beide live. Die
  pauschale Sperre `util.forbid_mutation()` wich statusabhängigen Triggern; beim
  Angebot entscheidet die **Belegnummer** (entsteht erst beim Versand), nicht der
  Status. **Rechnung bewusst NICHT** — siehe Nachtrag in `ENTSCHEIDUNGEN.md`: Der
  Löschweg hätte den Schutz auf `billing_link` gelockert und damit die
  Doppelabrechnungssperre aushebelbar gemacht.
- **Protokoll-Maske verbreitert**: neue Dialogstufe `arbeitsflaeche` (92rem),
  zweispaltig — ausgeführte Arbeiten links mit 8 Zeilen, Beiwerk rechts.
  Ausgeführte Arbeiten werden vorbelegt („Protokoll vom …"), weil `activity_text`
  in der DB nicht leer sein darf.
- **Dritter Berichtszustand `ABGESCHLOSSEN`** (`e66b36e`, live, Migration 0144):
  fertig ohne Unterschrift = **voll abrechenbar**. Hintergrund: 80 % der Berichte
  werden nie unterschrieben, die alte Regel sperrte damit den Normalfall aus.
  `ENTWURF` bleibt draußen. Dabei zwei Löcher geschlossen — Positionen eines
  abgeschlossenen Berichts waren noch änderbar (0080 prüfte nur `UNTERZEICHNET`),
  und das Ersetzen von `protect_site_report()` hätte fast den Briefkopf-Schutz aus
  0132 mitentfernt.
- **Lohngruppen angelegt** (live, über den Dienst): Meister/Techniker 85 €/h,
  Monteur 65, Helfer 45, Azubi 25. **Keine dieser Zahlen steht im Code** — Pflege
  unter *Einstellungen → Lohngruppen*, Endpunkte `/pricing/wage-groups` waren
  bereits vorhanden. `cost_rate` (Kostensatz für die Deckungsbeitrags-Auswertung)
  ist bewusst leer gelassen; den kennt nur der Betrieb.
- **Der Belegbezug steht** (`d78caf0`, live): Angebot, Rechnung und Bildschirm nennen
  Wohneinheit, Eigentümer, Mieter und „Vertreten durch". Eigentümer-Kaskade
  Wohnung → Liegenschaft → Gemeinschaft, damit WEG, Mietshaus und Eigenheim ohne
  Konfiguration bedient sind. Eingefroren in `billing_snapshot`, Live-Fallback je
  Feld. Regel und Schaden in `INVARIANTEN.md` §2; Auflöser in
  `services/belegbezug.py`. **Kein Schemaeingriff.**
  *Nächster Schritt dort:* Ein Auftrag über **mehrere** Wohnungen (drei Bäder, eine
  Rechnung) zeigt heute nur die Einheit am Auftrag. Nach der Eigentumsgrenze wäre
  das dreimal Sondereigentum — also drei Eigentümer auf einem Beleg. Mit dem User
  am fertigen Blatt klären, bevor gebaut wird.

**Erledigt am 2026-08-01** *(hier nur als Beleg, dass es nicht vergessen wurde)*
- `main`/`develop` sind auf `origin` — der Rückstand von 27 Commits ist Geschichte.
- Die volle Backend-Suite läuft wieder. Sie war seit `0114_geraetetoken` im Teardown
  kaputt: Djangos `flush` leert `public.accounts_user`, aber `security.device_token`
  hält einen Fremdschlüssel darauf und ist als `managed = False` nie Teil des
  TRUNCATE — Postgres verweigert das zu Recht. 19 Tests starben daran, ausgerechnet
  die für Mahnungs-Schreibpfad, Abrechnung unter Nebenläufigkeit und Löschschutz.
  Behoben in `backend/conftest.py` (Details siehe Kommentar dort); **kein Eingriff
  ins Fachschema**. Gegenprobe gegen `main` bestätigt: vorbestehend, keine Regression.

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
