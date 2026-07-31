# Funktionsinventar F — KI-Schicht

> Teil der Funktions- und Reifegradanalyse. Einstieg: `05-funktions-und-reifegradanalyse.md`.
> Stichtag **28.07.2026**, Arbeitsstand `develop` @ `0281db9`.

Rechte-Modul `ai`: **11 der 405 API-Operationen**, 3.249 Zeilen in
`backend/db_core/ai/`, 9 Tabellen im Schema `ai`, 18 Testdateien.

Dies ist der Bereich, in dem eine Gründungsunterlage am leichtesten zu weit geht.
Deshalb hier besonders scharf getrennt: **was implementiert und getestet ist**,
**was architektonisch trägt** und **was noch nicht nachgewiesen ist**.

Legende: **P** produktiv ausgerollt · **U** umgesetzt und getestet · **T** teilweise ·
**G** geplant · **F** fehlt.

---

## F1 Das tragende Prinzip

> Die KI schreibt nie selbst in eine Fachtabelle. Sie erzeugt einen **Vorschlag
> ohne fachliche Wirkung** (`ai.ai_proposal`). Erst die Annahme durch einen
> Menschen materialisiert ihn — über **dieselben Fach-Services**, Rechte,
> Statusautomaten, Freigaben und Datenbank-Trigger wie eine Handeingabe.

Belegstelle: `db_core/ai/proposal.py`. `approve()` ruft ausdrücklich die
bestehenden Fach-Services, nicht die Datenbank. Es gibt keinen zweiten
Schreibweg an den Triggern vorbei — und weil die Regeln physisch in PostgreSQL
sitzen (587 Trigger, davon 168 Fachregeln), ist das keine
Selbstverpflichtung, sondern eine Eigenschaft des Systems.

**Warum das förder- und investorenrelevant ist:** Es adressiert das zentrale
Problem agentischer Unternehmenssoftware — Automatisierung darf Governance,
Rechte und Nachvollziehbarkeit nicht umgehen. Wer die KI als privilegierten
Nebenweg baut, verliert genau die Prüfbarkeit, die im GoBD-Umfeld gefordert ist.

---

## F2 Umgesetzt und getestet

