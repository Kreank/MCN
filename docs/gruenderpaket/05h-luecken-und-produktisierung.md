# Lücken, Risiken und Produktisierung

> Teil der Funktions- und Reifegradanalyse. Einstieg: `05-funktions-und-reifegradanalyse.md`.
> Stichtag **28.07.2026**, Arbeitsstand `develop` @ `0281db9`.

Diese Datei fasst zusammen, **was zwischen dem heutigen System und einem
verkaufbaren Produkt liegt**. Die Aufwandsangaben sind Schätzungen des
Analysierenden auf Basis vergleichbarer, bereits umgesetzter Slices dieses
Projekts (ein Slice = ca. 0,5–3 Personentage bei der bisherigen Arbeitsweise).
Sie sind **keine Zusagen** und gehören im Businessplan mit dieser Einordnung
zitiert.

---

## H1 Stufe 1 — sperrend vor der ersten externen Pilotierung

Diese Punkte verhindern, dass ein fremder Betrieb das System eigenständig
benutzen kann. Ohne sie ist jede Pilotierung in Wahrheit eine betreute
Vorführung.

| # | Lücke | Warum sperrend | Aufwand (Schätzung) |
|---:|---|---|---|
| 1 | **Benutzer anlegen / einladen** | Es gibt weder Endpunkt noch Oberfläche. Neue Benutzer entstehen nur über das gesperrte `/admin/` oder `createsuperuser`. Ein Kunde kann seine Mitarbeitenden nicht selbst aufnehmen | 2–4 Tage (Endpunkt, Einladungsmail, Erstpasswort-Flow, Rollenzuweisung) |
| 2 | **Mailversand scharf schalten — kontrolliert** | Heute die einzige Sicherung gegen Fehlversand ist ein `.env`-Schalter. Angebot, Rechnung, Mahnung und Passwort-Reset sind ohne Mail wertlos; mit Mail ist ein Fehlklick eine echte Mahnung an einen echten Kunden | 2–3 Tage (Sandbox-/Freigabemodus, Versandprotokoll, Bounce-Behandlung, bewusste Scharfschaltung je Betrieb) |
| 3 | **Off-box-Backup und dokumentierter Restore-Probelauf** | GoBD, zehn Jahre Aufbewahrung, unwiederbringliche Unterschriften in MinIO. Backup und Produktivdaten liegen auf derselben Platte | 2–3 Tage (zweites Ziel, Versionierung, Restore-Skript, ein scharf gefahrener Probelauf) |
| 4 | **Volle Testsuite als automatisches Release-Gate** | Die Suite ist inhaltlich in Ordnung (Lauf vom 28.07.2026: **4.187 bestanden, kein fachlicher Fehlschlag**), aber sie lief vor dem letzten Produktivdeploy **nicht** — das Gate hängt an Disziplin, nicht am Werkzeug. Genau der Fall, den die eigene Regel als CI-Auslöser benennt | 1–2 Tage (GitHub Actions: `check` + `pytest` gegen Wegwerf-Postgres + `ng build` als Merge-Gate) |
| 5 | **Datenimport für Bestandskunden** | Kein Betrieb wechselt ohne seine Kontakte, Objekte und offenen Posten. Es existiert kein Importpfad außer DATANORM (Artikel) | 5–10 Tage (CSV-Import Kontakte/Objekte/Artikel, Abgleich, Dry-run, Protokoll) |
| 6 | **Geführte Ersteinrichtung** | `GET /company/onboarding` liefert nur einen Status. Firmenprofil, Nummernkreise, Steuersätze, Lohngruppen, Mahnstufen, Rollen und Mailkonto muss heute jemand kennen, der das System gebaut hat | 3–5 Tage |

**Summe Stufe 1: grob 15–27 Personentage.**

---

## H2 Stufe 2 — nötig, damit der Pilot ein Produkt wird

