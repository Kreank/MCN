# Technische Produktanalyse MCN

> Arbeitsstand: 28.07.2026  
> Zweck: belastbare Tatsachengrundlage für Businessplan, Förderanträge und
> Produktstrategie. Maßgeblich sind Quellcode, Migrationen und aktueller
> Projektstand; ältere Roadmap-Angaben können überholt sein.

## 1. Kurzurteil

MCN ist kein reines Gründungskonzept und kein UI-Prototyp. Der Quellcode bildet
bereits einen großen Teil der kaufmännischen und operativen Prozesskette eines
SHK-/Gebäudeservicebetriebs ab. Das System läuft im Echtbetrieb mit echten
Kunden- und Betriebsdaten sowie einem Artikelbestand von rund zwei Millionen
Datensätzen.

Die stärkste technische Besonderheit ist nicht ein einzelnes KI-Feature, sondern
die Verbindung aus:

1. einem tiefen, objekt- und auftragsbezogenen SHK-Domänenmodell,
2. physisch in PostgreSQL durchgesetzten Fachregeln,
3. einem KI-Vorschlagsmodell ohne direkten Schreib-Sonderweg,
4. lokaler Datenhaltung und lokal betreibbarer KI,
5. durchgängigen Arbeitskontexten von Kontakt und Liegenschaft bis zu Auftrag,
   Einsatz, Dokument, Rechnung und Nachbearbeitung.

Das Produkt ist heute am treffendsten als **betriebsfähiger Vertical-Software-
Kern mit frühem KI-Layer** zu beschreiben. Für den Verkauf an viele unabhängige
Kunden fehlen noch Produktisierung, standardisiertes Onboarding, belastbare
Mehrkunden-Betriebsprozesse und ein validiertes Geschäftsmodell.

## 2. Nachweisbarer Entwicklungsstand

Die Größenangaben sind eine Momentaufnahme des Repositories:

| Indikator | Stand |
|---|---:|
| relevanter Eigencode | ca. 292.500 Zeilen |
| Python-Dateien | 474 |
| TypeScript-Dateien | 254 |
| Datenbank | 161 Tabellen in 18 Schemata |
| Datenbankregeln | 168 Fachregel- und 321 Schutz-Trigger |
| weitere DB-Sicherungen | 660 CHECKs, 17 EXCLUDEs, 341 PL/pgSQL-Funktionen |
| Backend-Testdateien | 187 |
| Frontend-Testdateien | 22 |
| OpenAPI | 333 Pfade, 405 Operationen, 573 Schemata |
| sichtbare Frontend-Featurebereiche | über 80 |

Diese Zahlen belegen Aufwand und Breite, nicht automatisch Qualität. Beim
vollständigen Lauf am 28.07.2026 bestanden 4.187 Backend-Testfälle; 15 wurden
übersprungen. Die 19 ausgewiesenen Fehler waren ausschließlich
Teardown-Artefakte, weil Djangos `flush` an den absichtlich eingerichteten
No-Truncate-Triggern scheitert; es gab keinen fachlichen Testfehlschlag. Der
Produktionsbuild des Frontends war erfolgreich und enthielt eine
Budget-Warnung für `angebot-editor.scss`. Einschränkend bleibt, dass diese
vollständige Suite vor dem letzten Produktiv-Deployment nicht ausgeführt wurde.

## 3. Bereits umgesetzte Produktbereiche

### Kunden, Objekte und technischer Anlagenkontext

- Personen und Organisationen, Ansprechpartner, Kontaktwege und Adressen
- Liegenschaften, Gebäude, Einheiten, Räume und Anlagen
- Eigentum, Belegung/Mieter, Verwaltung und Vollmachten
- objektbezogene Gebäudeansicht mit Etagen, Einheiten, Bewohnern und Technik
- Dublettenprüfungen und strukturierte Schnellaufnahme
- Dateien, Bilder und Dokumente in den jeweiligen Arbeitsmappen

**Praktischer Nutzen:** Kunden-, Objekt-, Bewohner- und Anlagendaten bleiben nicht
in getrennten Listen. Mitarbeitende können einen Auftrag im Kontext des
tatsächlichen Gebäudes, der betroffenen Einheit, der Anlage und der
zuständigen/bewohnenden Personen bearbeiten.

### Operative Auftragsabwicklung

- Projekte, Vorgänge und konfigurierbare Pipeline
- Aufträge und Einsätze
- Plantafel, Kalender, Terminserien, Kategorien und Ressourcen
- Aufgaben, Checklisten und Logbuch
- Zeit- und Materialerfassung
- Baustellenberichte einschließlich Unterschrift und PDF
- Raumaufmaß, Grundriss-, Heizlast- und Bauteilfunktionen
- schneller Telefonauftrag/Schnellerfassung

