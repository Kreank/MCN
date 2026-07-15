# KI-Tool-Vertrag — Design (ENTWURF, zur Review)

> Ergänzt `docs/ki-orchestrierung.md`. Dies ist ein **langlebiger Vertrag**: die
> Schnittstelle zwischen dem MCN-Orchestrator und heterogenen Werkzeugen
> (Wahrnehmungs-Geräte, das LLM, Fach-Lese-Services, künftige Tools). Später
> schwer zu ändern → jetzt gründlich. Offene Entscheidungen am Ende.

## 1. Prinzipien (nicht verhandelbar)

1. **Ein einheitlicher Vertrag für alle Werkzeuge.** ASR-Handy, S21-Vision,
   Server-OCR, das LLM, ein Fach-Lese-Service — dieselbe Form. Ein neues Gerät ist
   Einstöpseln, kein Umbau.
2. **Werkzeuge sind Konfiguration, nicht Code.** Endpoint, Auth, Timeouts, Retry
   liegen in Daten, nicht in `if`-Zweigen.
3. **Werkzeug-Ausgabe ist DATEN, nie Anweisung.** `is_untrusted` per Default;
   Prompt-Injection-Grenze (Foto/PDF „ignoriere alle Anweisungen").
4. **Deterministische Orchestrierung.** Der Workflow ruft Werkzeuge; das Modell
   fast nie. ≤ 3–5 Werkzeuge pro Modell-Entscheidung.
5. **Zuverlässigkeit zuerst.** Geräte schlafen/sind offline. Der Vertrag ist
   async-fähig und idempotent — **die Idempotenz garantiert die DB, nicht der
   Code** (Repo-Doktrin).
6. **Provenance & Reproduzierbarkeit.** Jeder Aufruf wird mit Ein-/Ausgabe-Hash
   protokolliert und hängt an einem `ai_run`.
7. **Sicherheit.** Gegenseitige Authentifizierung; Secrets nie in Fachdaten/Logs
   (`credential_reference` + Fernet); Datenklassen-Grenze pro Werkzeug.
8. **Evolvierbar.** Envelope und Capability-Schemas sind **versioniert**.

## 2. Werkzeug-Typen (Capabilities)

Ein Werkzeug hat genau **eine Capability** (macht den Vertrag scharf; ein Gerät mit
zwei Fähigkeiten = zwei Registry-Einträge). Erstsatz, versioniert erweiterbar:

| Capability | Eingabe → Ausgabe | Ziel im System |
|---|---|---|
| `ASR` | Audio-Referenz → Transkript (Text) | `ai.content_item` (PROTOKOLL/EINSATZBERICHT) |
| `VISION` | Bild-Referenz → Beschreibung/Analyse (Text/strukturiert) | `ai.content_item` (FOTO_BESCHREIBUNG) |
| `OCR` | Dokument-Referenz → Text | `ai.content_item` (PDF) |
| `LLM` | Nachrichten (+Schema) → Text/JSON | Reasoning/Entwurf (der bestehende `llm.py`-Adapter erfüllt diesen Vertrag) |
| `DOMAIN_QUERY` | strukturierte Anfrage → Fach-Lesedaten | RAG-Kontext (Dossiers/Suche, schon gebaut) |

Jede Capability hat ein **versioniertes JSON-Ein- und -Ausgabeschema**.

## 3. Kernentitäten (DB)

### 3.1 `ai.tool` — Registry (ein Werkzeug)

| Feld | Zweck |
|---|---|
| `id`, `tool_key` (stabil, eindeutig) | Identität; `tool_key` referenziert der Workflow |
| `label` | menschenlesbar |
| `capability` | ASR/VISION/OCR/LLM/DOMAIN_QUERY |
| `endpoint_url` | wohin (HTTP) |
| `invocation_mode` | `SYNC` (immer da: OCR, LLM) \| `ASYNC` (Geräte, die schlafen) |
| `credential_reference` | Verweis auf das Auth-Secret (nie das Secret selbst) |
| `data_boundary` | welche Datenklasse das Werkzeug empfangen darf (Default `LOCAL_ONLY`; Tor für Art. 9) |
| `timeout_seconds`, `max_attempts`, `backoff_seconds` | Zuverlässigkeitspolitik |
| `status` | `ACTIVE` \| `INACTIVE` |
| `last_seen_at`, `last_health` | Health/Liveness |
| `contract_version`, `capability_version` | Evolution |

Schutzstandard (No-Delete/Audit) wie jede Fachtabelle. Pflege über eine Management-UI
(wie `supplier_connection`).

### 3.2 `ai.tool_call` — tatsächlicher Aufruf (append-only Protokoll + Job)

Trennt sauber vom Plan: **`ai_run.tools_used` = geplante Werkzeuge (beim Start
eingefroren, unveränderlich); `ai.tool_call` = die echten Aufrufe mit Ergebnis
und Metriken** (append). Das löst die Immutabilitäts-Spannung aus dem Executor.

| Feld | Zweck |
|---|---|
| `id`, `ai_run_id`, `tool_id` | Zuordnung |
| `step_key` | logischer Schritt im Workflow (für Idempotenz) |
| `idempotency_key` | **UNIQUE** (partiell) → dieselbe Arbeit läuft nie doppelt (DB erzwingt es) |
| `status` | `QUEUED`→`RUNNING`→`SUCCEEDED`\|`FAILED`\|`EXPIRED` |
| `attempt` | Zähler für Retry |
| `request_hash`, `input_ref` | was rein ging (Hash für Reproduzierbarkeit; Ref auf `content.file` o. Ä.) |
| `output_ref` / `content_item_id`, `output_hash` | was raus kam |
| `error_code`, `error_message` | Fehler (secret-frei, klassifiziert) |
| `is_untrusted` | Ergebnis ist DATEN |
| `metrics` (jsonb) | Dauer, Tokens, Modell — fließt in `ai_run.resource_usage` |
| `deadline_at`, `created_at`, `updated_at` | Zeit/Timeout |

## 4. Wire-Protokoll (Envelope)

**Anfrage MCN → Werkzeug** (`POST endpoint_url`, `Authorization: Bearer <token>`):
```json
{
  "contract_version": "1",
  "tool_key": "asr-handy-1",
  "capability": "ASR",
  "correlation_id": "<tool_call.id>",
  "idempotency_key": "<stabil pro logischem Schritt>",
  "callback_url": "https://mcn/api/ai/tool-callback/<einmal-token>",   // nur ASYNC
  "input": { "...capability-spezifisch..." },
  "input_ref": { "kind": "file", "url": "https://mcn/api/ai/tool-input/<token>" },
  "deadline_ts": "2026-07-15T12:00:00Z"
}
```

**Antwort Werkzeug → MCN**:
```json
{
  "contract_version": "1",
  "status": "ok | error | pending",
  "job_id": "<nur bei pending>",
  "output": { "...capability-spezifisch..." },
  "output_ref": { "kind": "text", "inline": "..." },
  "is_untrusted": true,
  "error": { "code": "TIMEOUT|AUTH|BAD_INPUT|TOOL_ERROR|...", "message": "secret-frei" },
  "metrics": { "duration_ms": 1234, "model": "...", "tokens": 0 },
  "content_hash": "<sha256 der Ausgabe>"
}
```

**ASYNC-Rückkanal:** Ein `SYNC`-Aufruf an ein schlafendes/langsames Gerät darf
`status:"pending"` + `job_id` liefern; das Ergebnis kommt später per **token-
gesichertem Callback** an `POST /api/ai/tool-callback/<token>` (`auth=None`, Token
nur als SHA-256-Hash, Einmal-Einlösung via `select_for_update`) — **exakt das
Muster des IDS-Punchout-Returns**, das schon steht und bewährt ist.

## 5. Datenübergabe (push vs. pull)

Große Eingaben (Audio, Bild) nicht in den JSON-Envelope inlinen. Vorschlag:
**tokened Pull-URL, durch MCN proxied** (`GET /api/ai/tool-input/<token>`,
Einmal-/kurzlebig) — das Gerät holt die Bytes bei MCN, **MinIO bleibt unexponiert**,
und der Zugriff ist prüfbar (kein Presigned-URL-Bypass, wie bei `dateien.py`).
Kleine Eingaben (LLM-Nachrichten) inline.

## 6. Auth & Vertrauensgrenze

- **MCN → Werkzeug:** Bearer-Token je Werkzeug (`credential_reference` → Fernet at
  rest, `MCN_MAIL_KEY`-Muster). Geräte nehmen **nur authentifizierte** Aufrufe an.
- **Werkzeug → MCN (Callback/Pull):** token-gesicherte Einmal-URLs (SHA-256-Hash,
  select_for_update), wie Punchout.
- **Ausgabe `is_untrusted`:** Der Prompt trennt System-Instruktion strikt vom
  Werkzeug-Ergebnis. Perception ist der Injection-Vektor.
- **`data_boundary`:** heute alle `LOCAL_ONLY`; das Feld ist die vorgezogene Bremse,
  falls je ein Werkzeug außer Haus ginge (Art. 9 dürfte es nie sehen).

## 7. Zuverlässigkeit (der schlafende-Handys-Kern)

- **`invocation_mode`** unterscheidet immer-da (SYNC) von schläft-manchmal (ASYNC).
- **Idempotenz** über `idempotency_key` + partiellen UNIQUE-Index — ein Retry
  verdoppelt nie die Arbeit. Garantiert die DB.
- **Retry** mit `max_attempts` + Backoff; **transiente** Fehler (UNREACHABLE,
  TIMEOUT) werden wiederholt, **permanente** (AUTH, BAD_INPUT) brechen den Schritt.
- **Health:** `last_seen_at`/`last_health`; der Router prüft Erreichbarkeit vor dem
  Dispatch und degradiert sauber (einreihen statt hart scheitern).
- **Kein Celery.** Die `ai.tool_call`-Queue drainiert ein Management-Command auf dem
  bestehenden Scheduler-Loop (`deploy/scheduler-entrypoint.sh`), idempotent per
  DB-Index — dasselbe Muster wie `wartung_faellige_ausloesen`.

## 8. Fehler-Taxonomie

`UNREACHABLE`, `TIMEOUT` (transient → Retry) · `AUTH`, `BAD_INPUT`,
`UNSUPPORTED_CAPABILITY`, `CONTRACT_VERSION` (permanent → Schritt scheitert) ·
`TOOL_ERROR` (werkzeugseitig; transient/permanent je Werkzeug) ·
`UNTRUSTED_REJECTED` (Ausgabe verletzte eine Sicherheitsregel). Deterministisch,
damit die Retry-Logik nicht rät.

## 9. Evolution

- `contract_version` (Envelope) + `capability_version` (Ein-/Ausgabeschema) +
  `tool.contract_version`. Regel: additiv erweitern; Pflichtfeld-Änderung = neue
  Version, alte bleibt bedienbar, bis alle Werkzeuge nachziehen.

## 10. Offene Entscheidungen (User)

1. **Registry als DB-Tabelle** (Management-UI, wie `supplier_connection`) **oder**
   env/settings-Config (wie die LLM-Profile)? → Empfehlung: **DB-Tabelle** (Geräte
   sind Betreiber-Daten mit Health/Credentials, gehören nicht in Code/Env).
2. **Datenübergabe:** tokened Pull-URL (proxied) **oder** Bytes pushen? → Empfehlung
   **Pull-URL** für Medien.
3. **Async-Rückkanal:** Callback-Hook (wie Punchout) **oder** MCN pollt das Gerät?
   → Empfehlung **Callback**, mit Poll als Fallback.
4. **Capability-Schemas** in der DB (zur Laufzeit pflegbar) **oder** im Code
   (versioniert, testbar)? → Empfehlung **Code** (testbar, mitversioniert).
5. **`data_boundary` jetzt schon echt** (Klassifikation) **oder** nur als Flag-
   Platzhalter, bis es ein nicht-lokales Werkzeug gibt?

## 11. Was wir bewusst NICHT im ersten Vertrag bauen (aber vorsehen)

- Streaming-Ausgaben (LLM-Token-Stream) — Feld im Envelope offenlassen.
- Werkzeug-Ketten/Kompositionen — macht der Workflow, nicht der Vertrag.
- Kosten-/Kontingent-Steuerung pro Werkzeug — `metrics` sammelt schon die Daten.

---

# Revision 2 — Härtung nach Drei-Kritiker-Review (2026-07-15)

Drei unabhängige Opus-Reviews (Zuverlässigkeit, Sicherheit, Langlebigkeit). Die
DB-Tore aus `0027` wurden als solide und nicht aufweichbar bestätigt. Die Lücken
lagen eine Ebene tiefer — Runtime, Vertrauensgrenze, Audit. Alles Folgende ist
**Formentscheidung, gehört in die erste Migration**, nicht in einen späteren Slice.

## Die strukturelle Kernänderung: eine neue Entität `ai.workflow_run`

`0027`s `ai_run` ist **LLM-zentrisch und laufkurz** (ein Modell, eine Prompt-Version,
finish-once, kein WAITING). Ein v1-Workflow benutzt aber **mehrere Modelle** (ASR,
Embedder, LLM) und **wartet asynchron** (schlafendes Handy, Stunden/Tage). Deshalb:

- **`ai.workflow_run`** = durabler, wiederaufnehmbarer Zustandsträger:
  `QUEUED/RUNNING/WAITING/DONE/FAILED/CANCELLED`, resume-fähig.
- `ai_run` bleibt, was es ist: die Protokollzeile **eines einzelnen Modell-Aufrufs**.
- `tool_call` **und** jeder `ai_run` hängen am `workflow_run`.
- **Idempotenz keyt auf `workflow_run` + `step_key`, NIE auf `ai_run`** — sonst
  dedupliziert der Retry nach einem Resume nicht mehr (der alte `ai_run` ist
  finish-once).

## Zuverlässigkeit (Job-Queue-Standardteile — fehlten komplett)

- **Zweiter, schneller Scheduler-Tick** (15–30 s) für die `tool_call`-Queue, getrennt
  vom täglichen Fälligkeitslauf. (Ohne: Sprachmemo um 9:00 → ASR-Dispatch erst 3 Uhr
  nachts.) Änderung an `deploy/scheduler-entrypoint.sh`.
- **Claiming per `SELECT … FOR UPDATE SKIP LOCKED` + Lease** (`leased_until`). Der
  UNIQUE-Index verhindert nur Doppel-INSERT, nicht Doppel-Dispatch zweier Ticks.
- **Stale-Reaper:** `RUNNING AND leased_until < now()` → `EXPIRED`/Retry. Sonst hängt
  ein Call nach Scheduler-Crash/Deploy für immer, der Lauf bleibt offen, keine Meldung.
- **Terminalzustand-Trigger auf `ai.tool_call`** (analog `guard_ai_run_update`): nur
  `QUEUED→RUNNING→terminal` und `→QUEUED` (Retry). Verhindert, dass eine späte Antwort
  nach Timeout einen toten Call „wiederbelebt".
- **Zustände vollständig:** + `CANCELLED` (Lauf abgebrochen → in-flight Calls stoppen).
- **`idempotency_key` = `sha256(workflow_run_id || step_key)`**; bewusster Zweitaufruf
  = **neuer `step_key`**, nicht derselbe. Partial-Prädikat des UNIQUE explizit
  dokumentieren.
- **Deadlines sind serverautoritativ** (`deadline_ts` fürs Gerät nur informativ);
  MCN entscheidet Timeout, nicht die Geräteuhr.
- **Backpressure:** Circuit-Breaker bei `last_health` rot (schnell fehlschlagen statt
  enqueuen); Backoff **mit Jitter**; Dead-Letter nach `max_attempts` → `workflow_run`
  = FEHLER + sichtbare Fehl-Kachel.
- **`content.file_link`-Analogon:** `content_item.source_tool_call_id` **UNIQUE** →
  ein doppelter Callback erzeugt kein zweites Transkript.
- **Betriebssicht:** View „offener Lauf, Call terminal, kein Fortschritt" +
  Queue-Kennzahlen (Tiefe/Alter).

## Sicherheit / Vertrauensgrenze

- **Kein PII-Klartext ins unlöschbare `tool_call`/`ai_run`/`ai_proposal`.** Transkript/
  Beschreibung/Mail-Text leben NUR im **löschbaren** `content_item`/`file`; das
  Audit hält **Hash + Verweis**. `tool_call.error_message` = MCN-eigener klassifizierter
  Code, nie die Device-Freitextmeldung. (DSGVO Art. 17 vs. No-Delete.)
- **`is_untrusted` leitet der SERVER aus der Capability ab** (ASR/VISION/OCR/MAIL =
  immer untrusted). Das Feld im Antwort-Envelope wird ignoriert — ein Gerät darf seine
  eigene Vertrauensstufe nicht behaupten.
- **Feld-Provenienz im Vorschlag:** markieren, welche Payload-Felder aus untrusted
  Quellen stammen; die Freigabe-Kachel hebt das sichtbar hervor. Einzige echte Bremse
  gegen **Content-Poisoning** (Injektion kann nicht schreiben, aber den Vorschlags-
  *Inhalt* vergiften → Mensch winkt plausiblen Entwurf durch).
- **Callback ist geräte-gebunden (Mutual Auth):** Token bindet an den `tool_call`,
  **zusätzlich** das gerätespezifische Bearer bindet an die Identität; MCN prüft, dass
  die antwortende `tool_id`/Capability die dispatchte ist (ein VISION-Tool beantwortet
  keinen ASR-Call).
- **TLS auch im LAN verbindlich** (Prinzip). Sonst sind alle Token/Bearer abhörbar.
- **Token im Header, nicht im Pfad** (sonst in nginx-Logs). Token an
  (`tool_call`, `attempt`) gebunden und **gültig bis `deadline_at`** statt einmalig —
  das Punchout-Einmal-Muster passt beim Retry NICHT.
- **Pull-URL** an (`tool_call`, `input_ref`, `tool_id`) gebunden, gültig bis Deadline
  (kein IDOR, kein Einmal-Bruch beim Retry).
- **Bearer eindeutig pro Gerät** + Rotations-/Revoke-Pfad benennen (Blast-Radius eines
  kompromittierten Alt-Handys eindämmen). **Eigener Schlüssel `MCN_CRED_KEY`**, NICHT
  `MCN_MAIL_KEY` überladen (Kopplung/Namens-Schuld — dieselbe Lektion wie beim IDS-Fix).
- **`metrics` auf Whitelist-Keys** (duration_ms, tokens, model) + Typ/Größe validieren
  (Device-Input ist untrusted).
- **Downgrade-Schutz:** Antwort mit `contract_version` unter dem registrierten Minimum
  ablehnen.
- **Fehlerhygiene des `llm.py` als Prinzip auf den neuen Tool-Client übertragen** (nie
  URL/Bearer in eine Exception/ein Request-Log).

## Langlebigkeit / Audit / Reproduzierbarkeit

- **Reproduzierbarkeit = Einfrieren, nicht Hashen** (Lektion `site_report.py`: „Kopie,
  nicht Verweis"). Der Lauf speichert den **eingefrorenen Snapshot** des tatsächlich
  verwendeten Prompt-Textes, des montierten RAG-Kontexts und der effektiven
  Modellparameter — nicht nur `prompt_version` + eine ID auf ein löschbares
  `content_item`. (Spannung zu Art. 17 → siehe offene Entscheidung.)
- **`capability_version`/`contract_version` auf `tool_call` einfrieren** (bei Dispatch);
  alte Schema-Versionen im Code für immer behalten (self-describing).
- **`data_class` an `content_item`** + Durchsetzungspunkt im Dispatcher (Versand
  verweigern, wenn Datenklasse > Tool-`data_boundary`). Einwertig (`LOCAL_ONLY`) genügt;
  die Spalte ist der schwer nachrüstbare Teil, nicht die Werteskala.
- **`cost_units`/`currency` auf `tool_call`** jetzt vorsehen (leer bis gebraucht).
- **Routing:** entweder bewusst in den deterministischen Router (Code) — Empfehlung —
  oder minimale Felder (`priority`/`is_default`/`capability_group`) auf `ai.tool`.
- Streaming braucht einen **anderen Transport** (SSE/WS) — nicht so tun, als decke ein
  Envelope-Feld es ab. `language`/Locale auf Envelope/Proposal (billig).

## Offene Entscheidungen (jetzt, weil form-tief)

- **E-A Mandantenfähigkeit:** single-tenant per Design (dokumentiert) ODER `tenant_id`
  sofort in alle KI-Tabellen + UNIQUE-Index. Die Annahme steht nirgends.
- **E-B DSGVO-Aufbewahrung vs. Reproduzierbarkeit:** Wie mit personenbezogenem Rohtext
  in unveränderlichen Vorschlägen/Läufen umgehen (Löschanspruch vs. GoBD/Nachweis)?
- **E-C `data_class` + Dispatcher-Tor jetzt einbauen** (einwertig) oder vertagen?
- **E-D Geräte-Betriebsart:** ASR-Handy HTTP-raus-fähig (Callback) oder passiv/Poll?
  Entscheidet Callback- vs. Poll-Modell.

---

# Revision 3 — Entscheidungen gefallen (2026-07-15)

- **E-A → Single-Tenant.** Kein `tenant_id`. MCN ist und bleibt eine Firma
  (eine `CompanyProfile`), konsistent mit dem ganzen übrigen Schema. Echte
  Mandantenfähigkeit wäre ein systemweites Projekt — nicht Teil hiervon.
- **E-B → Rohtext nur löschbar, Audit nur Hash/Verweis.** Personenbezogener
  Rohtext (Transkript/Bildbeschreibung/Mail) lebt ausschließlich im **löschbaren**
  `content_item`/`file`. `ai_run`/`ai_proposal`/`tool_call` halten **Hash +
  Verweis**, nie das Zitat. Wird ein Vorschlag zum Beleg, erbt er die
  GoBD-Aufbewahrung; ein abgelehnter/abgelaufener bleibt löschbar. Reproduzier-
  barkeit weicht bewusst dem Löschanspruch (Art. 17).
- **E-C → `data_class` jetzt.** Spalte an `content_item` + Dispatcher-Tor
  (Versand verweigern, wenn Datenklasse > Tool-`data_boundary`). Einwertig
  `LOCAL_ONLY`; die Werteskala wächst später, die Spalte/das Tor stehen jetzt.
- **E-D → Gerät ist PASSIV; MCN pollt.** Große Konsequenz für Transport &
  Sicherheit:

## Poll-Modell (MCN initiiert ALLE Verbindungen)

Weil das Gerät kein HTTP nach außen kann, entfällt der ganze inbound-Pfad:
- **Dispatch:** MCN POSTet den Auftrag **inklusive der Eingabe-Bytes** ans Gerät
  (kein Pull, weil das Gerät nicht zu MCN ziehen kann).
- **Ergebnis:** entweder **synchron** in der Dispatch-Antwort (`status:ok`, kurze
  Aufgaben) **oder** `status:pending` + `job_id`, und **MCN pollt** die
  Status-/Ergebnis-Route des Geräts (mit dem Geräte-Bearer).
- **Schlafendes Gerät:** Die `tool_call`-Queue wiederholt den **Dispatch**, bis das
  Gerät erreichbar ist; danach **Poll** bis fertig. Der schnelle Tick treibt
  Dispatch UND Poll.

**Dadurch entfallen ersatzlos** (und mit ihnen ihre Sicherheitssorgen aus Rev 2):
der `auth=None`-Callback-Hook, die tokened Pull-URL, alle **inbound** Einmal-Token
(H2/H5), Token-im-Pfad-in-Logs (M1). Die Vertrauensgrenze schrumpft auf **eine
Richtung**: MCN → Gerät, per **Geräte-Bearer + TLS** (Bearer eindeutig pro Gerät,
Fernet at rest unter **`MCN_CRED_KEY`**, nie `MCN_MAIL_KEY`). Das Gerät
authentifiziert MCN; MCN authentifiziert die Geräte-`tool_id`/Capability der
Antwort. Das Gerät **initiiert nie** — strukturell dichter.

## Was aus Rev 2 unverändert bleibt (Runtime & Audit)

`ai.workflow_run` (durabel, resume-fähig, WAITING) als Anker · Idempotenz auf
`workflow_run + step_key` · schneller Scheduler-Tick · `FOR UPDATE SKIP LOCKED` +
Lease + Stale-Reaper · Terminalzustand-Trigger auf `tool_call` · serverautoritative
Deadlines · Circuit-Breaker + Backoff-Jitter · `content_item.source_tool_call_id`
UNIQUE · `is_untrusted` server-abgeleitet · Feld-Provenienz im Vorschlag ·
Reproduzierbarkeits-Snapshot (eingefrorener Prompt/RAG-Kontext/Parameter, Kopie
nicht Verweis) · `capability_version` am `tool_call` eingefroren · `cost_units`-
Platzhalter · Downgrade-Schutz · `llm.py`-Fehlerhygiene auf den Tool-Client.

## Finale Entitäten (Migration, eine)

1. `ai.workflow_run` — durabler Zustandsträger (state, step-cursor, resume).
2. `ai.tool` — Registry (Endpoint, `capability`, `invocation_mode`, `data_boundary`,
   `credential_reference`→`MCN_CRED_KEY`, Timeouts/Retry, Health, `capability_version`).
3. `ai.tool_call` — echter Aufruf (State-Machine + Trigger, `leased_until`, Idempotenz,
   Hashes/Refs statt Klartext, eingefrorene Version, `cost_units`).
4. Ergänzung an `ai.content_item`: `data_class`, `source_tool_call_id` (UNIQUE).

`ai_run`/`ai_proposal`/`embedding` aus 0027 bleiben unverändert; `ai_run` hängt
künftig an `workflow_run`.
