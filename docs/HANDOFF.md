# HANDOFF — MCN Leitstand (für die nächste Session)

Dieses Dokument macht eine frische Session sofort handlungsfähig: **Stand, offene
Punkte, Wegweiser.** Mehr steht hier bewusst nicht — alles Dauerhafte liegt in
eigenen Dateien (unten).

---

## 📍 Stand

| | |
|---|---|
| **Live** | `mitra.tech-artist.de`, deployt **2026-07-31** aus `main` @ `8001dea` |
| **Migrationskopf live** | **0139** (`0139_rolle_azubi`) — im Repo steht der Kopf auf **0143** |
| **Branches** | `develop` = Arbeit (**voraus**) · `main` = was live läuft @ `90af608` |
| **⚠️ Deploy steht aus** | `develop` trägt den Slice „Material→Rechnung, Kalender/ICS, öffentliche Links" (2026-08-01 vom Dev-PC gepusht, Fast-Forward). Der Rollout zieht **fünf** Migrationen nach: `0139_material_abrechenbar`, `0140`, `0141`, `0142`, `0143_merge`. |
| **Daten** | **Echtbetrieb**, keine Demo: ~2 Mio Artikel, echte Kundendaten, `MCN_SEED=0` |
| **Backup** | Dienst läuft (nächtlich 02:30 + `MCN_BACKUP_RUN_ON_START=1`); manuelle Dumps in `backups-manuell/` |
| **Testsuite** | **grün** (2026-08-01): `4455 passed, 21 skipped`, gegen frisch gebaute Test-DB |

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
2. **Vor dem anstehenden Deploy:** `deploy/.env` braucht **nichts** nachgetragen —
   die drei neuen Schalter (`MCN_PUBLIC_LINK_MAIL`, `…_TTL_DAYS`, `…_IP_THRESHOLD`)
   haben sichere Defaults, der scharfe ist fail-closed (ohne ihn 422 statt Mailversand).
   `MCN_FRONTEND_BASE_URL` ist korrekt gesetzt. **Beachten:** `0141` ändert den
   Default-Akteur des Queue-Workers auf den Systemakteur „Online-Selbstbedienung"
   (statt „erster aktiver Account"); `MCN_AI_ACTOR_ID` ist nicht gesetzt, der neue
   Default greift also.

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
