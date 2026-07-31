# MCN-Gründerpaket

> Masterstruktur, Stand 28.07.2026

Dieses Verzeichnis bündelt alle Unterlagen für Gründung, Finanzierung,
Förderung, Vertrieb und Unternehmensaufbau. Sämtliche Fassungen greifen auf
dieselbe Fakten-, Quellen- und Finanzbasis zurück. Dadurch bleiben Zahlen,
Produktstatus und Aussagen konsistent, obwohl Banken, Förderstellen,
Investoren und Kunden unterschiedliche Schwerpunkte erhalten.

## Zielbild

| Baustein | Zielumfang | Hauptadressat |
|---|---:|---|
| Businessplan | 80–120 Seiten | Banken, Förderstellen, Investoren |
| Finanzmodell | 3–5 Jahre, Szenarien | Banken, Investoren, Gründer |
| Technisches Whitepaper | ca. 30 Seiten | technische Due Diligence, Partner |
| Markt- und Wettbewerbsanalyse | ca. 20 Seiten | Investoren, Förderstellen |
| Marketing- und Vertriebsstrategie | ca. 15 Seiten | Gründer, Investoren |
| Investor Deck | 15–20 Folien | VC, Angels |
| Kunden-Pitch | 10–15 Folien | SHK-Pilot- und Zielkunden |
| Produktvision | 10–20 Seiten | Team, Partner, Recruiting |
| Roadmap bis Version 5.0 | 5 Jahre | Team, Investoren, Förderung |
| Unternehmensstrategie | 15–25 Seiten | Gründer, Beirat, Investoren |

Gesamtumfang der Langfassungen: ca. 170–200 Seiten zuzüglich Anhänge und
Tabellen. Decks sind Verdichtungen und werden nicht zum Seitenumfang addiert.

## Dokumentenarchitektur

### 00 – Steuerung und Belege

- `00-masterplan.md` – Kapitel, Reihenfolge, Qualitätskriterien
- `01-claim-evidenz-matrix.md` – welche Aussage worauf beruht
- `02-quellenregister.md` – interne und externe Quellen
- `03-annahmen-und-offene-eingaben.md` – fehlende Gründer- und Finanzdaten
- `04-begriffe-und-zahlen.md` – verbindliche Terminologie und Kennzahlen

### 05 – Funktions- und Reifegradanalyse (Phase I, abgeschlossen 28.07.2026)

Die technische und fachliche Due Diligence. Gemessen aus Quellcode,
Datenbankstruktur, OpenAPI-Schema, Testsuite und Git-Historie — nicht aus der
Projektdokumentation.

- `05-funktions-und-reifegradanalyse.md` – **Einstieg:** Methodik, Kennzahlen,
  Gesamtbild je Domäne, Verifikation, Gesamturteil, Folgerungen für die
  Claim-Evidenz-Matrix
- `05a-inventar-kunde-objekt.md` – Kontakte, Liegenschaften, Räume, Anlagen,
  Belegung, Eigentum, Verwaltung, Dateien
- `05b-inventar-auftragskette.md` – Vorgang, Projekt, Auftrag, Aufgabe,
  Baustellenbericht, Planung, Zeiterfassung
- `05c-inventar-kaufmaennisch.md` – Angebot, Rechnung, Buchhaltung,
  Eingangsbelege, E-Rechnung, DATEV, Auswertungen
- `05d-inventar-artikel-lieferanten.md` – Artikelstamm, Preislogik, DATANORM,
  IDS-Connect, Gerätewissen
- `05e-inventar-personal-wartung-organisation.md` – Personal, Wartung/Fristen,
  Firma, Rechte, Freigaben, Login, Suche, Dossiers
- `05f-inventar-ki.md` – KI-Schicht: Governance, Orchestrierung, Funktionen,
  offene Nachweise
- `05g-architektur-betrieb-sicherheit.md` – Architektur, Datenbankregelwerk,
  Sicherheit, Betrieb, Qualitätssicherung, Skalierung
- `05h-luecken-und-produktisierung.md` – priorisierte Lückenliste mit
  Aufwandsschätzungen, Risiken für die Bewertung durch Dritte

### 10 – Hauptdokumente

