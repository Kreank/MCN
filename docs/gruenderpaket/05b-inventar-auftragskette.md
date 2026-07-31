# Funktionsinventar B — Auftragskette und Ausführung

> Teil der Funktions- und Reifegradanalyse. Einstieg: `05-funktions-und-reifegradanalyse.md`.
> Stichtag **28.07.2026**, Arbeitsstand `develop` @ `0281db9`.

Abgedeckte Rechte-Module: `workflow`, `planung`, `zeiterfassung`.
Zusammen **109 der 405 API-Operationen** — der größte Einzelblock des Systems.

Legende: **P** produktiv ausgerollt · **U** umgesetzt und getestet · **T** teilweise ·
**G** geplant · **F** fehlt. „Live" = im ausgerollten Stand (`0fb1ae1`, Migration 0134).

---

## B0 Das Domänenmodell in einem Absatz

Zentral ist der **Auftrag** (`workflow.work_order`). Der **Vorgang**
(`service_case`) ist ein *optionaler* Eingangskorb für alles, was noch nicht
beauftragt ist; das **Projekt** ist die *optionale* Klammer über mehrere
Aufträge. Der **Einsatz** (`service_job`) ist der Termin am Board. Diese
Reihenfolge ist eine bewusste Entscheidung und unterscheidet MCN von
Ticket-zentrierten Systemen: Ein Anruf muss keinen Vorgang erzeugen, um zu einem
Termin zu werden.

---

## B1 Eingang, Vorgang, Projekt (`workflow`, 18 Operationen)

| Funktion | Reife | Live | Evidenz | Anmerkung |
|---|:--:|:--:|---|---|
| Vorgänge (Eingangskorb) mit Statusautomat | U | ✔ | `/workflow/service_cases` (6 Op.), Trigger `trg_service_case_initial_status` | Erlaubte Übergänge kommen vom Server (`GET …/transitions`), nicht aus dem UI |
| Vorgang zum Projekt hochstufen | U | ✔ | `POST …/promote-to-project` | Frühere Lücke, inzwischen geschlossen |
| Projekte mit Kategorien, Verantwortlichem, Logbuch | U | ✔ | `/workflow/projects` (9 Op.), `services/projekt.py` | Logbuch append-only |
| Checklisten am Projekt | U | ✔ | `…/checklists`, Migration 0036 (Schutz) | |
| Interne Notiz (nicht kundensichtbar) | U | ✔ | `POST …/internal-note` | |
| Schnellaufnahme (atomar: Kontakt + Objekt + Vorgang) | **T** | ✔ | `POST /workflow/quick-intake` | Funktioniert; **kennt aber keine Mieter** (siehe `05a` A3) |
| Kanban- und Listenansicht | U | ✔ | `features/vorgang-kanban`, `features/vorgang-liste`, `features/eingang-nav` | |
| Konfigurierbare Pipeline | U | ✔ | Migration 0042 | |

## B2 Aufträge (`workflow`, 12 Operationen)

| Funktion | Reife | Live | Evidenz | Anmerkung |
|---|:--:|:--:|---|---|
| Auftrag anlegen/ändern/lesen, mit und ohne Vorgang | U | ✔ | `/workflow/work_orders`, `services/auftrag.py` | |
| Statusautomat mit Freigabetor | U | ✔ | `POST …/status`, Trigger `trg_status_transition_guard` | Recht `FREIGEBEN`; DISPOSITION darf seit 0122 freigeben |
| Beteiligte am Auftrag (Auftraggeber ≠ Objekt) | U | ✔ | `POST …/parties` | |
| Zuständigkeit bestätigen | U | ✔ | `POST …/responsibility` | Beantwortet „darf der, der anruft, überhaupt beauftragen" |
| Nachweise/Evidence am Auftrag | U | ✔ | `POST …/evidence` | |
| Kundenhistorie am Auftrag | U | ✔ | `GET …/kundenhistorie` | |
| Nachtragsvorschau | U | ✔ | `GET …/nachtrag`, `api/tests/test_nachtrag_api.py` | Mehrleistung wird sichtbar, bevor sie fakturiert wird |
| Offene Abrechnung am Auftrag | U | ✔ | `GET …/offene-abrechnung`, `services/abrechnung.py` | Zentrale Doppelabrechnungssperre |
| Soll-Ist-Abgleich Angebot ↔ Bericht | U | ✔ | `GET …/soll-ist` | Schlüsselt über die **Quellzeile**, nicht über den Anzeigetext |
| Auftragszentriertes Frontend | U | ✔ | `features/auftrag-liste`, `features/auftrag-detail` | Nav ist auf Aufträge umgestellt (Commit `7d490db`) |