| Baustein | Reife | Evidenz | Was es leistet |
|---|:--:|---|---|
| **Modellagnostischer LLM-Adapter** | U | `ai/llm.py` (382 Z.), `db_core/tests/test_llm.py` | Ein Port, austauschbare Backends. Modell, Endpoint und Parameter sind **Konfiguration** (`MCN_AI_PROFILES`), kein Code — die Grundlage für A/B-Vergleiche über `AiRun.model_name`/`model_version`. Ohne Profil greift ein `FakeBackend`, damit Dev und Tests ohne Modell laufen |
| **Constrained Decoding (JSON-Schema)** | U | `ai/llm.py`, alle Workflows | Der zentrale Kompensationshebel für kleine lokale Modelle: die Ausgabeform wird erzwungen, nicht erhofft |
| **Lauf-Protokollierung mit Provenance** | U | `ai/executor.py`, Trigger `guard_ai_run_update` | Jeder Lauf trägt Modell, Workflow, Prompt, auslösenden Benutzer, Rechtekontext, Quellen und geplante Werkzeuge. „Genau einmal abgeschlossen" garantiert ein **Trigger**, nicht Code. `sources`/`tools_used` sind nach dem INSERT unveränderlich |
| **Vorschlagsverfahren** | U | `ai/proposal.py`, `/ai/proposals` (5 Op.), `db_core/tests/test_ai_proposal.py` | Annehmen (materialisiert über die Fach-API), Ablehnen, Löschen. Idempotent und nebenläufigkeitssicher (`SELECT … FOR UPDATE`) |
| **DSGVO-Löschpfad für Vorschläge** | U | `delete_proposal`, Trigger `guard_ai_proposal_delete` (0110) | Löschbar sind **nur** REJECTED/EXPIRED — gegen den personenbezogenen Text im `proposed_payload` (Art. 17) |
| **Resume-bare Workflow-Engine** | U | `ai/engine.py`, Trigger `guard_workflow_run` (0106) | QUEUED→RUNNING→WAITING→RUNNING→…→DONE/FAILED. Idempotenz an `(workflow_run, step_key)`. Der Handler läuft **nie unter gehaltener DB-Sperre** — sonst hinge eine Zeile am Modell-Endpunkt |
| **Werkzeug-Queue mit Lease, Retry und Reaper** | U | `ai/runtime.py`, `manage.py ki_tool_queue_tick`, `db_core/tests/test_ai_queue.py`, `…_reaper.py`, `db/tests/nebenlaeufigkeitstest_tool_queue.sh` | Claiming per `SELECT … FOR UPDATE SKIP LOCKED`, klassifizierte Fehler (transient vs. permanent) |
| **Werkzeug-Registry mit verschlüsseltem Bearer** | U | `ai/registry.py`, `manage.py ki_tool register` | Werkzeuge sind Konfiguration. Bearer Fernet-verschlüsselt (`MCN_CRED_KEY`, **isoliert vom Mailschlüssel**), Klartext verlässt die Schicht nur zum Dispatch und wird nie geloggt |
| **Passives Gerätemodell (MCN pollt, das Gerät ruft nicht)** | U | `ai/tool_client.py`, `docs/ki-tool-vertrag.md` | MCN initiiert **alle** Verbindungen. Das Gerät gilt als untrusted: Metriken werden auf eine Whitelist reduziert, seine Freitext-Fehlermeldung wird **nie** übernommen, eine vom Gerät behauptete Vertrauensstufe existiert nicht |
| **PII-Grenze im Werkzeugpfad** | U | `ai/runtime.py` | Audio/Bild liegen im **löschbaren** `content.file` und werden nur zur Dispatch-Zeit geladen; das Transkript landet im löschbaren `ai.content_item`, `tool_call` hält nur Verweis + Hash. `is_untrusted` leitet **der Server** aus der Capability ab |
| **Workflow Sprachmemo → Berichtsentwurf** | **T** | `ai/workflow_sprachmemo.py`, `POST /ai/sprachmemo`, `db_core/tests/test_ai_sprachmemo.py` | Vollständig implementiert und getestet. Der Bericht führt **keine Preise** (Schema und Prompt erzwingen das), das Transkript wird als **untrusted Daten** strikt von der System-Instruktion getrennt (Prompt-Injection über das gesprochene Wort). **Nicht nachgewiesen: der Lauf mit einem real angebundenen ASR-Gerät** |
| **Leitstand-Tagesbriefing** | U | `ai/leitstand_briefing.py` (376 Z.), `GET /ai/briefing`, `db_core/tests/test_ai_briefing.py` | Der Code sammelt die Lage **deterministisch** ein (mit harten Obergrenzen je Quelle); das Modell **formuliert nur**. Reine Leseansicht, deshalb bewusst ohne Freigabe-Tor |
| **Konversationeller Assistent („frag das CRM")** | U | `ai/assistent.py` (1.127 Z.), `/ai/conversations` (4 Op.), `features/ki-assistent`, 4 Testdateien | Kein offener ReAct-Loop, sondern **zwei enge, schema-erzwungene Modellschritte um eine deterministische Retrieval-Mitte**. Das Modell wählt Treffer als **Indizes in eine rechtegefilterte Liste**, nie als frei erfundene IDs |
| **Gesprächsspeicher** | U | `ai.conversation`, `ai.conversation_turn`, Migration 0117/0118 | Nachfragen möglich, Gespräche löschbar |
| **Sicherheitskern: Indexgrenze = Objektgrenze** | U | `db_core/tests/test_ki_assistent_offen.py` | Der Assistent kann nichts finden, was der Fragende in der Oberfläche nicht sähe — der Rechtefilter sitzt **vor** dem Modell, nicht danach |
| **Frontend Vorschläge** | U | `features/ki-vorschlaege` | Annehmen/Ablehnen mit Begründung |

---

## F3 Architektonisch entschieden, noch nicht gebaut

| Baustein | Reife | Stand | Anmerkung |
|---|:--:|---|---|
| **RAG / Firmenwissen (pgvector)** | **G** | Tabelle `ai.embedding` existiert (chunk, `embedding_model`, `embedding_version`, `content_hash`) — aber die Spalte `vector` ist `real[]`, **die pgvector-Erweiterung ist nicht installiert** (`pg_extension`: plpgsql, btree_gist, pg_trgm) | Es gibt damit **keinen Vektorindex und keine Ähnlichkeitssuche**. Entschieden ist: pgvector im **bestehenden** Postgres, eigenes Schema `knowledge`, ausdrücklich **vom Schutzstandard ausgenommen** (ein Index ist Cache, kein Original). Zeitpunkt bewusst zuletzt |
| **Weitere Vorschlagsarten** | **G** | v1 kennt `SITE_REPORT_ENTWURF` | Angebot, Terminvorschlag, Mahnentscheidung sind naheliegend, aber nicht implementiert |
| **Native Sprach-/Bilderfassung auf dem Gerät** | **G** | Vertrag und Queue stehen, Gerät fehlt | siehe F4 |

---

## F4 Nicht nachgewiesen — was **nicht** behauptet werden darf

1. **Kein Live-Durchklick der KI-Strecken mit einem realen Modellprofil.**
   Ohne gesetztes `MCN_AI_PROFILES` greift das `FakeBackend`. Sämtliche Tests
   laufen gegen dieses Fake oder gegen injizierte Transporte. Belegt ist damit
   die **Verdrahtung**, nicht die Antwortqualität eines konkreten Modells.
2. **Kein echtes ASR-Gerät angebunden.** Der Pfad Sprachmemo → Bericht ist
   end-to-end nur mit simuliertem Werkzeug durchlaufen
   (`manage.py ki_tool register` + `MCN_CRED_KEY` sind die offenen Schritte).
3. **Keine gemessene Erkennungsqualität, Annahmequote oder Zeitersparnis.** Es
   existiert keine Telemetrie, die Vorschläge, Korrekturen und Laufzeiten im
   Betriebsalltag zählt.
4. **Keine produktive Wissensbasis.** Siehe F3.
5. **Keine standardisierten Hardware-/Betriebsprofile** für Kundeninstallationen
   (welches Modell auf welcher GPU mit welchen Antwortzeiten).

**Zulässige Formulierung:** „Die Governance- und Orchestrierungsgrundlage für
lokal betriebene KI ist implementiert und getestet: Vorschlagsverfahren ohne
direkten Schreibweg, protokollierte Läufe, Werkzeug-Queue, verschlüsselte
Gerätezugänge und ein rechtegefilterter Auskunftsassistent. Die
Breitenvalidierung mit realem Modell und realer Feld-Hardware steht aus."

**Unzulässig:** „KI erledigt X automatisch", „spart X Prozent Zeit",
„vollautomatischer Berichtsentwurf".

---

## F5 Die Datenschutz-Position

Die Architektur ist **lokal-only** ausgelegt (Entscheidung wegen DSGVO): Das
größte Modell läuft als OpenAI-kompatibler Endpunkt (llama.cpp/Ollama/vLLM) im
eigenen Netz; die Wahl des Modells bleibt bewusst offen. Ergänzt wird das durch:

- **Datenklassifikation** (`LOCAL_ONLY`) je Inhalt,
- **`untrusted`-Kennzeichnung** für Inhalte aus externen Quellen, vom Server
  abgeleitet und nicht vom Gerät behauptet,
- **verschlüsselte Geräte-Bearer** mit eigenem Schlüssel, isoliert vom Mailkonto,
- **löschbare Rohtexte** bei nur gehashtem Audit-Eintrag.

Das ist ein belastbares Verkaufs- und Förderargument gegenüber cloudbasierten
Wettbewerbern — vorausgesetzt, die Unterlage sagt dazu, dass die konkrete
Modellwahl und die Betriebsprofile noch offen sind.

---

## Zusammenfassung Block F

| Ebene | Reife | Urteil |
|---|---|---|
| Governance-Architektur (Vorschlag, Tore, Audit, PII-Grenze) | **hoch** | implementiert, getestet, durch DB-Trigger abgesichert |
| Orchestrierung (Engine, Queue, Registry, Gerätevertrag) | **hoch** | implementiert und nebenläufigkeitsgetestet |
| Fachliche KI-Funktionen (Briefing, Assistent, Sprachmemo) | **mittel** | implementiert; Qualität mit realem Modell nicht gemessen |
| Wissensbasis / RAG | **niedrig** | Tabellenform vorhanden, pgvector nicht installiert |
| Feldhardware (ASR, Vision) | **niedrig** | Vertrag und Queue stehen, kein Gerät angebunden |
| Wirkungsnachweis (Zeit, Qualität, Annahmequote) | **fehlt** | keine Telemetrie |

**Kernsatz für externe Unterlagen:** Die KI-Schicht von MCN ist heute vor allem
eine **Sicherheits- und Kontrollarchitektur für KI in Betriebsdaten** — und erst
in zweiter Linie eine Sammlung von KI-Funktionen. Genau in dieser Reihenfolge
sollte sie auch dargestellt werden, weil das der belegbare Teil ist.