**Praktischer Nutzen:** Informationen werden nicht erst am Ende aus Telefon,
Papierzettel, Kalender und Einzeldateien zusammengetragen. Sie entstehen im
gemeinsamen Vorgangs- und Objektkontext.

### Angebot, Abrechnung und Buchhaltung

- Angebotserstellung mit Positionen und Kalkulation
- Rechnungen einschließlich Abschlag, Schlussrechnung, Storno und Korrektur
- eingefrorene Abrechnungs-Snapshots
- PDF-Ausfertigung und Archivierung
- ZUGFeRD/Factur-X-Ausgabe
- offene Posten, Zahlungen, Skonto, Mahnstufen und Mahnlauf
- DATEV-EXTF-Export
- Eingangsbelege, Sachkonten und Kostenstellen
- Vier-Augen-Freigaben für sensible Vorgänge

**Praktischer Nutzen:** Die Prozesskette endet nicht bei der
Auftragsdokumentation. Leistungsdaten können in kaufmännische Dokumente und
nachgelagerte Buchhaltungsprozesse überführt werden, ohne die Fachlogik in
unverbundene Systeme zu verteilen.

Die E-Rechnungsausgabe ist über Eigentests hinaus belegt: veraPDF 1.30.2
bestätigte PDF/A-3B und Mustang 2.24.0 das EN16931-Schematron für sechs
Belegformen ohne Verstoß. XRechnung/B2G darf daraus nicht als vollständig
umgesetzt abgeleitet werden.

### Artikel, Preise und Lieferanten

- umfangreicher Artikel- und Leistungsstamm
- DATANORM-Import mit Sicherheitsprüfungen und Dry-run
- Lieferantenanbindungen und IDS-Connect-Warenkorb
- Lieferanten- und Verkaufspreise
- Aufschlagsmatrizen, Preisgruppen, Lohn- und Verrechnungssätze
- Baugruppen und Bauteilkatalog
- Geräte-/Ersatzteilsuche

**Praktischer Nutzen:** Angebotspositionen und Materialkalkulation greifen auf
branchenübliche Lieferantendaten zurück. Einkaufspreis, Listenpreis,
Verkaufspreislogik und der konkrete Händlerwarenkorb werden fachlich getrennt.

### Personal und Organisation

- Login, Rollen und Rechtematrix
- Mitarbeitende, Arbeitsverträge und Qualifikationen
- Urlaub, Abwesenheiten, Atteste und Urlaubsbudgets
- Zeitkategorien, Stempeluhr, Arbeitszeitprüfung und Stundenkonto
- Firmenprofil, Niederlassungen, Gewerke, Mail- und
  Buchhaltungseinstellungen

### Wartung und Fristen

- Wartungsverträge mit Objekt- und Anlagenbezug
- automatische Auslösung von Aufgabe, Projekt oder Auftrag
- Prüfungen, Gewährleistungen und zentrale Fälligkeiten
- Scheduler für wiederkehrende und fristgebundene Vorgänge

**Praktischer Nutzen:** Wiederkehrendes Geschäft und Fristen sind nicht von
persönlichen Kalendern oder Erinnerungen einzelner Mitarbeitender abhängig.

## 4. KI: umgesetzt, vorbereitet und noch offen

### Umgesetzt

- modellagnostischer LLM-Adapter
- KI-Läufe und Vorschläge mit nachvollziehbarem Status
- Vorschläge genehmigen, ablehnen und kontrolliert ausführen
- KI-Assistent mit Gesprächsverlauf
- Dossiers für Kontakt, Liegenschaft, Projekt und Auftrag
- Leitstand-Briefing
- Tool-Registry, Queue, Leasing, Retry und Reaper
- Workflow für Sprachmemo zu strukturiertem Bericht
- Schutz unzuverlässiger/externer Inhalte als `untrusted`
- lokale Datenklassifikation (`LOCAL_ONLY`)

### Architektonische Stärke

Die KI schreibt nicht direkt in Fachtabellen. Sie erzeugt einen Vorschlag ohne
fachliche Wirkung. Erst die kontrollierte Ausführung durchläuft dieselben
Services, Rechte, Statusautomaten, Freigaben, Versionstests und DB-Trigger wie
eine menschliche Aktion. Damit wird KI nicht als privilegierter Nebenweg
behandelt.

Diese Aussage ist förder- und investorenrelevant, weil sie das zentrale Problem
agentischer Unternehmenssoftware adressiert: Automatisierung darf Governance,
Rechte und Nachvollziehbarkeit nicht umgehen.