| # | Lücke | Wirkung | Aufwand (Schätzung) |
|---:|---|---|---|
| 7 | **Mobile Nutzung im Feld** | Der gesamte Nutzen entsteht beim Monteur. Heute: Browser. Geräte-Token und rechtegefilterte Objektsicht sind vorbereitet, die Android-App ist **nicht gebaut**. Zwischenschritt: Touch-Tauglichkeit der bestehenden Oberfläche (Board-Kachelaktionen hängen an `:hover`) | Zwischenschritt 3–5 Tage; native App 30–60 Tage |
| 8 | **Telemetrie und Nutzenmessung** | Ohne sie bleibt jede Zeitersparnis eine Behauptung — und der Messplan aus `docs/TECHNISCHE_PRODUKTANALYSE.md` unausführbar | 3–5 Tage |
| 9 | **Support-, Update- und Datenexportprozess** | Ein Kunde muss wissen, wie er Hilfe bekommt, wann Updates kommen und wie er seine Daten wieder herausbekommt (Anti-Lock-in ist ein Verkaufsargument) | 2–4 Tage technisch, Rest organisatorisch |
| 10 | **Lösch-, Aufbewahrungs- und AVV-Prozesse** | DSGVO. Technisch weitgehend möglich (löschbare Rohtexte, DSGVO-Löschpfad für KI-Vorschläge), organisatorisch nicht ausformuliert | überwiegend Rechtsberatung |
| 11 | **Bank-/Zahlungsabgleich (CAMT/MT940)** | Zahlungen werden heute von Hand erfasst. Für einen Betrieb mit dreistelliger Rechnungszahl je Monat ist das der spürbarste tägliche Mehraufwand gegenüber etablierten Systemen | 5–8 Tage |
| 12 | **Marge in den Auswertungen** | Umsatz allein ist für einen Handwerksbetrieb die uninteressantere Zahl. Braucht die Einkaufspreis-Ebene bzw. den `billing_snapshot` | 3–5 Tage |
| 13 | **Lohnexport (DATEV Lohn / Lexware)** | Die Zeitwirtschaft ist arbeitsrechtlich sauber, endet aber in einer CSV | 4–6 Tage |
| 14 | **Externes Sicherheitsaudit / Penetrationstest** | Wird von größeren Kunden und manchen Förderstellen erwartet | Fremdleistung |

---

## H3 Stufe 3 — vor Skalierung auf viele Kunden

Der Kern ist eine **Produktentscheidung, keine Programmieraufgabe**: MCN ist
`is_singleton` — eine Firma je Datenbank (siehe `05g` G7).

| Variante | Technischer Aufwand | Betriebsaufwand je Kunde | Passt zur Lokal-KI-Position |
|---|---|---|---|
| **A — automatisierte Einzelinstanz / Appliance** | mittel (Provisionierung, Update-Automatik, Monitoring, Rollback): geschätzt 20–40 Tage | höher | **ja** |
| **B — echte Multi-Tenant-Plattform** | hoch (Datenmodell, Rechteprüfung, Indizes, Storage, Migration, Support): geschätzt 60–120 Tage, mit Regressionsrisiko im gesamten Bestand | niedrig | eingeschränkt |
| **C — hybrid** | mittel-hoch | mittel | ja |

**Empfehlung dieser Analyse: Variante A als erste Produktisierungsstufe.** Sie
erhält die Datenschutz- und Lokal-KI-Positionierung, vermeidet den systemweiten
Umbau und ist mit dem heutigen Deployment-Verfahren erreichbar. Sie begrenzt
aber die Kundenzahl je Betreuungsaufwand — die Finanzplanung muss das abbilden
(Betriebskosten je Instanz statt Grenzkosten nahe null).

Weiter nötig vor Skalierung: automatisierte Instanzbereitstellung, Monitoring
und Fehlerberichte, Rollback, Migrationsstrategie je Kundenversion, Lizenz- und
Abrechnungsverwaltung.

---

## H4 Fachliche Restlücken (bekannt, benannt, nicht sperrend)

