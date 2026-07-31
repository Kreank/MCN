# Funktionslücken — Tiefenrecherche 2026-07-31

> **Status: RECHERCHE, nichts davon umgesetzt.** Ausgangsfrage: Welche Funktionen
> der Klasse „Kunden-Terminbuchungslink" gibt es noch — also Dinge, die Dispo und
> Rechnungsstellung spürbar verbessern und die nur baubar sind, weil wir am
> Quelltext sitzen? Zwei Recherchen: Codebase (Ist-Stand) und Web (was Nutzer
> beklagen, was der Markt kann, was der Gesetzgeber verlangt).

## 0. Methode und ihre Grenzen — ehrlich

**Codebase:** drei parallele Recherchen über `backend/api`, `backend/db_core`,
`frontend/src/app/features` und die gesamte `docs/`-Ablage. Belastbar.

**Web:** ergiebig waren haustechnikdialog.de, bau.com-Forum, Capterra,
Trustpilot, ProvenExpert, sbz-online.de sowie die Hilfe-Dokumentation der
internationalen Anbieter.

⚠️ **Zwei Einschränkungen, die man beim Lesen mitdenken muss:**

1. **Reddit war nicht zugänglich** (Indexierungslücke/Fetch-Sperre). r/handwerk,
   r/Elektriker, r/HVAC, r/Plumbing fehlen also in der Auswertung. Das ist eine
   echte Lücke — dort steht erfahrungsgemäß das Ungeschminkteste.
2. **Viele Zahlen sind Herstellerangaben.** Wo eine Quelle vom Anbieter selbst
   stammt (openHandwerk-Blog, Stripe-Benchmarks, Hero-Erfahrungsseite), steht das
   unten dabei. Diese Zahlen sind als Richtung brauchbar, nicht als Beweis.

## 1. Die fünf größten Funde

Sortiert nach dem, was am meisten bringt. Alle fünf sind **intern belegt** und
**extern bestätigt** — das ist der Grund, warum sie oben stehen.

---

### 1.1 Gebuchtes Material landet in keiner Rechnung

**Der interne Befund.** `workflow.material_entry` wird vom Monteur am Einsatz
gefüllt (`einsatz_service.log_material`, `services/einsatz.py:519`) — **ohne
Preis und ohne jede Verbindung zum Rechnungswesen**. `BillingLink.source_kind`
(`models.py:4226`) kennt genau drei Herkünfte: `BERICHTSPOSITION`,
`ZEITBUCHUNG`, `ANGEBOTSPOSITION`. Material fehlt in dieser Aufzählung.

Damit gibt es **keinen** Weg von einem gebuchten Materialverbrauch in eine
Rechnungsposition. Abrechenbar wird Material nur, wenn es zusätzlich als
`SiteReportLine` (`line_type=MATERIAL`) im Baustellenbericht erfasst wird — ein
**zweiter, paralleler Erfassungsweg für dieselbe Sache**.

Zeitbuchungen können es übrigens: `_zeitbuchungen()` (`abrechnung.py:701`) liest
`TimeEntry` und rechnet über die Lohngruppe in Positionen um. Material nicht.

**Der externe Befund.** Genau das ist branchenweit **Schmerzpunkt Nr. 4**:

> „Eine vergessene Nachtragsposition kann den Nutzen mehrerer Monate auf einmal
> aufzehren." — openhandwerk.de *(Anbieterquelle, aber das Problem ist auch bei
> handwerk.com und SBZ dokumentiert)*

> „Erst wenn alle Stundenzettel und Lieferscheine mühsam eingesammelt und
> sortiert sind, kann eine Rechnung erstellt werden."

**Warum das der wertvollste Fund ist:** Ein Monteur, der Material am Einsatz
bucht, glaubt, es sei erfasst. Es ist erfasst — nur eben nirgends, wo es Geld
wird. Das ist ein stiller Umsatzverlust, und niemand merkt ihn, weil die Buchung
ja sichtbar in der Einsatzmappe steht.

