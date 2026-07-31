# Businessplan MCN / Mitra

> Evidenzbasierter Arbeitsentwurf, Stand 28.07.2026  
> Noch einzutragen: Gründerprofil, Rechtsform, Standort, Marktvalidierung,
> Preisbereitschaft, Finanzplanung und konkretes Förderprogramm.

## 1. Zusammenfassung

MCN ist eine KI-first Unternehmenssoftware für SHK-Betriebe und
Gebäudeserviceunternehmen. Sie verbindet Kunden- und Objektdaten,
Auftragsabwicklung, Planung, Dokumentation, Artikel- und Preismanagement,
Abrechnung, Buchhaltungsvorbereitung, Personalprozesse sowie Wartungs- und
Fristenmanagement in einem gemeinsamen Arbeitskontext. Der erzeugte
OpenAPI-Vertrag umfasst aktuell 333 Pfade mit 405 Operationen und 573 Schemata;
die fachliche Datenbank besteht aus 161 Tabellen in 18 Schemata.

Der Ausgangspunkt ist die praktische Erfahrung in einem realen SHK-Betrieb.
MCN wird nicht allein anhand theoretischer Anforderungen entwickelt, sondern
bereits mit echten Kunden- und Betriebsdaten eingesetzt. Der aktuelle Stand
umfasst unter anderem einen Artikelbestand von rund zwei Millionen Datensätzen,
eine produktive Webanwendung, objektbezogene Auftragsprozesse, Angebote und
Rechnungen, ZUGFeRD/Factur-X, DATEV-Export, Wartungslogik sowie erste
KI-Workflows.

Die ZUGFeRD/Factur-X-Ausgabe wurde für sechs Belegformen extern mit veraPDF
1.30.2 auf PDF/A-3B und mit Mustang 2.24.0 gegen das
EN16931-Schematron geprüft, jeweils ohne Verstoß. Eine vollständige
XRechnung-/B2G-Fähigkeit wird damit nicht behauptet.

Die Differenzierung liegt in der Verbindung von tiefem Branchen- und
Gebäudekontext mit lokal betreibbarer KI. Die KI erhält keinen unkontrollierten
Sonderzugriff. Sie erzeugt Vorschläge, die dieselben Rechte, Statusautomaten,
Freigaben, Vier-Augen-Verfahren und Auditregeln durchlaufen wie menschliche
Aktionen.

Die nächste Unternehmensphase dient nicht mehr dem Beweis, dass die
Anwendungsdomäne technisch abbildbar ist. Ziel ist die **Produktisierung und
Marktvalidierung**: reproduzierbares Onboarding, externe Pilotbetriebe,
messbarer Kundennutzen, ein skalierbares Betriebsmodell und ein tragfähiges
Lizenz- und Serviceangebot.

Die wichtigste Pilotbarriere ist organisatorisch: Ein Kundenadministrator kann
heute keine Benutzer selbst anlegen oder einladen. Zusammen mit Datenimport,
kontrolliertem Mailversand, Offsite-Backup und Restore-Probe, automatischem
Release-Gate sowie geführter Ersteinrichtung werden etwa 15–27 Personentage bis
zur externen Pilotfähigkeit veranschlagt.

## 2. Problem und Bedarf

Viele SHK- und Gebäudeservicebetriebe bearbeiten einen Auftrag über mehrere
Medien und Systeme hinweg. Telefonnotizen, E-Mails, Kalender, Kundendaten,
Objektinformationen, Fotos, Herstellerunterlagen, Materialpreise,
Arbeitsberichte und Abrechnung sind häufig nur lose miteinander verbunden.

Daraus entstehen typische Belastungen:

- Informationen werden mehrfach erfasst oder manuell übertragen.
- Disposition und Monteure suchen nach aktuellen Objekt-, Bewohner- oder
  Anlagendaten.
- Erfahrungswissen bleibt bei einzelnen Mitarbeitenden.
- Material-, Zeit- und Leistungsdaten müssen nach dem Einsatz rekonstruiert
  werden.
- Wiederkehrende Wartungen und Fristen sind abhängig von manueller Pflege.
- Generische CRM-Systeme kennen Kunden und Vorgänge, aber nicht die fachliche
  Struktur aus Liegenschaft, Gebäude, Einheit, Bewohner, Anlage, Eigentümer und
  Verwaltung.
- Cloud-KI ist bei Kunden-, Gesundheits-, Personal- und Gebäudedaten aus
  Datenschutz- und Kontrollgründen nicht immer akzeptabel.
