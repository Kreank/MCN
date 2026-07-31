# Funktions- und Reifegradanalyse MCN

> **Phase I des Gründerpakets — Due Diligence.**
> Erhebungsstichtag: **28.07.2026**. Grundlage: Quellcode, Datenbankstruktur,
> Testsuite, Migrationen, Git-Historie und Betriebskonfiguration im Repository
> `D:\Mitra\MCN`, Arbeitsstand `develop` @ `0281db9`.

Dieses Dokument ist die **Tatsachengrundlage** für Businessplan, Whitepaper,
Förderanträge und Decks. Es ist bewusst nüchtern und nennt Lücken beim Namen.
Alles, was hier nicht belegt ist, darf in keiner nach außen gehenden Unterlage
behauptet werden.

## Aufbau der Analyse

| Datei | Inhalt |
|---|---|
| **`05` (diese Datei)** | Methodik, Kennzahlen, Gesamtbild, Verifikation, Gesamturteil |
| `05a-inventar-kunde-objekt.md` | Kontakte, Liegenschaften, Räume, Anlagen, Belegung, Eigentum, Verwaltung, Dateien |
| `05b-inventar-auftragskette.md` | Vorgang, Projekt, Auftrag, Aufgabe, Bericht, Planung, Zeiterfassung |
| `05c-inventar-kaufmaennisch.md` | Angebot, Rechnung, Buchhaltung, Eingangsbelege, E-Rechnung, DATEV, Auswertungen |
| `05d-inventar-artikel-lieferanten.md` | Artikelstamm, Preislogik, DATANORM, IDS-Connect, Gerätewissen |
| `05e-inventar-personal-wartung-organisation.md` | Personal, Wartung/Fristen, Firma, Rechte, Freigaben, Login, Suche, Dossiers |
| `05f-inventar-ki.md` | KI-Schicht: Governance, Orchestrierung, Funktionen, offene Nachweise |
| `05g-architektur-betrieb-sicherheit.md` | Architektur, Datenbankregelwerk, Sicherheit, Betrieb, Qualitätssicherung, Skalierung |
| `05h-luecken-und-produktisierung.md` | Priorisierte Lückenliste mit Aufwandsschätzungen, Risiken |

---

## 1. Methodik

**Erhoben wurde ausschließlich aus prüfbaren Quellen:**

- das aus dem Code erzeugte **OpenAPI-Schema** (nicht die Dokumentation),
- die **tatsächliche Datenbankstruktur** einer gegen den aktuellen
  Migrationskopf aufgebauten Instanz (`pg_trigger`, `pg_constraint`,
  `information_schema`),
- **Testdateien und Testfunktionen** im Repository,
- die **Git-Historie** (welcher Stand ist ausgerollt, welcher nicht),
- die **Betriebskonfiguration** (`deploy/docker-compose.yml`, Entrypoints),
- die Projektdokumentation — **nur als Hinweis, nie als Beleg**, weil sie sich
  nachweislich in Teilen widerspricht (siehe `05g` G6).

**Nicht erhoben werden konnte** (weil außerhalb des Repositories):
tatsächliche Nutzungshäufigkeit im Live-Betrieb, Datenvolumen der
Produktivinstanz, Antwortzeiten unter Last.

### Reifegradskala

| Klasse | Bedeutung |
|---|---|
| **P — produktiv** | im Code, getestet **und** im ausgerollten Stand enthalten |
| **U — umgesetzt** | im Code vorhanden und durch Tests abgesichert |
| **T — teilweise** | wesentliche Grundlage vorhanden, End-to-End-Nachweis oder Teilfunktion fehlt |
| **G — geplant** | Architekturentscheidung getroffen, nicht implementiert |
| **F — fehlt** | nicht vorhanden |

### Der wichtigste methodische Vorbehalt

**„Ausgerollt" ist nicht dasselbe wie „im Alltag benutzt".** Aus dem Repository
lässt sich belegen, welcher Code auf dem Server liegt — nicht, wie oft ihn
jemand aufruft. Es gibt keine Telemetrie. Wo diese Analyse **P** vergibt, heißt
das: *ausgerollt*. Der Nachweis tatsächlicher Nutzung existiert nur pauschal
über die Aussage, dass die Instanz seit dem 17.07.2026 mit echten Kundendaten
und rund zwei Millionen Artikeln arbeitet.

