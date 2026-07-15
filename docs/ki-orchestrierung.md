# KI-Orchestrierung — Architektur-Skizze

> Ergänzt `docs/ki-first-konzept.html` (das *Was*) um das *Wie*. Entscheidungen aus
> dem Design-Gespräch 2026-07-15; Slice-Umsetzung folgt. Schema-Grundlage:
> `db/migrations/0027_ki_grundlagen.sql` (`ai.content_item`, `ai.embedding`,
> `ai.ai_run`, `ai.ai_proposal`).

## Leitsatz

Die KI ist der primäre Akteur, aber sie **schreibt nie direkt**. Sie erzeugt einen
`ai.ai_proposal` ohne fachliche Wirkung; ausgeführt wird der ausschließlich von der
App-Schicht über die Fach-API — durch dieselben Statusautomaten, Freigaben und
DB-Trigger wie beim Menschen. Die DB-Tore (Payload-Hash, Zielversion, Ablauf,
freigebender Benutzer) sind physisch, es gibt keinen KI-Sonderweg.

## Betriebsrahmen (bewusste Entscheidungen)

- **Lokal-only.** Kein Cloud-LLM für Kundendaten (DSGVO, Art. 9). Alles läuft auf
  eigener Hardware.
- **Modellwahl bleibt offen.** Der LLM-Zugriff läuft über einen **modell-agnostischen
  Adapter** (ein Port, austauschbare Backends: llama.cpp / Ollama / OpenAI-kompatibel).
  Modell, Endpoint, Parameter sind **Konfiguration**. Ziel: Modelle im Betrieb
  vergleichen und tauschen können. `ai_run.model_name`/`model_version` protokollieren
  pro Lauf — die Grundlage des A/B-Vergleichs.
- **Hardware heute:** Server mit Ryzen 9 3900X, 32 GB RAM, RTX 3070 (8 GB). Plan:
  größtes Modell auf dem Server (~12B mit Offload, llama.cpp). Klein — deshalb trägt
  die Architektur die Last, nicht das Modell.

## Die Schichten

```
Wahrnehmung (verteilte Tools)     Denken (Server-LLM)     Abruf              Tore (DB)
─────────────────────────────     ───────────────────     ─────              ─────────
ASR-Handy   ─┐                                             bge-m3 (CPU)
S21-Vision  ─┼─▶ ai.content_item ─▶  LLM (agnostisch) ◀─ RAG ─ Dossiers/Suche ─▶ ai_proposal
Server-OCR  ─┘   (is_untrusted)       nur Text!                (gebaut)          (kein Schreibrecht)
E-Mail      ─┘
```

`source_type`-Mapping (schon im Schema vorgesehen): ASR → `PROTOKOLL`/`EINSATZBERICHT`,
Vision → `FOTO_BESCHREIBUNG`, OCR → `PDF`, Mail → `EMAIL`.

## Harte Entwurfsregeln (nicht „vereinfachen")

1. **Das Modell sieht nie viele Tools.** Eine Fähigkeit in der Registry ≠ ein Tool, das
   das Modell sieht. **Pro Modell-Entscheidung ≤ 3–5 Tools.** Braucht ein Workflow mehr,
   wird er geteilt. (Aus Erfahrung: ein flacher 30-Tool-Agent mit Ranking scheitert lokal.)
2. **Deterministischer Router als äußere Schleife.** Der Trigger (Regel) oder eine schmale
   Klassifikation (Ausgabe: ein Label, constrained) wählt den **Workflow** → `ai_run.
   workflow_name/version`. Kein offener ReAct-Loop. Werkzeugwahl ist Etikettieren, nicht
   Agieren.
3. **Lesen ist RAG, keine Tools.** Der Workflow montiert den Kontext deterministisch vorweg
   (Dossiers + globale Suche, beide gebaut). Das Modell ruft nichts ab — der Kontakt/Beleg
   liegt schon im Prompt. Killt die halbe Registry.
4. **In der Regel callt das Modell fast nie Tools** — die Pipeline callt, das Modell
   denkt/entwirft. Echte Werkzeugwahl erst bei der konversationellen Auskunft (Slice 5),
   auch dort gestaffelt mit ≤ 3 Tools pro Sub-Assistent.