- `10-businessplan.md`
- `11-markt-und-wettbewerb.md`
- `12-marketing-und-vertrieb.md`
- `13-unternehmensstrategie.md`
- `14-produktvision.md`
- `15-roadmap-v5.md`
- `16-technisches-whitepaper.md`
- `17-foerdermittelstrategie.md`

### 20 – Finanzunterlagen

- `20-finanzmodell.xlsx`
- `21-finanzmodell-dokumentation.md`
- `22-kapitalbedarf-und-mittelverwendung.md`
- `23-preismodell.md`

### 30 – Präsentationen

- `30-investor-deck.pptx`
- `31-kunden-pitch.pptx`
- `32-recruiting-deck.pptx`

### 40 – Anhänge

- technische Produktanalyse
- Architekturdiagramme
- Prozessdarstellungen
- UI-Mockups und Produktscreenshots
- Gründerlebensläufe
- Pilotabsichten und Referenzen
- Quellen- und Methodennachweise

## Zielgruppenspezifische Fassungen

Ein einziges 200-Seiten-Dokument ist keine geeignete Unterlage für jede
Situation. Die gemeinsame Langfassung dient als Wissens- und
Due-Diligence-Basis. Daraus werden vier Fassungen erzeugt:

1. **Bank/Förderung:** Kapitaldienst, Liquidität, Umsetzungsplan,
   Fördergegenstand, Arbeitsplätze und Risiken.
2. **Investor:** Marktgröße, Differenzierung, Wachstum, Unit Economics, Team,
   Kapitalbedarf und Exit-Optionen.
3. **Kunde:** konkrete Prozesse, Einführung, Datenschutz, Nutzen,
   Integrationen und Preis.
4. **Technische Prüfung:** Architektur, Sicherheitsmodell, KI-Governance,
   Skalierung, Tests und offene Risiken.

## Statusregeln

Jede Produktaussage trägt intern eine der folgenden Klassen:

- **PRODUKTIV:** im aktuellen Echtbetrieb eingesetzt;
- **UMGESETZT:** im Code vorhanden und getestet, aber nicht zwingend live
  durchgeklickt;
- **TEILWEISE:** wesentliche Grundlage vorhanden, End-to-End-Nachweis oder
  Teilfunktion fehlt;
- **GEPLANT:** Roadmap oder Architekturentscheidung, noch nicht umgesetzt;
- **HYPOTHESE:** Markt-, Nutzen-, Preis- oder Wachstumsthese, noch zu validieren.

Diese Klassen werden in externen Dokumenten in natürliche Sprache übersetzt,
aber niemals inhaltlich verwischt.

## Arbeitsreihenfolge

1. technische und fachliche Due Diligence abschließen;
2. Claim-Evidenz-Matrix und verbindliche Produktbeschreibung fixieren;
3. aktuelle Markt-, Wettbewerbs- und Förderquellen recherchieren;
4. Gründer-, Rechtsform-, Preis- und Finanzdaten ergänzen;
5. Businessplan und Whitepaper parallel ausarbeiten;
6. Finanzmodell mit drei Szenarien aufbauen;
7. Decks aus den fertigen Langfassungen ableiten;
8. Konsistenz-, Zahlen-, Quellen- und Gegenargumentprüfung.

## Aktueller Arbeitsstand

- Markt- und Wettbewerbsanalyse: erste belastbare Fassung erstellt
- Funktions- und Reifegradanalyse: abgeschlossen, neun Dokumente (`05`–`05h`)
- Claim-Evidenz-Matrix: mit gemessenen Produktdaten synchronisiert
- Businessplan/Whitepaper/Finanzmodell/Decks: noch auszuarbeiten

Verbindliche gemessene Eckwerte für alle Folgedokumente:

- 405 API-Operationen auf 333 Pfaden, 573 OpenAPI-Schemata
- 161 Tabellen in 18 Datenbankschemata
- 168 Fachregel- und 321 Schutz-Trigger
- rund 292.500 Zeilen relevanter Eigencode
- 4.187 bestandene Backend-Testfälle, 15 übersprungen, 19 reine
  Teardown-Artefakte ohne fachlichen Fehlschlag
- ZUGFeRD/Factur-X für sechs Belegformen extern auf PDF/A-3B und EN16931
  validiert
- RAG/Wissensbasis geplant, noch ohne pgvector, Vektorindex oder
  Ähnlichkeitssuche
- geschätzter Aufwand bis zur externen Pilotfähigkeit: 15–27 Personentage