| Lücke | Bereich | Bewertung |
|---|---|---|
| `building.address_id` über die API nicht befüllbar | Objekt | Blockiert WEG über mehrere Gebäudeadressen; halber Tag |
| `quick_intake` kennt keine Mieter (trägt immer `PROPERTY_OWNER` ein) | Erfassung | Erzeugt beim häufigsten Anruf einen falschen Beteiligtentyp; 1 Tag |
| Eigentümer doppelt erfassbar (`property_party_role` vs. Eigentumsstand) | Daten | Datenqualitätsrisiko, kein Fehler; 1–2 Tage |
| 11 Reiter in der Liegenschaftsmappe | Bedienung | Recherche liegt vor (`docs/roadmap/liegenschaft-reiter-verschlankung.md`), Zusammenlegung auf 6 kostet 0,5–3 Tage je Schritt; **braucht eine Entscheidung des Users** |
| Zwei Formulare für denselben Raum | Bedienung | Doppelpflegerisiko |
| Einheit und Anlage ohne eigene Dateiablage | Dateien | halber Tag |
| Doppelte Endpunktfamilie `wage-groups` / `wage_groups` | API | Vor externer Nutzung vereinheitlichen |
| Plantafel: Kachelaktionen nur per Hover/Fokus | Bedienung | Auf Touch nicht erreichbar — relevant fürs Tablet |
| Tagesansicht an den zwei Zeitumstellungstagen um eine Spalte versetzt | Anzeige | Kosmetisch, dokumentiert |
| Board-Einstellungen im `localStorage` | Betrieb | Erst klären, ob firmenweit oder je Benutzer |
| Feiertage zählen nicht bei der Urlaubsberechnung | HR | Bewusst — Änderung wirkt rückwirkend auf Salden |
| XRechnung/PEPPOL (B2G) | E-Rechnung | Bewusst ausgeklammert; eigener Slice mit Leitweg-ID |
| Eingangsbelege ohne OCR und ohne Freigabe-Workflow | Buchhaltung | Grundstock vorhanden |
| Lagerverwaltung | Material | **Entscheidung, keine Lücke** — muss im Vertrieb aktiv kommuniziert werden |
| 19 dauerhafte Teardown-Fehler in der Testsuite | Qualitätssicherung | Fachlich harmlos (`flush` scheitert an den eigenen No-Truncate-Triggern), aber dauerhaft rote Einträge verwischen „bekannt vs. neu"; Savepoint statt `transaction=True`, ein halber Tag |
| Frontend-Testabdeckung: 22 Spec-Dateien auf 254 TS-Dateien | Qualitätssicherung | Bedienketten kaum abgedeckt — genau dort saß schon einmal ein toter Pfad bei grünen Einzeltests |

---

## H5 Risiken für die Bewertung durch Dritte

| Risiko | Ausprägung | Gegenmaßnahme |
|---|---|---|
| **Schlüsselpersonenrisiko** | Ein Entwickler, 220 Commits in 22 Tagen, keine CI, kein zweiter Mitwirkender | CI einführen, Dokumentation bereinigen, zweite Person einarbeiten — genau die Auslöser, die das Projekt selbst benannt hat |
| **Kurze Betriebszeit** | Echtbetrieb seit 17.07.2026, also elf Tage zum Stichtag | Nicht kaschieren; die Breite über Tests und DB-Regeln belegen, nicht über Laufzeit |
| **Ein Referenzbetrieb, kein externer Kunde** | Kein zahlender Dritter, keine Vorher-/Nachher-Messung | Pilotprogramm mit Messplan; Zahlungsbereitschaft prüfen |
| **Dokumentation teilweise überholt** | `HANDOFF.md` und `BACKLOG.md` widersprechen dem tatsächlichen Stand (siehe `05g` G6) | Vor jeder Einsichtnahme Dritter bereinigen — ein Prüfer, der einen Widerspruch findet, misstraut danach allem |
| **Skalierungsfrage offen** | `is_singleton` | Produktentscheidung treffen **bevor** Umsatzhochrechnungen erstellt werden; die Variante bestimmt die Kostenstruktur |
| **Keine gemessenen Nutzenwerte** | Alle Zeit-/Fehlerersparnisse sind Hypothesen | In allen Unterlagen als solche kennzeichnen; erst nach Messung beziffern |
| **Abhängigkeit von lokaler KI-Hardware** | Kein standardisiertes Hardware-/Betriebsprofil | Referenzkonfiguration definieren und messen (Modell, GPU, Antwortzeit) |

---

## H6 Die kürzeste ehrliche Antwort auf „Wie weit ist das?"

**Der Fachkern ist weit; das Produkt ist es nicht.**

- Was ein Betrieb **fachlich** braucht — Kunde, Objekt, Anlage, Auftrag, Termin,
  Zeit, Material, Bericht, Angebot, Rechnung, Mahnung, Wartung, Personal — ist
  gebaut, getestet und in wesentlichen Teilen durch Datenbankregeln abgesichert.
- Was ein **Kunde** braucht, um es allein zu benutzen — Benutzeranlage,
  Datenübernahme, geführte Einrichtung, verlässlicher Mailversand, Support und
  ein wiederherstellbares Backup — fehlt an mehreren Stellen.
- Was ein **Markt** braucht — Mehrkundenbetrieb, mobile Feldnutzung,
  Nutzennachweis — ist entschieden bzw. vorbereitet, aber nicht gebaut.

Für Bank und Förderstelle ist das eine gute Ausgangslage: Der teure, riskante
Teil (Fachlogik in einer regulierten Domäne) ist erledigt und nachprüfbar; der
verbleibende Teil ist überwiegend **absehbare Produktarbeit** mit
kalkulierbarem Aufwand — grob **15–27 Personentage bis zur Pilotfähigkeit** und
je nach Skalierungsvariante **20–120 weitere Tage** bis zur Mehrkundenfähigkeit.