- Unkontrollierte KI-Agenten können Fachregeln, Berechtigungen und
  Freigabeprozesse umgehen.

Der wirtschaftliche Bedarf besteht daher nicht nur in „mehr Digitalisierung“,
sondern in einer durchgängigen, fachlich kontrollierten Informationskette.

## 3. Lösung und Produkt

MCN bildet den betrieblichen Ablauf als zusammenhängenden Arbeitskontext ab:

```text
Kontakt / Liegenschaft / Anlage
              ↓
    Vorgang / Projekt / Auftrag
              ↓
    Planung / Einsatz / Bericht
              ↓
  Zeit / Material / Kalkulation
              ↓
 Angebot / Rechnung / Zahlung / Mahnung / DATEV
```

### Operativer Kern

Die Software führt Kontakt-, Objekt- und Anlagendaten mit Projekten, Aufträgen,
Einsätzen, Aufgaben, Terminen, Dateien und Berichten zusammen. Plantafel,
Kalender, Schnellerfassung und mobile-orientierte Einsatzprozesse unterstützen
Disposition und Außendienst.

### Kaufmännischer Kern

Artikel- und Lieferantendaten, DATANORM, IDS-Connect, Preislogik,
Angebotskalkulation, Rechnungen, Abschläge, Schlussrechnungen, Storno,
Zahlungen, Mahnwesen und DATEV-Export bilden eine zusammenhängende Kette.
Rechtlich und finanziell sensible Zustände werden durch Datenbankregeln und
Freigaben abgesichert.

### Wartung und Wissen

Wartungsverträge, Prüfungen, Gewährleistungen und Fälligkeiten erzeugen
systematisch Folgeaktivitäten. Eine geplante Wissensbasis soll
Herstellerunterlagen, interne Abläufe und freigegebene Projektdokumente im
jeweiligen Rechtekontext auffindbar machen.

Die Wissensbasis ist ausdrücklich kein heutiges Produktmerkmal: Eine
Embedding-Tabelle existiert, pgvector, Vektorindex und Ähnlichkeitssuche sind
noch nicht implementiert.

### KI-first

Die KI wird nicht als Chatfenster an ein fertiges CRM angehängt. Sie kann
Briefings, Entwürfe und strukturierte Vorschläge aus dem vorhandenen
Arbeitskontext erzeugen. Dabei gilt:

- Verarbeitung kann lokal auf eigener Hardware erfolgen.
- Das Modell ist austauschbar.
- Wahrnehmungsdaten aus Sprache, OCR oder E-Mail gelten als nicht vertrauenswürdig.
- KI schreibt nicht direkt in Fachdaten.
- Menschen behalten Freigabe und Verantwortung.
- jeder Lauf und jede Ausführung bleibt nachvollziehbar.

Ein Beispiel ist der vorbereitete Ablauf vom Sprachmemo zum strukturierten
Baustellenbericht. Der technische Workflow ist vorhanden; der vollständige
Nachweis mit real angebundenem ASR-Gerät ist Teil der nächsten Phase.

## 4. Kundennutzen

### Zeit

MCN vermeidet wiederholte Erfassung und verkürzt die Suche nach Informationen,
indem Telefonaufnahme, Objektkontext, Einsatzdaten, Dokumente und
kaufmännische Folgeprozesse verbunden werden.

### Qualität

Pflichtangaben, Statusübergänge, Preislogik und Belegregeln werden nicht nur in
Schulungsunterlagen beschrieben, sondern technisch erzwungen. Das reduziert
unvollständige Datensätze und fachlich ungültige Prozessschritte.

### Planbarkeit

Wartungen, Prüfungen, Gewährleistungen und Aufgaben werden aus strukturierten
Daten abgeleitet. Fälligkeiten hängen weniger von persönlicher Erinnerung ab.

### Datenschutz und Kontrolle

Lokaler Betrieb und lokale KI ermöglichen die Verarbeitung sensibler
Betriebsdaten ohne zwingende Übertragung an externe KI-Clouds. Rechte,
Vier-Augen-Verfahren und Audit gelten auch für KI-initiierte Arbeit.

### Mitarbeiterakzeptanz

Die Informationsarchitektur orientiert sich an bekannten Arbeitsmustern
etablierter Handwerkersoftware, wird jedoch als moderner, eigenständiger
Leitstand umgesetzt. Das soll die Umstellung erleichtern, ohne alte
Oberflächenkonzepte unverändert zu kopieren.

## 5. Zielkunden

### Primäres Einstiegssegment

Kleine und mittlere SHK-Betriebe sowie Gebäudeserviceunternehmen mit:

- mehreren Büro- und Außendienstmitarbeitenden,
- wiederkehrenden Wartungen oder Objektgeschäft,
- relevantem Anteil an Mehrfamilienhäusern, WEGs oder verwalteten Objekten,
- hohem Telefon-, Dokumentations- und Dispositionsaufwand,
- DATANORM-/IDS-Nutzung,
- Interesse an KI bei gleichzeitig hohem Datenschutzbedürfnis.

### Wirtschaftlicher Käufer

Typische Entscheider sind Inhaber, Geschäftsführung, Betriebsleitung oder
kaufmännische Leitung. Nutzer sind Disposition, Sachbearbeitung, Monteure,
Serviceleitung und Buchhaltung.

### Spätere Erweiterung

Nach Validierung im SHK-Kern kann das objektzentrierte Modell auf angrenzende
Gewerke und Gebäudedienstleister übertragen werden. Diese Erweiterung darf
nicht vor einer klaren Produkt-Markt-Passung im Kernsegment erfolgen.

## 6. Markt und Wettbewerb

Der Wettbewerb besteht aus:

1. etablierter Handwerkersoftware/ERP mit breiter Funktionsabdeckung,
2. modernen Cloud-Lösungen für Auftragsabwicklung und Disposition,
3. horizontalen CRM-, Dokumenten- und Buchhaltungssystemen,
4. Einzellösungen für Wartung, Zeiterfassung, Baustellendokumentation oder KI.

MCN sollte nicht mit „hat ebenfalls Kontakte, Angebote und Rechnungen“
positioniert werden. Die relevante Abgrenzung ist:

- tiefer Objekt- und Anlagenkontext,
- zusammenhängende operative und kaufmännische Prozesskette,
- lokale, austauschbare KI,
- kontrolliertes Vorschlags- statt unbeschränktes Agentenmodell,
- Datenbanktore, Audit und Vier-Augen-Prinzip,
- Entwicklung aus realer SHK-Praxis.

Eine belastbare Marktgrößen- und Wettbewerbsanalyse wird separat mit aktuellen,
belegten Quellen erstellt. Produktbehauptungen und Marktstatistiken dürfen
nicht vermischt werden.

## 7. Geschäftsmodell

Das Geschäftsmodell ist noch durch Kundeninterviews zu validieren. Als
Arbeitshypothese eignet sich:

- einmalige Einrichtungs-, Datenmigrations- und Schulungspauschale,
- monatliche Grundlizenz je Betrieb/Instanz,
- nutzer- oder rollenbezogene Staffel,
- optionale Module für lokale KI, Wartung, erweitertes Dokumentenwesen oder
  Schnittstellen,
- Servicevertrag für Betrieb, Backup, Updates und Support,
- optional Hardware-/Appliance-Paket für lokale KI.

### Zu prüfende Preislogik

Nicht nur „Preis pro Nutzer“ testen. Der Nutzen hängt auch an Auftragsvolumen,
Objektbestand, Wartungsverträgen und eingespartem Verwaltungsaufwand. Drei
Pakete könnten in Pilotinterviews gegeneinander getestet werden:

1. **Softwarebetrieb durch MCN**, getrennte Kundeninstanz;
2. **Managed Appliance** beim Kunden;
3. **Self-hosted Lizenz** für technisch betreute größere Betriebe.

Vor Aufnahme konkreter Preise in den finalen Businessplan werden mindestens
10–15 strukturierte Gespräche und mehrere schriftliche Pilotabsichten
empfohlen.

## 8. Markteintritt

### Phase 1: Referenzbetrieb messbar machen

- fünf bis sieben Kernprozesse mit Vorher-/Nachher-Kennzahlen dokumentieren
- echte Nutzerrollen beobachten
- KI-Qualität und Korrekturaufwand messen
- Support- und Onboardingaufwand protokollieren
- Datenschutz-, Backup- und Restore-Prozesse vervollständigen

### Phase 2: drei bis fünf Pilotbetriebe

- klar begrenztes Pilotpaket
- standardisierter Datenimport
- feste Einführung und Schulung
- wöchentliche Produktgespräche
- schriftliche Erfolgskriterien
- möglichst bezahlter Pilot oder verbindliche Kaufoption

### Phase 3: standardisierter Vertrieb

- Branchen-Netzwerk, Innungen, Großhandel und Fachpartner
- Referenzfälle mit messbaren Ergebnissen
- produktbezogene Demonstration entlang eines echten Auftrags
- wiederholbares Onboarding und Supportmodell