### Klärung des Live-Stands (Widerspruch in der Projektdokumentation)

`docs/HANDOFF.md` behauptet, der lokale Zweig `main` liege 27 Commits vor
`origin/main`. **Tatsächlich ist es umgekehrt:**

| Zweig | Stand | Datum |
|---|---|---|
| `origin/main` (Live-Linie) | `90553ce` | 27.07.2026 |
| `main` (lokal, veraltet) | `e41c36d` | 14.07.2026 — **89 Commits hinter** `origin/main` |
| `develop` (Arbeitsstand) | `0281db9` | 28.07.2026 — 3 Commits vor `origin/main` |

Der zuletzt **ausgerollte** Stand ist laut `HANDOFF.md` der Commit `0fb1ae1` vom
22.07.2026 mit Migrationskopf **0134**; das ist mit der Git-Historie konsistent
(`0134_vollmacht_modell` ist die letzte Migration in diesem Commit). Danach sind
**sechs Commits** entstanden, die **nicht live** sind — darunter Migration
0135/0136 (Wartungsvertrag ↔ Anlage), die Anlagenkarte und die Gebäudeansicht.

---

## 2. Kennzahlen (gemessen am 28.07.2026)

### Umfang

| Indikator | Wert |
|---|---:|
| API-Operationen (aus dem OpenAPI-Schema) | **405** |
| API-Pfade | 333 |
| Schema-Definitionen im OpenAPI | 573 |
| Frontend-Routen | 88 |
| Frontend-Featurebereiche | 87 |
| Datenbanktabellen | 161 |
| Datenbankschemata | 18 |
| Django-Migrationen | 138 |
| Python-Dateien (ohne Fremdbibliotheken) | 474 |
| TypeScript-Dateien | 254 |
| Eigencode gesamt | ≈ 292.500 Zeilen |
| davon Backend-Testcode | 67.594 Zeilen (42 % des Backends) |
| Commits seit Projektbeginn (06.07.2026) | 220 |

### Regelwerk in der Datenbank

| Regelart | Anzahl |
|---|---:|
| Fachregel-Trigger (Statusautomaten, Einfrieren, Konsistenz) | **168** |
| Schutz-Trigger (Audit / No-Delete / No-Truncate) | 321 |
| CHECK-Constraints | 660 |
| Fremdschlüssel | 363 |
| EXCLUDE-Constraints (Überlappungsfreiheit) | 17 |
| PL/pgSQL-Funktionen | 341 |

### Tests

| Indikator | Wert |
|---|---:|
| Backend-Testdateien | 187 |
| Backend-Testfunktionen (Quelltext) | ≈ 3.117 |
| **ausgeführte Testfälle (inkl. Parametrisierung)** | **4.187 bestanden**, 15 übersprungen |
| Laufzeit der vollen Suite | 18 min 05 s |
| SQL-Akzeptanztestsuiten | 7 |
| Nebenläufigkeits-Testskripte | 4 |
| **Frontend-Testdateien** | **22** |

---

## 3. Gesamtbild je Domäne