**Zu klären:** Ist das Absicht? DB-Beschluss **B-26** verbietet Bestandsführung
(„reine Verbrauchserfassung"). Abrechenbarkeit ist aber nicht Bestandsführung —
das sind zwei verschiedene Dinge. Möglicherweise wurde hier eine Regel breiter
ausgelegt als nötig. **Das ist eine Frage an dich, keine Feststellung.**

---

### 1.2 Der Monteur kann nicht melden, dass er unterwegs ist

**Der interne Befund.** `POST /einsaetze/{id}/status` nutzt `require`
(fail-closed) und wirft bei Scope `EIGENE` **403** (`api/planung.py:828`,
Docstring: „den Status steuert die Disposition/Leitung; Monteur → 403"). Das
Frontend blendet die Statuswechsel-Oberfläche für Monteure konsequent aus
(`einsatz-detail.ts:100–108`). Die Status `UNTERWEGS` und `VOR_ORT` existieren
also im Automaten — **setzen kann sie nur die Disposition**.

**Die Folge.** Der Monteur fährt los und ruft im Büro an, damit dort jemand einen
Status klickt. Oder er ruft nicht an, und niemand weiß, wo er ist.

**Der externe Befund.** Zwei Dinge treffen hier zusammen:

* Foren/Fachpresse nennen als Dispo-Kernproblem: *„Rückmeldungen von der
  Baustelle kommen zu spät oder gar nicht"* → Ursache für Doppelfahrten
  (handwerk.com, „Die 10 größten Fehler bei der digitalen Einsatzplanung").
* International ist die **„Monteur ist unterwegs"-Benachrichtigung mit ETA**
  (wie beim Lieferdienst) ein etabliertes Feature. In ServiceTitan-Reviews wird
  es als *„lifesaver"* bezeichnet — das ist eine **echte Nutzerstimme auf G2**,
  kein Marketing.
* Im deutschen Markt kann das **fast niemand** kundenseitig. Nur Spezialtools
  (Hellotracks, Glympse). Das ist eine offene Flanke.

**Der Zusammenhang zum Buchungslink:** Beides ist dieselbe Mechanik — ein
Token-Link nach außen. Wenn der Monteur „unterwegs" melden kann, kann daraus
automatisch eine Kundenbenachrichtigung fallen. Ohne den ersten Schritt geht der
zweite nicht.

**Zu klären:** Die 403-Sperre ist eine bewusste Entscheidung. Sie so weit zu
öffnen, dass ein Monteur *auf seinem eigenen zugewiesenen Einsatz* die
Fortschrittsstatus `UNTERWEGS`/`VOR_ORT`/`PAUSIERT` setzen darf — aber **nicht**
`ABGESCHLOSSEN`/`AUSGEFALLEN` (die haben kaufmännische Folgen) — wäre eine
kleine, klar begrenzte Änderung. Das Muster dafür gibt es schon: `update_einsatz`
lässt Monteuren genau zwei Felder frei (`_EIGENE_UPDATE_FELDER`).

---

### 1.3 Kein Bankabgleich — und das Thema ist nirgends als Lücke vermerkt

**Der interne Befund.** Zahlungseingang wird **manuell** gebucht
(`record_payment`, `services/buchhaltung.py:568`). Suche nach CAMT, MT940,
Kontoauszug, Bankabgleich im gesamten Repo: **null Treffer** — weder Code noch
TODO noch Docstring. Es ist nicht „verschoben", es ist schlicht nicht gedacht.

Vorhanden und fertig sind: offene-Posten-Liste, Mahnlauf mit Vorschau,
Mahnstufen, Teilzahlung, Zahlungsstorno. Der Unterbau steht also — es fehlt genau
der Schritt, der die Arbeit macht: **das Zuordnen der Kontoauszugszeilen.**

**Der externe Befund.** Zahlungsverzug ist der am härtesten belegte Schmerz:

> „87,5 % der befragten Handwerksbetriebe haben säumige Zahler unter ihren
> Kunden. Viele Betriebe warten 60–90 Tage auf Zahlung." — openhandwerk.de
> *(Anbieterquelle, Statistikherkunft nicht unabhängig verifizierbar)*

Hero Software bewirbt „HERO Wallet" (automatischer Rechnungs- und
Zahlungsabgleich) als eigenes Modul — das ist also ein Feld, auf dem der direkte
Wettbewerber bereits sichtbar spielt.

**Zusatzbefund:** Auch **Skonto ist nur halb da**. `Invoice.discount_percent` /
`discount_days` existieren, `zahlungsbedingungen()` (`beleg.py:220`) rechnet sie
für die Anzeige — aber `record_payment` verlangt einen manuell eingegebenen
Betrag. Niemand prüft, ob die Skontofrist eingehalten wurde. Mit Bankabgleich
fiele das mit ab.

---

### 1.4 Das Gewerk am Termin ist write-only

**Der interne Befund.** `workflow.service_job.trade_id` (Migration 0120) wird
gesetzt — über den Anruf-Durchstich (`telefonauftrag.py:74`) und im Schema
`TerminCreateIn.trade_id` (`api/planung.py:1286`). Aber **kein einziges
Ausgabeschema** trägt ein `trade`-Feld: nicht `ServiceJobOut`, nicht
`ServiceJobDetailOut`, nicht `BoardJobOut`, nicht `TerminOut`. Das Board hat
trotz gegenteiliger Absicht im Docstring **keinen Gewerk-Filter** (nur
`category_id`, `api/planung.py:1135–1143`).

**Ergebnis:** Ein am Telefon sorgfältig gesetztes Gewerk ist nach dem Speichern
für niemanden mehr sichtbar. Die Daten sind da, die Leitung ist nicht angelötet.

Das ist der kleinste Aufwand auf dieser ganzen Liste und behebt einen Zustand,
der schlicht unfertig ist.

**Verwandter Fund:** `ServiceJobQualification` (Zusatz-Qualifikationsbedarf je
Termin) ist bis in den Angular-Service verdrahtet
(`planung-stammdaten.service.ts:142,148`) — **aber keine Komponente ruft es auf**.
Endpunkt `PUT /planung/einsaetze/{id}/qualifikationen` (`qualifikation.py:168`)
existiert und hat keine Bedienfläche.

---

### 1.5 Angebotsfreigabe per Link — dieselbe Technik wie der Buchungslink

**Der externe Befund.** Von allen Selbstbedienungsfunktionen ist diese die **am
weitesten verbreitete** — und zwar auch im deutschen Markt: AngebotFIX, ToolTime,
MOCO, Grip, Jobber. Der Kunde öffnet einen Link, sieht das Angebot, nimmt es mit
einem Klick an. Status springt automatisch auf angenommen.

**Der interne Befund.** MCN hat den Statusautomaten dafür schon:
`VERSENDET → ANGENOMMEN | ABGELEHNT` (`set_quote_status`, `beleg.py:2808`,
Regeltabelle `QUOTE_AUSGANG`). Das Angebot ist ab `VERSENDET` per DB-Trigger
eingefroren (Snapshot + Content-Hash) — **der Kunde kann also nachweislich nur
das annehmen, was verschickt wurde**. Genau die Härtung, die so ein Link braucht,
ist bereits da.

**Warum das zusammen mit dem Buchungslink gebaut werden sollte:** Token-Hash,
Ablauf, Widerruf, Drosselung, CSRF, öffentliche Route ohne Auth-Guard — das ist
zu 90 % derselbe Unterbau. Zwei Mal getrennt gebaut kostet das Doppelte.

---

## 2. Die Link-Familie — was noch in diese Klasse gehört

Alles Folgende teilt sich den Unterbau mit dem Buchungslink (Token-Hash, Ablauf,
Drosselung, öffentliche Route). Sortiert nach Verhältnis Nutzen zu Aufwand.

| # | Funktion | Markt-Verbreitung | Unterbau in MCN | Aufwand obendrauf |
|---|---|---|---|---|
| L1 | **Angebot online annehmen** | Standard, auch DE | Statusautomat + Snapshot-Einfrieren fertig | klein |
| L2 | **Terminbuchung** (der geplante Slice) | international Standard, DE dünn | Verfügbarkeit vollständig ableitbar | mittel |
| L3 | **Terminerinnerung + Bestätigung** | Standard (SMS oft Aufpreis) | ⚠️ hängt am Mailschalter | klein, aber blockiert |
| L4 | **„Monteur ist unterwegs"** | international Standard, **DE fast unbesetzt** | Status existiert, Monteur darf ihn nicht setzen (1.2) | mittel |
| L5 | **Zahlungslink auf der Rechnung** | international Standard, DE im Aufbau | offene Posten + Zahlungserfassung fertig | mittel (Zahlungsdienstleister) |
| L6 | **Mängelmeldung mit Foto per Link** | **praktisch unbesetzt** | Eingangskorb/Vorgang existiert | mittel |
| L7 | **Kundenportal** (Anlagenhistorie, Rechnungen) | international Standard, **DE nur Ansätze** | Dossier + Anlagenkarte fertig | groß |
| L8 | **Wartungserinnerung mit Selbstbuchung** | Erinnerung ja, Selbstbuchung selten | Fälligkeiten-Engine erzeugt schon Termine | klein auf L2 |
| L9 | **QR-Code an der Anlage** → Historie/Wartung | Nische, im DE-Handwerk unbelegt | Anlagenkarte fertig | klein auf L7 |
| L10 | **Bewertungsanfrage nach Abschluss** | international Standard | — | klein, aber siehe Warnung |

**Drei Anmerkungen dazu:**

**Zu L6 (Mängelmeldung).** Die Recherche fand **keinen** Handwerkersoftware-
Anbieter, der das als natives Endkundenfeature führt — es wird über generische
Formular-Tools (MoreApp, Jotform) gelöst. Für einen Betrieb mit Hausverwaltungen
als Kunden ist das der Alltag: Ein Mieter meldet „Heizung tropft, 3. OG rechts".
MCN hat das Objektmodell dafür bereits bis auf die Einheit hinunter. Das ist der
Punkt mit dem größten Abstand zum Markt.

**Zu L10 (Bewertungsanfrage) — Warnung.** „Review Gating" (nur zufriedene Kunden
zur Bewertung schicken) ist von Google **ausdrücklich verboten** und kann zur
Entfernung des Unternehmensprofils führen; wettbewerbsrechtlich ist es zusätzlich
riskant. Falls gebaut: **alle** Kunden gleich behandeln, keine Vorfilterung nach
Zufriedenheit. Kein Trichter, kein „wie zufrieden waren Sie?" davor.

**Zu L3/L5 — der Mailschalter.** Beide hängen an
`MCN_EMAIL_BACKEND=…console.EmailBackend`. Solange der steht, geht keine Mail
raus. Das ist laut CLAUDE.md die einzige verbleibende Sicherung gegen echten
Mailversand an echte Kundenadressen aus dem Produktivsystem.

---

## 3. Dispo — weitere Lücken

| Thema | Ist-Stand | Extern |
|---|---|---|
| **Fahrtzeit / Route** | **existiert nicht.** „Fahrtzeit" ist ausschließlich ein Zeiterfassungs-Typ (`FAHRTZEIT`, 0066), keine Wegzeitberechnung. Kein Routing-Code im Repo | Fachpresse nennt „Fahrzeiten werden nicht eingerechnet" als Standardfehler der Einsatzplanung |
| **Notdienst / Bereitschaft** | **existiert nicht als System.** „Notdienst" ist eine Terminkategorie im Seed, „Bereitschaft" ein Zeittyp. Kein Bereitschaftsplan, keine Rotation, kein Eskalationspfad | Für SHK der Umsatzbringer schlechthin |
| **Offline-Fähigkeit der Monteur-App** | **fehlt.** Kein Service Worker, kein Manifest. Die Monteursicht ist responsives Web | Schmerzpunkt Nr. 8: „Apps, die permanente Netzverbindung brauchen, versagen im Keller" |
| **Kapazitätsplanung** | **halb.** Soll/Ist-Stunden je Bahn werden angezeigt (`services/planung.py:729–850`), aber keine Verteilung/Optimierung | — |
| **Qualifikationsabgleich** | **halb.** Katalog, Nachweise, Bedarf, weiche Warnung — alles da. Eine „wer passt?"-Vorschlagsfunktion ist **bewusst nicht gebaut** (`qualifikation.py:488–493`) | — |
| **Subunternehmer einplanen** | nicht vorhanden | Wird bei HERO ausdrücklich vermisst: *„Subunternehmen kann ich nicht direkt einplanen, was für unseren Ablauf essenziell wäre"* |
| **iCal-Export/-Import** | **existiert nicht** — steht seit `docs/roadmap/06-planung.md` als Konzept da, null Umsetzung (keine Treffer für ical/.ics/VCALENDAR) | Ausdrücklicher Nutzerwunsch: *„offene Kalenderschnittstelle… fehlt bei fast allen Lösungen"* (haustechnikdialog) |

Der ICS-Export ist nebenbei der billigste Punkt der ganzen Liste — ein
Textformat, keine Abhängigkeit, und er ist ohnehin für die Terminbestätigung des
Kunden vorgesehen.

---

## 4. Rechnung und Geld — weitere Lücken

| Thema | Ist-Stand |
|---|---|
| **Material → Rechnung** | fehlt (siehe 1.1) — der größte Posten |
| **Bankabgleich CAMT/MT940** | fehlt vollständig (siehe 1.3) |
| **Skonto-Abgleich** | halb — vereinbart und angezeigt, nicht geprüft (siehe 1.3) |
| **Mahnstufen** | 3 statt 6 geseedet; `dunning_level.fee` bleibt bewusst NULL (STB-Vorbehalt B-22). Steht schon im Backlog |
| **Mahnverlauf pausieren** | fehlt im DB-Schema (steht im Backlog) |
| **DATEV Personenkonten (OPOS)** | Export nutzt **Sammeldebitor**; echte Debitorenverwaltung „gibt es im Schema nicht und ist ein Folge-Slice" (`services/datev.py:95–109`). Ebenda: *„Ein echter DATEV-Import beim Steuerberater ist die abschließende Abnahme"* — **noch nicht erfolgt** |
| **XRechnung / PEPPOL** | bewusst nicht gebaut (`erechnung.py:6,51–58`). ZUGFeRD/Factur-X EN16931 ist fertig und validiert |
| **Marge / EK-Ebene** | kein Model; Voraussetzung für die Auswertungen im Backlog |

**Was gut dasteht und nicht angefasst werden muss:** Angebot→Rechnung in drei
Wegen (aus Angebot, aus Auftrag/REGIE, aus Nachtrag), Abschlags- und
Schlussrechnung mit Anrechnung, Storno/Gutschrift mit Vier-Augen-Prinzip,
Doppelabrechnungssperre über `BillingLink` mit partiellem UNIQUE-Index,
Preisklärung als 422 statt stiller 0,00-€-Position. Das ist mehr, als die meisten
Wettbewerber im Forum vorzuweisen haben.

Besonders bemerkenswert: **`rechnung_aus_auftrag` liest nur *unterzeichnete*
Baustellenberichte.** Die digitale Unterschrift vor Ort — international
Marktstandard — ist hier also nicht nur vorhanden, sondern als kaufmännisches Tor
verdrahtet.

---

## 5. Regulatorik — was mit Datum kommt

Diese Punkte sind keine Wunschfunktionen, sondern Termine.

| Wann | Was | Betrifft MCN |
|---|---|---|
| **seit 1.1.2025** | E-Rechnung **empfangen** können ist Pflicht (§ 14 UStG) | Eingangsbeleg-Erfassung existiert — **kann sie ZUGFeRD/XRechnung-XML einlesen?** Ungeprüft |
| **bis 31.12.2026** | Übergangsfrist für Papier/PDF-Versand läuft aus | — |
| **1.1.2027** | Ausstellungspflicht für Betriebe > 800.000 € Vorjahresumsatz | ZUGFeRD ist fertig → **erfüllt** |
| **1.1.2028** | Ausstellungspflicht für **alle** B2B | erfüllt |
| **B2G, schon heute** | XRechnung + Leitweg-ID zwingend; ohne korrekte Leitweg-ID **automatische Ablehnung** | ⚠️ **nicht erfüllt** — bewusst weggelassen. Für Gebäudeservice mit Kommunen/Schulen als Auftraggeber ein echtes Loch |
| **Mitte/Ende 2026** | XRechnung 4.0 angekündigt (KoSIT) | beobachten |
| **1.7.2030** | ViDA: verpflichtende digitale Meldung grenzüberschreitender B2B-Umsätze | Datenarchitektur ist strukturiert → gut aufgestellt |
| **offen** | Arbeitszeitgesetz-Novelle: Referentenentwurf Juni 2026, **Kabinettsbeschluss steht aus**. Pflicht zur *elektronischen*, tagesaktuellen Erfassung | Stempeluhr + Tagesnachweise + Genehmigung sind gebaut → **vorbereitet** |
| **seit 28.6.2025** | BFSG (Barrierefreiheit) — gilt für **B2C** | Siehe Kasten unten |

### 🎯 Der BFSG-Punkt ist ein Vorteil, kein Problem

Das BFSG gilt nicht für reine B2B-Software. **Sobald aber ein Kundenportal oder
Buchungslink von Verbrauchern genutzt wird** — und genau das ist bei einem
Terminbuchungslink für Privatkunden der Fall — ist das eine B2C-Dienstleistung
und fällt darunter. Neuentwicklungen: sofort. Bestand: Übergangsfrist bis
28.6.2030.

MCN baut **ohnehin WCAG 2.2 AA** (CLAUDE.md, nicht verhandelbar). Der
Buchungslink erfüllt die Anforderung damit ab dem ersten Tag, während
Wettbewerber, die ein Portal ohne Barrierefreiheit betreiben, nachrüsten müssen.
Das ist ein Verkaufsargument, kein Aufwand.

### 🎯 Prüfpflichten als Fälligkeiten-Vorlagen — der klarste USP-Hebel

MCN hat eine **fertige Fälligkeiten-Engine** (`maintenance.due_item`, Endpoint
`/due-items`, `erledigen` erzeugt ein Folgeobjekt inkl. Termin). Was fehlt, ist
der **Katalog der gesetzlichen Fristen** als Vorlage:

| Pflicht | Grundlage | Intervall |
|---|---|---|
| Legionellenprüfung | TrinkwV § 14a | alle 3 Jahre (Großanlagen mit Vermietung); Neuanlage 3–12 Monate nach Inbetriebnahme |
| DGUV V3 ortsveränderlich | DGUV Vorschrift 3 + BetrSichV § 3 | Richtwert 6 Monate; Baustelle 3; Höchstwerte 1–2 Jahre |
| DGUV V3 ortsfest | DGUV V3 + VDE 0105-100 | i. d. R. 4 Jahre |
| Feuerstättenschau | KÜO / SchfHwG | 2× in 7 Jahren, frühestens 3 / spätestens 5 Jahre |
| Heizungsprüfung | **GEG § 60b** | gestaffelt nach Baujahr; Anlagen vor 1.10.2009: Frist bis 30.9.2027 |
| Hydraulischer Abgleich | **GEG § 60c** | Pflicht bei jeder neuen Anlage; neue Wärmepumpe zusätzlich Betriebsprüfung nach voller Heizperiode, spätestens 2 Jahre |
| F-Gase-Dichtheitsprüfung | EU-VO 2024/573 | nach CO₂-Äquivalent gestaffelt; **ab 1.1.2026** Verbot von Frischware GWP ≥ 2.500 bei Wartung |

⚠️ **TrinkwV-Novelle für 2026 erwartet**, Details noch unklar — vor Umsetzung
gegenprüfen. Ebenso ist die Arbeitszeitgesetz-Novelle nur ein Referentenentwurf.

Warum das ein USP ist: Ein Betrieb, der beim Anlegen einer Anlage automatisch die
richtige gesetzliche Prüffrist zugewiesen bekommt, verkauft dauerhaft Wartung
statt einmalig Montage. Die Engine steht — es fehlt der Inhalt.

---

## 6. Was ich ausdrücklich NICHT vorschlage

* **RAG/Wissensbasis vorziehen** — in ENTSCHEIDUNGEN.md fixiert: ganz zum Schluss,
  nach dem KI-Ausbau. Nicht neu aufmachen.
* **Lagerverwaltung** — B-26, bewusst weggelassen. (Material→Rechnung aus 1.1 ist
  etwas anderes und berührt diesen Beschluss nicht.)
* **Feiertagskalender in die Urlaubstage-Zählung ziehen** — INVARIANTEN Kap. 8:
  „wer das umstellt, ändert rückwirkend Urlaubssalden".
* **KI-Telefonassistent, der Anrufe annimmt und Termine bucht** — international
  Trendthema 2025/26 (Jobber bietet es als Zusatzmodul). Passt nicht zur
  Entscheidung „lokal-only, kein Cloud-Modell" und ist gegenüber dem
  Anruf-Durchstich, den es schon gibt, kein naheliegender nächster Schritt.
* **Buy-Now-Pay-Later an der Rechnung** — im US-Markt üblich, in Deutschland bei
  Handwerksrechnungen unüblich. Erwähnt der Vollständigkeit halber.

Ebenfalls bereits als offen dokumentiert und hier nicht doppelt aufgeführt:
Reiter-Verschlankung 11→6, `building.address_id` nicht befüllbar, Mieter in der
Schnellaufnahme, Bilder/Lightbox, Räume als dritte Ebene (I13),
`auftrag.building_id/unit_id` ohne API (I11f), Benutzeranlage/-einladung.

---

## 7. Vorschlag zur Reihenfolge

**Stufe 1 — kleine Schnitte mit unmittelbarer Wirkung**

1. **Gewerk sichtbar und filterbar machen** (1.4). Kleinster Aufwand der Liste,
   behebt einen schlicht unfertigen Zustand.
2. **ICS-Export für Termine.** Ein Textformat, keine Abhängigkeit, ausdrücklicher
   Nutzerwunsch aus dem Forum — und wird für die Terminbestätigung ohnehin
   gebraucht.
3. **Material am Einsatz mit Preis versehen und abrechenbar machen** (1.1) —
   **erst nach deiner Klärung**, ob B-26 dem entgegensteht.

**Stufe 2 — die Link-Familie, gemeinsamer Unterbau**

4. Token-Unterbau bauen (Hash, Ablauf, Widerruf, Drosselung, CSRF, öffentliche
   Route) — einmal, für alles Folgende.
5. **Angebot online annehmen** (L1) — Statusautomat und Snapshot-Einfrieren sind fertig.
6. **Terminbuchung** (L2) — nach `kunden-terminbuchung.md`, Fragen F1–F6 vorher klären.
7. **Mängelmeldung mit Foto** (L6) — der Punkt mit dem größten Abstand zum Markt.

**Stufe 3 — Geld**

8. **Bankabgleich CAMT/MT940** (1.3) inklusive Skonto-Prüfung. Der Unterbau
   (offene Posten, Mahnlauf) steht; es fehlt das Zuordnen.
9. **Mahnstufen 3→6** (steht schon im Backlog).

**Stufe 4 — Dispo-Ausbau**

10. **Monteur meldet UNTERWEGS/VOR_ORT** (1.2), begrenzt auf Fortschrittsstatus.
11. Darauf aufbauend die **Kundenbenachrichtigung „Monteur ist unterwegs"** (L4)
    — sobald der Mailschalter fällt.
12. **Notdienst/Bereitschaftsplan** — für SHK der Umsatzbringer.

**Offen und gesondert zu entscheiden**

* **XRechnung/Leitweg-ID** — hängt allein daran, ob deine Kunden öffentliche
  Auftraggeber bedienen. Wenn ja, ist es keine Kür.
* **Prüfpflichten-Katalog** als Fälligkeitsvorlagen — größter USP-Hebel, aber
  fachliche Sorgfalt nötig (Fristen falsch = Haftung).

---

## 8. Quellen

**Foren/Bewertungen (echte Nutzerstimmen):**
haustechnikdialog.de/Forum/t/119694 · haustechnikdialog.de/Forum/t/40435 ·
bau.com/forum/edv/10047.php · trusted.de/sander-doll-bewertung ·
capterra.com/p/198147/openHandwerk/reviews · capterra.com/p/150053/ServiceTitan/reviews ·
provenexpert.com/de-de/craftboxx · g2.com/products/servicetitan/reviews ·
sbz-online.de (Softwarewechsel im Betrieb)

**Fachpresse:** handwerk.com („Die 10 größten Fehler bei der digitalen
Einsatzplanung") · kwpsoftware.de · streit-software.de

**Anbieterdokumentation (Herstellerangaben):** help.servicetitan.com ·
help.getjobber.com · help.housecallpro.com · hero-software.de/finance/wallet ·
tooltime.app · craftnote.de · taifun-software.de

**Recht:** bundesfinanzministerium.de (E-Rechnung-FAQ; GoBD-Änderung 14.7.2025) ·
e-rechnung-bund.de · gesetze-im-internet.de (KÜO) ·
bundesgesundheitsministerium.de (TrinkwV/Legionellen) · RKI ·
haufe.de (hydraulischer Abgleich GEG) · wettbewerbszentrale.de (BFSG) ·
KPMG/PKF (ViDA) · IHK Rhein-Neckar (Arbeitszeiterfassung)