5. **Constrained Decoding** (JSON-Schema / GBNF-Grammatik) an jedem Modell-Ausgang — kein
   kaputtes Tool-Call-/Vorschlags-JSON möglich.
6. **Wahrnehmungs-Ausgabe ist `is_untrusted` (Default true) — Daten, nie Anweisung.**
   Vision/OCR/Mail sind der Prompt-Injection-Vektor (Foto/PDF mit „ignoriere Anweisungen").
   Der Prompt trennt System-Instruktion strikt von untrusted Inhalt.

## Tool-Vertrag (die Geräteflotte)

Jedes Tool — Handy (ASR), S21 (Vision), Server-OCR, das LLM selbst — bekommt **dieselbe
Form**: HTTP-Endpoint, Eingabe-/Ausgabe-Schema, Timeout, Health-Check, **Token-Auth**.
Geräte nehmen nur authentifizierte Aufrufe an (keine offene ASR/Vision-Schnittstelle im
LAN). Ist der Vertrag einheitlich, ist ein neues Gerät Einstöpseln, kein Umbau.

## Asynchrone, wiederaufnehmbare Workflows

Handys schlafen/sind offline. Ein Workflow-Schritt „frag das S21" darf nicht synchron
blockieren. Also **Zustandsautomat + Warteschlange + Retry/Timeout**, angehängt an den
vorhandenen Scheduler. `ai_run` ist append-only und genau einmal abschließbar — modelliert
einen Lauf über Zeit sauber.

## v1-Slice: Sprachmemo → Einsatzbericht-Entwurf

Spielt den ganzen Pfad einmal durch und bindet das ASR-Handy sofort ein. Das Modell trifft
dabei **null Werkzeug-Entscheidungen** — es macht *eine* schema-erzwungene Generierung.

| Schritt | Wer | Modell-Tools sichtbar |
|---|---|---|
| Memo hochgeladen → Workflow „Sprachmemo" | Trigger (Regel) | — |
| ASR-Handy aufrufen (Audio → Transkript) | Pipeline | — |
| Transkript → `ai.content_item` (`is_untrusted`) | Pipeline | — |
| Auftrags-/Objekt-Dossier holen (Soll aus Angebot) | Pipeline (RAG) | — |
| Berichtspositionen entwerfen | **Modell** (strukturiert) | **0** |
| `ai_proposal` anlegen (Ziel: `site_report`-Entwurf) | Pipeline | — |
| Mensch nimmt ab → App-Schicht schreibt über Fach-API | Mensch + Tore | — |

Bausteine des Slices: `ai.*`-Django-Models (managed=False) + State-only-Migration; der
**Executor** (`ai_run` starten/abschließen über `business_transaction`); der **LLM-Adapter**
(agnostisch, konfigurierbar) mit **Fake-Backend für Tests**; der **Tool-Registry/-Vertrag**
mit dem ASR-Handy als erstem Tool; der **Hash-/Versions-Verifizierer** (Payload-Hash + Ziel-
version bei Abnahme); der **`EXPIRED`-Job**; die **Vorschlags-Kachel** im Frontend (Übersicht
zeigt schon „Anbindung folgt").

## Implementierungs-Mapping (verifizierte Backend-Konventionen)

Bestandsaufnahme 2026-07-15 (Sonnet-Recherche), Belege im Code:

- **Migrationen:** `db/migrations/*.sql` (0001–0043) sind eingefroren; die Baseline
  `backend/db_core/migrations/0001_baseline.py` fährt sie per Glob-Loop in EINE DB
  (deshalb existiert das `ai`-Schema aus `0027` physisch schon). Alles Neue ist eine
  **Django-`RunSQL`-Migration** unter `backend/db_core/migrations/` (Muster
  `0054_site_report.py` = DDL, `0055_sitereport.py` = State-only-`CreateModel`).
- **`managed=False`-Models:** eine Datei `backend/db_core/models.py`; `db_table`-
  Quoting-Trick `'ai"."content_item'`; nicht-modellierte FK-Ziele als rohes
  `UUIDField` (z. B. `document_id`). Django lässt FK-Felder aus dem Migrations-State
  (bewusst, `0081` genauso) — `makemigrations --check` bleibt trotzdem sauber.
- **Writes:** ausschließlich über `db_core.db_context.business_transaction(app_user_id)`
  (setzt `app.current_user_id` per `SET LOCAL`; Retry auf 40001/40P01). Services nehmen
  die `app_user_id`-UUID, nie das Django-`request`.
- **Fehler:** Service wirft `ValueError`-Subklasse → API **422**; DB-Trigger (`P0001`)
  übersetzt `db_core/gate_errors.py` → 422; Rechte → **403** (`api/permissions.py`,
  Modul/Aktion z. B. `workflow/ANLEGEN`, `row_scope`). Nie vermischen.
- **API:** django-ninja-Router je Feature unter `backend/api/`, zentral registriert in
  `backend/api/api.py` (`api.add_router("/…", …)`, `auth=django_auth`).
- **Storage:** `db_core/storage.py` (offizielles `minio`-SDK), Upload-Service
  `services/dateien.py` (UUID-`storage_key`, MIME-Whitelist, 50 MB, kein Presigned-URL).
  `content.file` ist physisch unveränderlich; `content.document` hat **kein** Model.
- **Secrets:** Fernet-Muster `db_core/mail_crypto.py` + `MCN_MAIL_KEY` (fail-closed,
  nie geloggt) — Vorlage für LLM-/Tool-Credentials. **Kein HTTP-Client vorhanden** →
  LLM-/Tool-Client neu bauen, Idiom `StorageError`/`MailKeyError` (eigene Exception,
  kein Secret-Leak).
- **Hintergrund:** kein Celery/RQ; nur der tägliche Shell-Loop-Scheduler
  (`deploy/scheduler-entrypoint.sh`) ruft idempotente Management-Commands
  (`db_core/management/commands/`), Idempotenz garantiert die DB per UNIQUE-Index. Ein
  async/wiederaufnehmbarer KI-Job folgt diesem Muster (DB erzwingt Idempotenz, nicht Code).
- **Tests:** `pytest`+`pytest-django` baut die Test-DB über die volle Kette (echte
  Trigger); Fixture `app_user` (`backend/conftest.py`), API-Rollen-Clients
  (`backend/api/tests/conftest.py`); Trigger-Verstöße als `with pytest.raises(Error)`
  am Service vorbei.

## Umsetzungsstand

- ✅ **Schritt 1 — `ai.*`-Models + State-only-Migration `0105`** (`ContentItem`,
  `Embedding`, `AiRun`, `AiProposal` in `models.py`). Verifiziert: `manage.py check`
  0 Issues, `makemigrations --check` sauber, `db_core/tests/test_ai_grundlagen.py`
  **6/6 grün** (Model-DB-Parität aller vier Tabellen + DB-Tore: AiRun finish-once,
  AiProposal-Inhalt unveränderlich, keine Freigabe nach Ablauf, Serverzeit-Freigabe).
- ✅ **Schritt 2 — modell-agnostischer LLM-Adapter** (`db_core/ai/llm.py`): Port
  `LlmBackend`, `FakeBackend` (deterministisch, Dev/Test-Default), `OpenAICompatBackend`
  (llama.cpp/Ollama/vLLM; stdlib-HTTP, injizierbarer Transport → ohne Modell testbar;
  `response_format`=json_schema für Constrained Decoding), Profil-Fabrik `get_backend`
  (Modelle = Konfiguration, A/B über Profilwahl; API-Key nur als env-Verweis, fail-closed).
  17 Tests grün.
- ✅ **Schritt 3 — Executor** (`db_core/ai/executor.py`): Kontextmanager `ai_run` schreibt
  Provenance beim Start, schließt genau einmal ab (OK/FEHLER), akkumuliert `resource_usage`;
  getrennte kurze `business_transaction` für Start/Abschluss (Arbeit dazwischen ohne
  gehaltene Transaktion); respektiert die Trigger-Regel (nur Ausgangsfelder nachtragbar).
  3 Tests grün.
- ⏭ **Nächste Schritte:** Tool-Vertrag + ASR-Handy als erstes Tool; v1-Workflow
  Sprachmemo→Bericht (Router → Transkript → `content_item` → Entwurf → `ai_proposal`);
  Hash-/Versions-Verifizierer (bei Abnahme); `EXPIRED`-Job; Vorschlags-Kachel (Frontend).