| Domäne | Operationen | Reife | Live | Schwerster offener Punkt |
|---|---:|:--:|:--:|---|
| Kontakte (`identity`) | 20 | hoch | ✔ | kein Import aus Fremdsystemen |
| Liegenschaften/Räume/Anlagen (`property`) | 27 | hoch | teilw. | Gebäudeadresse nicht erfassbar; Gebäudeansicht nicht ausgerollt |
| Belegung/Eigentum (`tenure`) | 13 | mittel-hoch | ✔ | Mieter fehlen in der Schnellaufnahme; doppelte Eigentümerpflege |
| Verwaltung/Vollmacht (`management`) | 10 | hoch | ✔ | — |
| Vorgang/Projekt/Auftrag/Aufgabe/Bericht (`workflow`) | 45 | hoch | ✔ | keine Feld-/Offline-Erfassung |
| Planung/Plantafel (`planung`) | 42 | hoch | ✔ | Touch-Bedienung am Board |
| Zeiterfassung (`zeiterfassung`) | 22 | hoch | ✔ | kein Lohnexport |
| Angebot/Rechnung (`invoicing`) | 35 | hoch | ✔ | Versand betrieblich stillgelegt |
| Buchhaltung (`buchhaltung`) | 14 | hoch | ✔ | kein Bank-/Kontoauszug-Import |
| Eingangsbelege (`accounting`) | 11 | Grundstock | ✔ | kein OCR, kein Freigabe-Workflow |
| Artikel/Preise/Lieferanten (`pricing`) | 44 | sehr hoch | ✔ | kein Live-Test gegen ein echtes Händlerportal |
| Gerätewissen | 3 | hoch | ✔ | EK einzelner Hersteller fehlt |
| Wartung/Fristen (`maintenance`) | 20 | hoch | teilw. | Anlagenzuordnung nicht ausgerollt |
| Personal (`hr`) | 24 | hoch | ✔ | kein Lohnexport; Steuer/Bank bewusst offen |
| Firma/Organisation (`company`) | 18 | mittel-hoch | ✔ | kein Einrichtungsassistent |
| Rechte/Freigaben/Login (`security`, `auth`) | 20 | hoch | ✔ | **keine Benutzeranlage** |
| Dateien (`content`) | 9 | hoch | ✔ | Einheit/Anlage ohne eigene Ablage |
| Auswertungen | 11 | mittel | ✔ | keine Marge |
| Suche/Dossiers | 5 | hoch | ✔ | — |
| **KI (`ai`)** | 11 | s. u. | ✔ | kein Live-Durchklick, kein ASR-Gerät, kein RAG |
| **Mobile** | — | **F** | — | **nicht gebaut** |
| **Summe** | **404** | | | zuzüglich `/health` = **405** |

Die Zeitwirtschafts-Stammdaten (Zeitkategorien, Pausenregel, Feiertage — 7
Operationen) sind unter `hr` gezählt, nicht unter Zeiterfassung; die Spalte ist
damit überschneidungsfrei.

Die KI-Schicht lässt sich nicht mit einer Note beschreiben; sie zerfällt in eine
**reife Governance-/Orchestrierungsarchitektur** und **unbewiesene fachliche
Wirkung** (Details `05f`).

---

## 4. Verifikation im Rahmen dieser Analyse