## B3 Aufgaben (`workflow`, 6 Operationen)

| Funktion | Reife | Live | Evidenz |
|---|:--:|:--:|---|
| Aufgaben anlegen/ändern/erledigen/verwerfen/wieder öffnen | U | ✔ | `/workflow/tasks` (6 Op.), `services/aufgabe.py` |
| Aufgabe direkt am Auftrag | U | ✔ | Migration 0129 |
| Eigener Zeilen-Scope (`row_scope='EIGENE'`) | U | ✔ | `api/tests/test_row_scope.py` — einer von **nur zwei** Bereichen mit echtem Zeilen-Scope |
| Frontend | U | ✔ | `features/aufgaben` |

## B4 Baustellenberichte (`workflow`, 9 Operationen)

| Funktion | Reife | Live | Evidenz | Anmerkung |
|---|:--:|:--:|---|---|
| Bericht anlegen/ändern, Positionen setzen | U | ✔ | `/workflow/site_reports`, `services/site_report.py` | **Führt keine Preise** — sonst wäre er eine Preisvereinbarung des Monteurs |
| Vorbelegen aus einem Angebot | U | ✔ | `POST …/vorbelegen`, `GET …/vorbelegen-angebote` | Herkunft der Position wird **abgeleitet**, nie vom Client geglaubt |
| Unterschrift und Versiegelung | U | ✔ | `POST …/sign`, Trigger `trg_site_report_protect` | Versiegelt **auch Positionen und Anhänge** |
| PDF-Ausfertigung | U | ✔ | `GET …/pdf`, `services/site_report_pdf.py` | |
| Berichtskopf einfrieren | U | ✔ | Migration 0132, `api/tests/test_bericht_kopf_einfrieren.py` | |
| Bericht ohne Auftrag (freier Termin) | U | ✔ | `api/tests/test_bericht_freier_termin_api.py` | |

**Bewertung:** Der Baustellenbericht ist der Beleg, an dem die
Rechtssicherheits-Architektur am deutlichsten wird — Versiegelung inklusive
Anhänge, abgeleitete Positionsidentität und ein Schema-Test, der
`information_schema` nach Geldspalten durchsucht, damit die Regel auch künftige
Migrationen überlebt.

---

## B5 Planung, Plantafel und Termine (`planung`, 42 Operationen)

| Funktion | Reife | Live | Evidenz | Anmerkung |
|---|:--:|:--:|---|---|
| Einsätze anlegen, terminieren, Status fortschreiben | U | ✔ | `/planung/einsaetze` (14 Op.), `services/einsatz.py` | Auftragsbezug ist **unveränderlich** |
| Mitarbeiterzuweisung | U | ✔ | `POST/DELETE …/assignments` | |
| Ressourcen (Fahrzeuge, Geräte) | U | ✔ | `/planung/ressourcen` (4 Op.) | |
| Zeit- und Materialbuchung am Einsatz | U | ✔ | `POST …/times`, `POST …/materials` | |
| Plantafel (Schwimmbahnen, Kollisionen) | U | ✔ | `GET /planung/plantafel`, `features/plantafel`, `db_core/tests/test_plantafel_board.py` | Doppelbelegung ist **Warnung, keine Sperre** |
| Kalenderansicht | U | ✔ | `features/planung-kalender` | |
| Terminkategorien mit Vorschlagsdauer | U | ✔ | `/planung/kategorien` (4 Op.) | Kategorieänderung verschiebt **keinen** Bestandstermin |
| Freier Termin (ohne Auftrag/Kontakt) | U | ✔ | `POST /planung/termine`, `db_core/tests/test_freier_termin_service.py` | Bewusst **kein** „hochstufen" |
| Serientermine | U | ✔ | `…/serie` (2 Op.), `db_core/tests/test_serientermine_service.py` | Echte Einzeltermine, keine Regel; `series_anchor` als Taktgeber |
| Ort am Termin (Gebäude/Einheit) | U | ✔ | Migration 0119, `db_core/tests/test_termin_ort_service.py` | |
| Qualifikationen und Bedarfsabgleich | U | ✔ | `/planung/qualifikationen`, `…/vorlagen` (13 Op.) | **Warnt, blockiert nicht**; Stichtag ist der Terminbeginn in Ortszeit |
| Abwesenheitsanzeige im Board | U | ✔ | `GET /planung/abwesend` | Zeigt „abwesend von–bis", **nicht** die Art (DSGVO Art. 9) |
| Anruf-Durchstich (Kunde + Auftrag + Termin in einem Formular) | U | ✔ | `POST /planung/anruf`, `services/telefonauftrag.py`, Migration 0122 | Der Kernprozess „Telefon klingelt" in einem Vorgang |