Die Demonstration sollte nicht bei einem KI-Chat beginnen. Stärker ist ein
durchgängiger Fall: Telefonauftrag → Objekt/Anlage → Einsatz → Sprachmemo/
Bericht → Material/Preis → Rechnung → Wartungsfolge.

## 9. Technologie und Schutzposition

Der technologische Kern besteht aus PostgreSQL, Django, Angular, MinIO und
lokal betreibbaren Modelladaptern. Die Architektur ist nicht von einem
einzelnen proprietären KI-Anbieter abhängig.

Die Schutzposition entsteht weniger durch klassische Patentierbarkeit als
durch:

- tiefes, getestetes Branchenmodell,
- dokumentierte Fachinvarianten und Grenzfälle,
- reale Artikel-, Preis- und Lieferantenprozesse,
- Integrationswissen zu DATANORM, IDS, DATEV und E-Rechnung,
- Produktwissen aus dem laufenden SHK-Betrieb,
- wachsende Datenbasis für Prozess- und Qualitätsverbesserung,
- kontrollierte KI-Orchestrierung im Fachkontext.

## 10. Datenschutz, Sicherheit und Compliance

Bereits technisch adressiert sind:

- rollenbezogene Rechte und begrenzte Sichten,
- Audit und No-Delete-Regeln,
- unveränderliche bzw. eingefrorene Belegzustände,
- Storno statt Löschung kaufmännischer Belege,
- Vier-Augen-Verfahren,
- verschlüsselte Zugangsdaten,
- lokaler Objektspeicher,
- lokale KI-Datengrenze,
- E-Rechnung und DATEV-Export,
- Backup- und Restore-Konzept.

Noch organisatorisch bzw. extern zu validieren sind:

- GoBD-Verfahrensdokumentation,
- Datenschutz-Folgenabschätzung je Betriebsmodell,
- Auftragsverarbeitungsverträge,
- Lösch- und Aufbewahrungskonzept,
- KI-Risikoklassifizierung und Pflichten aus geltendem EU-Recht,
- externer Security-Test,
- Restore-Probeläufe und Offsite-Sicherung.

Der Businessplan bezeichnet MCN bis zur externen Prüfung nicht pauschal als
„rechtssicher“ oder „zertifiziert“.

## 11. Entwicklungs- und Produktroadmap

### Meilenstein 1 – Referenzreife

- offene Datenmodell-Lücken schließen
- vollständige Release-Verifikation
- Offsite-Backup und Restore-Nachweis
- KI/ASR-End-to-End
- Prozessmessung im Referenzbetrieb

### Meilenstein 2 – Pilotreife

- Benutzeranlage/-Einladung und geführtes Onboarding
- Datenimport und kontrolliert aktivierbarer Mailversand
- automatisiertes Release-Gate
- automatisierte Bereitstellung einer Kundeninstanz
- Import-/Migrationswerkzeuge
- Support-, Monitoring- und Updateprozess
- Vertrags-, Datenschutz- und Betriebsunterlagen

### Meilenstein 3 – Marktreife

- drei bis fünf erfolgreiche Pilotbetriebe
- validierte Pakete und Preise
- standardisierte Schulung
- belastbare Referenzkennzahlen
- wiederholbarer Vertrieb

### Meilenstein 4 – Skalierung

- automatisierte Flottenverwaltung getrennter Instanzen oder begründete
  Multi-Tenant-Entscheidung
- Partner- und Einführungskanal
- Service-Level und Supportorganisation
- Ausbau Wissensbasis/RAG im Rechtekontext
- Übertragung auf angrenzende Gewerke nach Nachweis im SHK-Kern

## 12. Förderprojekt

Ein förderfähiges Vorhaben sollte nicht als „allgemeine Weiterentwicklung eines
CRM“ beschrieben werden. Der innovative und risikobehaftete Kern ist:

> Entwicklung und betriebliche Validierung einer lokal betreibbaren,
> kontrollierten KI-Orchestrierung für durchgängige SHK-/Gebäudeserviceprozesse,
> bei der KI-Vorschläge denselben fachlichen, datenschutzbezogenen und
> revisionsrelevanten Regeln unterliegen wie menschliche Aktionen.

Mögliche Arbeitspakete:

1. lokale multimodale Erfassung aus Sprache, Dokument und Bild,
2. strukturierte Kontextbildung aus Objekt, Anlage und Auftrag,
3. sichere Vorschlags- und Freigabelogik,
4. rechtegebundene Wissensbasis,
5. Qualitäts-, Fehler- und Laufzeitmessung,
6. Pilotierung in mehreren unabhängigen Betrieben,
7. Produktisierung für reproduzierbaren lokalen oder isolierten Betrieb.