### Noch nicht ausreichend nachgewiesen

- vollständiger Live-Durchklick der KI-Strecken mit realem Modellprofil
- Sprachmemo-Workflow mit einem tatsächlich angebundenen ASR-Gerät
- Wissensbasis/RAG: nur geplant; `ai.embedding` ist als Tabelle vorhanden,
  aber pgvector ist nicht installiert, die Vektorspalte ist `real[]`, und es
  existieren weder Vektorindex noch Ähnlichkeitssuche
- messbare Erkennungsqualität und Zeitersparnis im Betriebsalltag
- standardisierte Hardware- und Betriebsprofile für Kundeninstallationen

Der Businessplan darf daher nicht behaupten, die lokale KI sei bereits in allen
Prozessen produktiv validiert. Belastbar ist: Die Governance- und
Orchestrierungsgrundlage ist implementiert; einzelne End-to-End-Nachweise und
die Breitenvalidierung stehen aus.

## 5. Architektur und Skalierbarkeit

### Heute

- PostgreSQL 16 als fachliche Quelle der Wahrheit
- Django 5 und django-ninja mit OpenAPI
- Angular als eigenständiger Leitstand
- MinIO für Dateien und archivierte Ausfertigungen
- Containerbetrieb hinter nginx
- Scheduler und Queue-Worker
- lokaler, austauschbarer KI-Endpunkt
- produktiver Single-Tenant-Betrieb

### Was gut skaliert

- klare Trennung von UI, API, Services, DB-Regeln und Objektspeicher
- stateless ausbaubares Web-Backend
- OpenAPI als Grundlage weiterer Clients
- Queue-/Lease-Muster für asynchrone KI-Arbeit
- versionierte Migrationen und reproduzierbare Container-Builds
- abgeleitete statt redundant gespeicherte Finanz- und Zeitwerte
- eingefrorene Snapshots für rechtlich relevante Dokumente

### Was für Tausende Kunden noch fehlt

MCN ist ausdrücklich Single-Tenant. Tausende Kunden sind deshalb nicht durch
bloßes Hochskalieren eines Servers erreichbar. Vor einer SaaS-Zusage ist eine
Produktentscheidung nötig:

1. **isolierte Instanz je Kunde:** hohe Datenisolation und lokale KI-Option,
   dafür aufwendigere Provisionierung, Updates und Monitoring;
2. **echte Multi-Tenant-Plattform:** effizienterer zentraler Betrieb, dafür
   systemweiter Umbau von Datenmodell, Rechteprüfung, Indizes, Storage,
   Migration und Support;
3. **hybrides Modell:** zentrale Verwaltung/Updates, getrennte Kundeninstanzen
   oder regionale Appliances.

Für die aktuelle Datenschutz- und Lokal-KI-Positionierung erscheint ein
automatisiertes Single-Tenant-/Appliance-Modell als naheliegende erste
Produktisierungsstufe. Diese Empfehlung muss mit Zielkunden getestet werden.

## 6. Tatsächlich gelöste Probleme

### Medienbrüche

Telefonaufnahme, Kontakte, Objektstruktur, Ausführung, Fotos, Bericht,
Material, Angebot und Rechnung können in einem gemeinsamen fachlichen Kontext
geführt werden. Die Software reduziert damit manuelle Übertragung und die Suche
nach verteilt abgelegten Informationen.

### Fehlender Objektkontext in generischen CRMs

MCN modelliert nicht nur „Kunde und Auftrag“, sondern Gebäude, Einheiten,
Bewohner, Anlagen, Eigentum und Verwaltung. Das ist besonders für Wartung,
Mehrfamilienhäuser, WEGs und Gebäudeservice relevant.

### Fehleranfällige kaufmännische Übergaben

Preislogik, Belegstatus, Abschläge, Schlussrechnungen, Storno, Zahlungen,
Mahnwesen und DATEV-Export sind durch fachliche Regeln miteinander verbunden.
Mehrere kritische Werte werden abgeleitet oder beim rechtlich relevanten
Zeitpunkt eingefroren.

### Wissens- und Erinnerungsabhängigkeit

Wartungsintervalle, Prüfungen, Gewährleistungen, Aufgaben und Fristen werden
systematisch ausgelöst. Der operative Betrieb hängt dadurch weniger von
Einzelpersonen und privaten Kalendern ab.

### Unsichere KI-Integration

Lokale Verarbeitung, modellagnostische Adapter, Datenklassifikation,
Vorschlagsprinzip, Vier-Augen-Flow und Audit schaffen eine technische Basis,
auf der KI in sensiblen Betriebsdaten eingesetzt werden kann, ohne ihr direkten
Schreibzugriff zu geben.

