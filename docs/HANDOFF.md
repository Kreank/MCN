# HANDOFF — MCN Leitstand (für die nächste Session)

Dieses Dokument macht eine frische Session sofort handlungsfähig: **Stand, offene
Punkte, Wegweiser.** Mehr steht hier bewusst nicht — alles Dauerhafte liegt in
eigenen Dateien (unten).

---

## 📍 Stand

| | |
|---|---|
| **Live** | `mitra.tech-artist.de`, deployt **2026-08-02** aus `main` @ `e66b36e` — verifiziert: HTTP 200, `/api/docs` 200, alle Container healthy, `bezug` im ausgelieferten OpenAPI |
| **Migrationskopf live** | **0144** (`0144_bericht_abgeschlossen`) — vollständig ausgerollt |
| **Branches** | `develop` = `main` = `origin` = `e66b36e` — alles deckungsgleich |
| **Nutzung** | **Testbetrieb** — der Server dient dem Probelauf durch Chef/Kollegen. Echte Kundendaten und ~2 Mio Artikel liegen drauf, aber es hängt kein Tagesgeschäft daran. `MCN_SEED=0`. |
| **Backup** | Dienst läuft (nächtlich 02:30 + `MCN_BACKUP_RUN_ON_START=1`); manuelle Dumps in `backups-manuell/` |
| **Testsuite** | **grün** (2026-08-02): `4475 passed, 21 skipped`, gegen frisch gebaute Test-DB |

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
3. **Sammelrechnung** — entschieden, noch nicht gebaut. Der Weg: aus mehreren
   Rechnungs**entwürfen** desselben Eigentümers eine Rechnung, je Quellentwurf eine
   Rubrik (Zwischensumme je Wohnung), die Entwürfe gehen darin auf. Kein Auftrag über
   mehrere Wohnungen — der Soll-Ist-Abgleich rechnet je Auftrag, und ein Mehrverbrauch
   in Wohnung 1 höbe sonst einen Minderverbrauch in Wohnung 3 auf.

**Als Nächstes: die Protokoll-Maske** (Sascha, 2026-08-02, beim Testen)
> „Das Zusammenklicken geht mir tatsächlich bisschen auf die Nerven. Als ich dann
> fertig war, hab ich den Entwurf gesehen. Können wir das nicht so machen, dass wenn
> ich auf den Button Protokoll klicke, genau dieses Entwurffenster auftaucht?"

Gewünscht ist: „Neues Protokoll" führt **direkt** in die Entwurfsansicht, statt erst
durch einen Formular-Dialog. Beim Öffnen die Wahl, **hinterlegte Angebote zu
übernehmen** oder leer zu starten. „Ausgeführte Arbeiten" oben als Freitext, der Rest
bleibt bzw. wird aus dem Angebot vorbelegt. **Material entfällt bei „Zeiten &
Material"** — es wird künftig im Entwurf erfasst. **Gebuchte Zeiten des Termins**
erscheinen unten automatisch als Position, und zwar als **Leistung**.
Offen beim Bauen (am fertigen Bildschirm zu entscheiden): Zeitpositionen je
Mitarbeiter einzeln oder je Lohngruppe zusammengefasst — zunächst zusammengefasst.

**Erledigt am 2026-08-02**
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