Technische Risiken, die eine Förderung begründen können:

- ausreichende Qualität kleiner lokaler Modelle,
- robuste Strukturierung unvollständiger Baustellensprache,
- Schutz gegen fehlerhafte oder manipulierte Eingangsdaten,
- Rechteerhalt beim semantischen Abruf,
- wirtschaftlicher Betrieb lokaler Modelle,
- Übertragbarkeit aus einem Referenzbetrieb auf andere Betriebsabläufe.

## 13. Organisation und Personal

Hier fehlen für die finale Fassung:

- Gründername, Ausbildung und Berufserfahrung,
- Rolle und Erfahrung im SHK-Betrieb,
- bisheriger Entwicklungsaufwand und Eigenmittel,
- geplante Rechtsform und Beteiligungsverhältnisse,
- benötigte Rollen für Entwicklung, Vertrieb, Einführung, Datenschutz und
  Support,
- externe Partner und deren verbindlicher Status.

Besonders glaubwürdig ist die Kombination aus Domänenpraxis und technischer
Umsetzung. Der Plan sollte konkret belegen, welche betrieblichen Probleme aus
eigener Erfahrung stammen und welche fachlichen Entscheidungen daraus
entstanden sind.

## 14. Finanzplanung – benötigte Eingaben

Für die Dreijahresplanung werden mindestens benötigt:

| Eingabe | Einheit |
|---|---|
| verfügbare Eigenmittel | Euro |
| bisherige Entwicklungsstunden/-kosten | Stunden/Euro |
| Gründerlohn bzw. Unternehmerlohn | Euro/Monat |
| geplante Mitarbeitende je Jahr | FTE und Kosten |
| Pilotpreis und regulärer Preis | Euro |
| Einrichtungs-/Migrationspreis | Euro |
| Hardwarekosten lokale KI | Euro je Installation |
| Hosting-, Backup- und Monitoringkosten | Euro je Kunde/Monat |
| Supportaufwand | Stunden je Kunde/Monat |
| Vertriebs- und Einführungskosten | Euro je Neukunde |
| erwartete Kunden je Quartal | Anzahl |
| Kündigungsrate | Prozent |
| Förderbetrag und Eigenanteil | Euro/Prozent |

Danach entstehen:

- Umsatzplanung nach Einmal- und wiederkehrenden Erlösen,
- Personal- und Betriebskostenplanung,
- Liquiditätsplan,
- Rentabilitätsvorschau,
- Break-even-Szenario,
- konservatives, realistisches und ambitioniertes Szenario.

## 15. Risiken und Gegenmaßnahmen

| Risiko | Gegenmaßnahme |
|---|---|
| zu breite Produktfläche | Pilotpaket und wenige messbare Kernprozesse |
| internes Werkzeug lässt sich schwer übertragen | externe Piloten und standardisiertes Onboarding |
| hoher Support je Einzelinstanz | automatisierte Provisionierung, Monitoring und Updates |
| etablierter Wettbewerb | Differenzierung über Objektkontext und kontrollierte lokale KI |
| KI liefert plausible Fehler | Vorschlagsprinzip, Freigabe, Provenienz und Messung |
| lokale Hardware ist teuer/komplex | abgestufte Hardwareprofile und kleine spezialisierte Modelle |
| Compliance wird überschätzt | externe Prüfung und vorsichtige Produktclaims |
| Abhängigkeit von einer Person | Dokumentation, Tests, Releaseprozess und Teamaufbau |
| fehlende Marktbereitschaft | bezahlte Piloten und Preisinterviews vor Skalierungsinvestition |

## 16. Nächste Entscheidungen

Für eine einreichungsfähige Fassung müssen als Nächstes geklärt werden:

1. Wird eine neue Gesellschaft gegründet oder das Produkt aus einem bestehenden
   Betrieb heraus vermarktet?
2. Wer gehört zum Gründerteam und welche Rollen sind besetzt?
3. Welches Betriebsmodell soll zuerst verkauft werden?
4. Welche drei Kernprozesse bilden das Pilotprodukt?
5. Welche externen Betriebe stehen für Interviews oder Piloten zur Verfügung?
6. Welche Preisannahmen gibt es bereits?
7. Welches Förderprogramm und welcher Förderzweck werden konkret adressiert?
8. Wie viel Kapital und Arbeitszeit stehen in den nächsten 24 Monaten zur
   Verfügung?