## 7. Schärfster möglicher USP

> MCN verbindet den vollständigen Objekt- und Auftragskontext eines
> SHK-/Gebäudeservicebetriebs mit lokal betreibbarer KI, die nicht am
> Regelwerk vorbeiarbeitet, sondern dieselben fachlichen und rechtlichen Tore
> durchläuft wie ein Mensch.

Der USP besteht aus der Kombination. „CRM für Handwerker“, „lokale KI“ oder
„GoBD-konforme Rechnungen“ sind einzeln keine ausreichende Differenzierung.

## 8. Produktlücken mit hoher Priorität

### Vor externer Pilotierung

- Benutzeranlage/-einladung: aktuell existieren weder Endpunkt noch
  Bedienoberfläche; neue Benutzer entstehen nur administrativ
- vollständige Testsuite und wiederholbare Release-Gates
- off-box Backup und dokumentierter Restore-Probelauf
- echtes KI-/ASR-End-to-End-Szenario
- kontrolliert aktivierbarer Mailversand
- Datenübernahme und geführtes Betriebs-Onboarding
- Telemetrie für Prozessdauer, Fehler und KI-Qualität
- Bereinigung offener Objektmodell-Lücken (u. a. Gebäudeadresse,
  Eigentümer-Doppelstruktur, Mieter in Schnellaufnahme)
- klare Support-, Update- und Datenexportprozesse

Der derzeit geschätzte Produktisierungsaufwand bis zur externen Pilotfähigkeit
liegt bei etwa 15–27 Personentagen. Die anschließende Skalierungsarbeit wird je
nach Betriebsmodell auf weitere 20–120 Personentage geschätzt.

### Vor Skalierung

- Ziel-Betriebsmodell festlegen
- automatisierte Instanzbereitstellung und Updates
- Monitoring, Fehlerberichte und Rollback
- Migrationsstrategie je Kundenversion
- Lizenz-, Rollen- und Abrechnungsverwaltung
- standardisierte Importpfade aus verbreiteten Quellsystemen
- dokumentierte Lösch-, Aufbewahrungs- und AVV-Prozesse
- CI spätestens bei mehreren Mitwirkenden bzw. häufigeren Deployments

## 9. Wahrscheinliche Fragen von Förderstellen und Investoren

1. Welche Funktionen sind im Echtbetrieb, welche nur technisch vorbereitet?
2. Wie viele externe Betriebe haben das Produkt getestet oder zahlend bestellt?
3. Welche Zeitersparnis ist je Kernprozess messbar?
4. Ist das System ein internes Werkzeug oder ein reproduzierbares Produkt?
5. Wie werden Installation, Updates, Support und lokale KI beim Kunden betrieben?
6. Wie grenzt es sich von etablierten Handwerker-ERP-/CRM-Systemen ab?
7. Welche Daten und Schnittstellen verhindern einen Anbieter-Lock-in?
8. Wie werden Datenschutz, GoBD, E-Rechnung und KI-Regulierung organisatorisch
   abgesichert, nicht nur technisch?
9. Wie hoch sind Entwicklungs-, Hardware-, Betriebs- und Supportkosten je Kunde?
10. Welche Teile des Know-hows sind schwer nachbaubar?

## 10. Messplan für belastbare Nutzenversprechen

Vorher-/Nachher-Messungen in mindestens drei Betrieben:

| Prozess | Kennzahl |
|---|---|
| Telefonauftrag | Minuten bis vollständiger, disponierbarer Vorgang |
| Einsatzvorbereitung | Suchzeit nach Objekt-, Bewohner- und Anlagendaten |
| Baustellenbericht | Minuten von Sprachmemo/Feldeingabe bis prüfbarem Bericht |
| Angebot | Minuten und manuelle Übernahmen bis zum versandfertigen Angebot |
| Rechnung | Minuten vom abgeschlossenen Einsatz bis zur Ausfertigung |
| Material | Anteil korrekt zugeordneter Artikel/Preise ohne Nacharbeit |
| Wartung | übersehene bzw. verspätete Fälligkeiten |
| Datenqualität | Dubletten, fehlende Pflichtangaben, Korrekturen |
| KI | Annahmequote, Korrekturaufwand, Fehlerquote und Laufzeit je Vorschlag |

Erst diese Daten erlauben Aussagen wie „spart X Minuten“ oder „reduziert Fehler
um Y Prozent“. Bis dahin bleibt die belastbare Formulierung qualitativ:
MCN reduziert Medienbrüche und führt Informationen im Arbeitskontext zusammen.