| Prüfung | Ergebnis |
|---|---|
| OpenAPI-Schema aus dem Code erzeugbar | **bestanden** — 333 Pfade, 405 Operationen, 573 Schemata |
| Datenbank gegen Migrationskopf 0136 aufgebaut | **bestanden** — 161 Tabellen, 587 Trigger, 660 CHECKs |
| `ng build --configuration production` | **bestanden** (Exit 0, 19,1 s, 140 Lazy-Chunks). **Eine Budget-Warnung:** `angebot-editor.scss` 9,72 kB gegen 8 kB Budget — die Aussage in `docs/BACKLOG.md`, das Frontend baue ohne Budget-Warnung, ist überholt |
| **`pytest` (volle Backend-Suite)** | **4.187 bestanden · 15 übersprungen · 19 Fehler · 1.085 s (18:05)** — die 19 Fehler sind **ausnahmslos** das dokumentierte Testrahmen-Artefakt („Database `test_mitra_crm_test` couldn't be flushed" beim Aufräumen von Tests mit `transaction=True`), verteilt auf vier Dateien. **Kein einziger fachlicher Testfehlschlag.** Vollständige Ausgabe unten |
| E-Rechnungs-Konformität (veraPDF, Mustang) | **nicht erneut gefahren** — auf dem Prüfrechner ist kein Java installiert; der Test überspringt dann sauber. Der Nachweis stammt aus `docs/erechnung-validierung.md` und dem Kopf von `services/erechnung.py` |

### Der Suitenlauf im Detail

Aufruf: `MCN_DEBUG=1 MCN_DB_NAME=mitra_crm_test MCN_DB_PASSWORD=… uv run pytest -q`
gegen die Dev-Datenbank (Container `mitra-crm-test`, PostgreSQL 16, Port 55432),
Arbeitsstand `develop` @ `0281db9`.

```text
4187 passed, 15 skipped, 19 errors in 1085.65s (0:18:05)
```

Die 19 Fehler entstehen **nach** dem eigentlichen Test, im Teardown von
Django-Tests mit `transaction=True`: Django versucht, die Testdatenbank per
`flush` zu leeren, und scheitert an den No-Truncate-Schutztriggern des Systems.
Betroffen sind ausgerechnet die Tests, die belegen, dass die **Datenbank**
selbst Löschungen und unzulässige Schreibvorgänge verweigert (u. a.
`test_die_DATENBANK_verbietet_das_loeschen`,
`test_datenbank_verweigert_mahnung_auf_stornierte_rechnung`,
`test_db_sperrt_die_zweite_bindung_am_service_vorbei`). Die Ursache ist also der
Schutzmechanismus, dessen Wirksamkeit diese Tests nachweisen — ein bekanntes und
in `docs/` dokumentiertes Artefakt, kein Produktfehler. **Es sollte trotzdem
bereinigt werden**, weil eine Suite mit 19 dauerhaften Fehlern die Unterscheidung
zwischen „bekannt" und „neu" dem Gedächtnis überlässt (Aufwand: Savepoint statt
`transaction=True`, geschätzt ein halber Tag).

> **Belegbarer Satz für externe Unterlagen:** *„Die Backend-Testsuite umfasst 187
> Dateien und 4.187 ausgeführte Testfälle; sie läuft in rund 18 Minuten
> vollständig durch, ohne einen fachlichen Fehlschlag."*
> **Nicht behaupten:** „fehlerfrei" ohne die Erläuterung der 19 Teardown-Fehler.

---

## 5. Gesamturteil

**MCN ist ein betriebsfähiger Vertical-Software-Kern mit einer frühen, aber
architektonisch ungewöhnlich sauberen KI-Schicht — und noch kein Produkt für
fremde Betriebe.**

### Was die Analyse belegt

1. **Fachliche Tiefe statt Feature-Breite.** Das Objektmodell reicht von der
   Liegenschaft über Gebäude, Einheit, Raum, Wand und Öffnung bis zur
   technischen Anlage — und daneben stehen Mietverhältnis, Eigentumsanteil,
   Verwaltungsmandat und Vollmacht als eigene, prüfbare Entitäten. Das ist im
   Handwerkersoftware-Umfeld unüblich und für Gebäudeservice, Mehrfamilienhaus
   und WEG unmittelbar nutzbar.
2. **Regeln, die halten.** 168 Fachregel-Trigger, 660 CHECK-Constraints und 17
   EXCLUDE-Constraints setzen zentrale kaufmännische und arbeitsrechtliche
   Regeln **physisch** durch — Doppelabrechnungssperre, Beleg-Festschreibung,
   Versiegelung unterschriebener Berichte, überlappungsfreie Arbeitsverträge,
   idempotente Fälligkeiten, Selbstgenehmigungssperre. Diese Regeln sind auch
   für die KI nicht umgehbar.
3. **Externe Konformität dort, wo sie zählt.** ZUGFeRD/Factur-X im Profil
   EN16931 ist mit den Referenzvalidatoren veraPDF und Mustang an sechs
   Belegformen ohne Verstoß geprüft — nicht nur mit eigenen Tests.
4. **Eine KI-Architektur, die das eigentliche Problem adressiert.** Die KI hat
   keinen Schreibweg an den Toren vorbei; jeder Lauf ist mit Provenance
   protokolliert, Rohtexte sind löschbar, Geräte gelten als untrusted, der
   Auskunftsassistent kann nichts finden, was der Fragende nicht sähe.
5. **Eine dokumentierte Entscheidungslage.** `INVARIANTEN.md` führt 395 Zeilen
   Regeln, jede mit dem konkreten Schaden, der ohne sie entstanden ist. Für eine
   technische Due Diligence ist das ein seltener und starker Befund.

### Was die Analyse ebenso deutlich zeigt

1. **Ein fremder Betrieb kann das System heute nicht allein in Betrieb nehmen** —
   es fehlen Benutzeranlage, Datenübernahme, geführte Einrichtung und ein
   verlässlich scharfer Mailversand.
2. **Der Nutzen ist nicht gemessen.** Keine Telemetrie, keine
   Vorher-/Nachher-Werte, kein externer Kunde. Jede Zeit- oder
   Fehlerersparnis ist bis auf Weiteres eine Hypothese.
3. **Der Außendienst hat keine App.** Das größte Nutzenversprechen entsteht beim
   Monteur; dort ist die Umsetzung am dünnsten.
4. **Mehrkundenfähigkeit ist eine offene Produktentscheidung**, keine
   Implementierungsaufgabe — `company.company_profile.is_singleton` ist der
   Beleg. Die gewählte Variante bestimmt die Kostenstruktur des gesamten
   Geschäftsmodells und muss **vor** der Finanzplanung feststehen.
5. **Die Betriebsabsicherung ist noch nicht auf GoBD-Ernstfallniveau.** Backup
   ohne Off-box-Ziel und ohne Restore-Probelauf, keine CI, und vor dem letzten
   Produktivdeploy lief die Testsuite nicht.

### In einem Satz

> Der teure und riskante Teil — belastbare Fachlogik in einer regulierten
> Domäne, physisch durchgesetzt — ist gebaut und nachprüfbar. Was fehlt, ist
> überwiegend absehbare Produktarbeit: grob **15–27 Personentage bis zur
> Pilotfähigkeit** und je nach Skalierungsvariante **20–120 weitere Tage** bis
> zur Mehrkundenfähigkeit (Schätzung, Herleitung in `05h`).

---

## 6. Folgerungen für die Claim-Evidenz-Matrix

Aus dieser Analyse ergeben sich Änderungen an `01-claim-evidenz-matrix.md`:

| Aussage | bisher | neu | Grund |
|---|---|---|---|
| ZUGFeRD/Factur-X umgesetzt | UMGESETZT | **UMGESETZT + extern validiert** | veraPDF 1.30.2 und Mustang 2.24.0, sechs Belegformen, kein Verstoß |
| MCN ist mandantenfähig | FALSCH HEUTE | **FALSCH HEUTE (belegt)** | `company.company_profile.is_singleton NOT NULL DEFAULT true` |
| lokale KI | TEILWEISE | **TEILWEISE (präzisiert)** | Adapter, Profile und `LOCAL_ONLY` umgesetzt; ohne `MCN_AI_PROFILES` greift ein `FakeBackend` — kein Lauf mit realem Modell nachgewiesen |
| RAG/Wissensbasis | (nicht geführt) | **GEPLANT** | `ai.embedding` existiert als Tabelle, **pgvector ist nicht installiert**; kein Vektorindex, keine Ähnlichkeitssuche |
| Benutzerverwaltung | (nicht geführt) | **FEHLT** | kein Endpunkt, keine Oberfläche |
| Testsuite grün | (nicht geführt) | **NICHT FREIGEGEBEN** | Suitenlauf zum Stichtag nicht abgeschlossen |
| Mobile/Android-App | (nicht geführt) | **GEPLANT** | Geräte-Token vorhanden, App nicht gebaut |

---

## 7. Empfohlene nächste Schritte der Due Diligence

1. **Den Suitenlauf abschließen und das Ergebnis hier nachtragen** — ohne ihn
   fehlt der wichtigste Qualitätsbeleg.
2. **`HANDOFF.md` und `BACKLOG.md` gegen den tatsächlichen Stand bereinigen**,
   bevor Dritte Einsicht bekommen (siehe `05g` G6).
3. **Die Skalierungsvariante entscheiden** (`05h` H3) — sie ist Voraussetzung
   für Preismodell, Unit Economics und Personalplanung.
4. **Den Messplan starten** (`docs/TECHNISCHE_PRODUKTANALYSE.md`, Abschnitt 10)
   im Referenzbetrieb, damit die ersten belastbaren Nutzenwerte vorliegen,
   bevor der Businessplan finalisiert wird.