**Bekannte Einschränkungen (dokumentiert, nicht behoben):** Board-Einstellungen
liegen im `localStorage` (unklar, ob firmenweit oder je Benutzer); die
Tagesansicht setzt an den zwei Zeitumstellungstagen Balken eine Spalte daneben,
wenn ein Nachttermin das Band öffnet; Kachel-Aktionen hängen an `:hover`/
`:focus-within` und sind auf Touch vom Board aus nicht erreichbar. **Der letzte
Punkt ist für den Außendiensteinsatz auf Tablets relevant.**

---

## B6 Zeiterfassung (`zeiterfassung`, 22 Operationen)

Die zugehörigen Stammdaten (Zeitkategorien, Pausenregel, Feiertage — 7 weitere
Operationen) liegen am Modul `hr` und sind dort gezählt (`05e`).

| Funktion | Reife | Live | Evidenz | Anmerkung |
|---|:--:|:--:|---|---|
| Stempeluhr (Start/Pause/Weiter/Stopp) | U | ✔ | `/zeiterfassung/stempel/*` (4 Op.), `services/zeiterfassung.py` | Vergessenes Ausstempeln wird **nicht** automatisch beendet (§ 17 MiLoG) |
| Manuelle Einträge anlegen/ändern/löschen | U | ✔ | `/zeiterfassung/eintraege` (3 Op.) | Überlappung eigener Buchungen per EXCLUDE hart gesperrt |
| Arbeitstag: einreichen, bestätigen, ablehnen | U | ✔ | `/zeiterfassung/tage/{id}/*` (4 Op.) | Eigenes Schloss, unabhängig vom kaufmännischen Tor |
| Gesetzliche Pausen anwenden (§ 4 ArbZG) | U | ✔ | `POST …/pausen-anwenden`, `GET/PUT /hr/pausenregel` | Pause wird **voll** abgezogen, nicht auf die Schwelle gekappt |
| Stundenkonto und Ausgleichsbuchungen | U | ✔ | `/zeiterfassung/ausgleich` (3 Op.), `GET …/stundenkonto` | In **Minuten** append-only; Saldo abgeleitet |
| Niemand gleicht sein eigenes Konto aus | U | ✔ | Trigger, `api/tests/test_stundenausgleich_api.py` | Gilt auch über den Storno |
| Zeitkategorien | U | ✔ | `/hr/zeitkategorien` (4 Op.) | Ein Zeitstrahl, `is_work_time` als einziges hartes Attribut |
| Feiertagskalender | U | ✔ | `GET /hr/feiertage`, Migration 0068 | Gilt **nicht** für die Urlaubstage-Zählung (bewusst) |
| Stundenliste als CSV | U | ✔ | `GET /zeiterfassung/stundenliste.csv` | |
| Frontend | U | ✔ | `features/zeiterfassung`, `features/meine-zeiten`, `features/zeitkategorien` | |

**Bewertung:** Die Zeitwirtschaft ist arbeitsrechtlich sauber gedacht (MiLoG,
ArbZG, Selbstgenehmigungssperre im Trigger) und damit ein verkaufbares
Einzelargument. Was fehlt: **Lohnexport** (DATEV Lohn, Lexware) — die Stunden
enden heute in einer CSV.

---

## Zusammenfassung Block B

| Bereich | Operationen | Reife | Wesentliche Lücke |
|---|---:|---|---|
| Vorgang/Projekt | 18 | hoch | Mieter in der Schnellaufnahme |
| Auftrag | 12 | hoch | — |
| Aufgaben | 6 | hoch | — |
| Baustellenbericht | 9 | hoch | Feld-/Offline-Erfassung fehlt (keine App) |
| Planung/Plantafel | 42 | hoch | Touch-Bedienung am Board; Zeitumstellungs-Anzeigefehler |
| Zeiterfassung | 22 | hoch | kein Lohnexport |

**Größte strukturelle Lücke des gesamten Blocks:** Es gibt **keine mobile
Anwendung**. Der Monteur arbeitet heute im Browser. Geräte-Token-Authentifizierung
(`POST /auth/device/login`, `DeviceTokenAuth`) und eine rechtegefilterte
Monteur-Objektsicht sind vorbereitet — die native Android-App ist **geplant, nicht
gebaut**. Für ein Produkt, dessen Nutzen im Außendienst entsteht, ist das die
wichtigste Produktlücke vor der Pilotierung.
