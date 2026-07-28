# Invarianten — nicht versehentlich „vereinfachen"

Regeln, die auch in einem Jahr noch gelten. Fast jede steht hier, **weil sie einmal
gefehlt hat** — die Klammern nennen den Schaden, der dabei entstand. Wer eine davon
aufheben will, braucht ein Argument, keine Vorliebe.

**Zwei Leitsätze über allem:**
- **Was im Service sitzt, ist umgehbar; erst was im Trigger sitzt, hält.** Drei
  Reparaturen mussten deshalb ein zweites Mal gemacht werden.
- **„Der Client sendet das nie" ist kein Argument.** Nach der Vision geht die KI durch
  dieselben Tore wie ein Mensch — also durch denselben Service, nicht durchs Angular-UI.

Ausführliche Herleitungen: `docs/archiv/chronik-2026-07.md`.

---

## 1. Geld & Preise

- **Fehlender Preis ist keine Sackgasse, aber NIEMALS 0 €.** Ein Preis gilt erst **ab > 0**
  als Preis (`_ist_preis`) — an allen drei Stellen, wo die DB-CHECKs `>= 0` erlauben.
  Sonst landet die Position mit 0,00 € auf einer plausibel aussehenden, um den vollen
  Betrag zu niedrigen Rechnung. Stattdessen: 422 `preis_unbekannt` mit Klärungsliste,
  Vorschläge **nie vorausgefüllt**, **kein „später"-Knopf**.
- **Fehlt der EK, bleibt der VK unbekannt — nie 0.** Gilt für Matrix, Auswertungen, Marge.
- **Der VK kommt aus genau EINER Rechenstelle, und die Regel ist die einzige Wahrheit.**
  Ein gespeicherter MATRIX-Preis wird nirgends gelesen, sondern live nachgerechnet —
  sonst zeigen Artikelansicht und Editor verschiedene Preise, sobald jemand die Regel ändert.
- **Rangfolge des VK-Vorschlags ist fest:** Handpreis → VK-Gruppe → Matrix (Artikel >
  Warengruppe+Lieferant > Warengruppe > Lieferant > Standard) → Staffel → Mindestmarge als
  Untergrenze → sonst `null`.
- **Die Mindestmarge quantisiert mit `ROUND_CEILING`** — eine abgerundete Untergrenze ist
  keine Untergrenze. (Bei 1-Cent-EK fiel eine 33-%-Mindestmarge sonst komplett weg.)
- **Ein vom Client genannter Preis wird nur akzeptiert, wo der Server keinen hat** — sonst
  wäre die Mindestmarge umgehbar. **Kein Schreibpfad in `pricing.article`** (statisch getestet).
- **Der Belegeditor rechnet keine Summen.** Exakte Rundung je Steuergruppe ist in
  JavaScript-`number` nicht verlustfrei; der Server rechnet verbindlich. Nicht „nachrüsten".
- **Die ausgewiesene Summe ist die Summe der ausgewiesenen Teile** — je Kante runden, nicht
  am Schluss. (Der Umfang sprang beim Speichern von 5,657 auf 5,656 m.)
- **Ein Wert, der in ein EINGABEFELD geht, ist NIE gruppiert formatiert.** `apiZuDeEingabe`
  ohne Tausenderpunkt, `apiZuDeAnzeige` nur zur Anzeige; Mehrdeutiges wie „1.500" wird
  **abgelehnt**, nicht geraten. (Stiller Datenverlust: 1200 → „1.200" → editiert zu „1.500"
  → gespeichert als **1,5**.) Nicht wieder zu einer Funktion zusammenlegen.
- **Massenpflege: Vorschau == Anwenden** (derselbe Code, `dry_run`), idempotent, Handpreise
  werden nie angefasst.
- **Der IDS-Warenkorb rechnet den VK aus dem zurückgegebenen EK**, nicht aus dem Stamm-EK.

## 2. Abrechnung, Belege & Storno

- **Belegposition ist eine Kopie, kein Verweis.** Werte sind eingefroren; ein neuer
  Listenpreis verfälscht kein geschriebenes Angebot. Umgekehrt schreibt das Speichern einer
  Position **niemals** in `pricing.article`.
- **Der einzige Weg vom Beleg in den Stamm ist ein transientes Häkchen** mit eigenem Recht
  (`pricing/AENDERN`) — es lebt nur im Dialog, nie im Payload, sonst schlüge es bei jedem
  Speichern erneut zu. Der **EK wird bewusst nicht übernommen**. Scheitert die Übernahme,
  bleibt die Positionsänderung erhalten und der Fehler wird angezeigt — nie eine Erfolgsmeldung.
- **Die Doppelabrechnungssperre liegt in der DATENBANK** (`invoicing.billing_link`, drei
  partielle UNIQUE `WHERE released_at IS NULL`). Ein UNIQUE auf der Rechnungsposition ginge
  nicht: nach einem Storno müssen dieselben Stunden wieder abrechenbar sein. **Der Storno löst
  die Bindung.**
- **Was die DB nicht sehen kann, fängt der Service — und zwar am AUFTRAG.** Angebots- und
  Berichtspositionen sind disjunkte Quellen; dieselbe Leistung ließ sich über beide Wege je
  einmal fakturieren (reproduziert: 178,50 € auf zwei Rechnungen).
- **Die Sperre hängt an der fakturierten Menge je Artikel-IDENTITÄT**, quellenunabhängig;
  divergente Einheit („Stk" vs. „Stück") → **fail-closed**.
- **Die Erstattungspflicht steht auf GENAU EINEM Beleg — dem Kreditbeleg.** Ein Kreditbeleg
  wird zuerst mit der offenen Forderung verrechnet; nur der Überschuss bleibt Erstattung.
  (Vorher stand die Pflicht auf zwei Belegen und keine Buchung machte beide ruhig.)
- **Erhaltungssatz: Σ offen = Σ Brutto − Σ Gezahltes**, cent-genau, ohne Division. Zuteilung
  darf Geld zwischen Zeilen verschieben, aber keins erfinden und keins verlieren.
- **Die Forderungsgrenze liegt an EINER Stelle** (`services/buchhaltung.py`). Vorher blieb
  eine **stornierte Rechnung offener Posten UND Mahnkandidat**.
- **Vollgutschrift auf eine gebundene Rechnung ist verboten** (verkappter Storno),
  **Teilgutschrift bleibt erlaubt** — eine Kulanz heißt nicht, dass nicht gearbeitet wurde.
- **Anrechnungspositionen sind nicht löschbar**, angehängte Zeilen werden **davor** eingefügt.
  (Löschen forderte den bereits gezahlten Abschlag ein zweites Mal ein: 4.760 → 5.950 €.)
- **Abschläge werden als NEGATIVE POSITIONEN je Steuersatz angerechnet**, nicht als Kopffeld —
  so bleiben Summenprüfung, offener Posten, Mahnwesen, DATEV und Auswertungen ohne Umbau korrekt.
- **Doppelanrechnung ist physisch ausgeschlossen** (Service UND DB); eine Schlussrechnung, die
  einen anrechenbaren Abschlag übergeht, ist nicht veröffentlichbar.
- **`anzeige_menge_preis()` ist die EINZIGE Vorzeichenstelle für die Ausgabe von Kreditbelegen.**
  Web zeigt die DB-Wahrheit (100 × −2,40 €), PDF/XML die EN16931-Darstellung (−100 × 2,40 €),
  weil BR-27 negative Einzelpreise verbietet. Das ist **kein Bug**.
- **Eine NULL im `billing_snapshot` ist eine LÜCKE, keine eingefrorene Aussage** — deshalb
  **feldweiser** Live-Fallback. Bestandsbelege werden **nicht** rehasht.
- **Bekannte Sichtbild-Divergenz bei Altbelegen ist kein Fehler:** Beträge sind identisch,
  Neurendern archivierter Ausfertigungen ist per GoBD ausgeschlossen.
- **Eingangsbelege sind eine eigene Tabelle** (`accounting.receipt`), keine gerichtete `invoice` —
  `invoicing` ist die GoBD-gesicherte Ausgangsseite.
- **Skontofrist darf nicht hinter der Fälligkeit liegen; Skonto bucht NICHTS aus.** Genau eine
  Rechenstelle (`beleg.zahlungsbedingungen()`), aus der PDF, XML, API und Frontend ziehen.
- **PDF/A verbietet Kernfonts** — eingebettetes DejaVu Sans ist Pflicht.
- **Beleg-PDF-Archivierung degradiert auf On-the-fly-Rendering, statt zu scheitern.**

### E-Rechnung & DATEV
- **BT-113 (TotalPrepaidAmount) wird bewusst NICHT genutzt** — es meint den *gezahlten* Betrag
  und mindert die Steuerbasis nicht; der Empfänger zöge die Vorsteuer doppelt.
- **BT-72 (Lieferdatum) bleibt leer** — die Rechnung führt keins, ein erfundenes wäre eine
  falsche Tatsachenbehauptung.
- **Die Skonto-Zeile braucht einen abschließenden Zeilenumbruch** (BR-DE-18), sonst verwirft
  der Validator sie und die Angabe ist maschinell wertlos.
- **DATEV-Modus ANZAHLUNG ermittelt den Leistungsteil als REST**, nie neu gerechnet — sonst
  ginge die Kette bei ungünstiger Rundung um einen Cent nicht auf.
- **Ein DATEV-Moduswechsel wird abgelehnt (422), solange offene Abschläge existieren.**
- **Die Anzahlungs-Standardkonten sind eine begründete Annahme, kein DATEV-Standard**
  (SKR03 1718 / SKR04 3272 bei 19 % — **nicht** 3270, das ist der 16-%-Corona-Satz).
  Mit dem Steuerberater klären.
- **Mahnstufen:** `fee`/`interest_note` bleiben NULL (Steuerberater-Vorbehalt), aktive Stufen
  müssen einen lückenlosen Präfix bilden.

## 3. Baustellenberichte

- **Der Bericht führt KEINE PREISE.** Er wird unterschrieben und versiegelt — mit Preisen wäre
  er eine **Preisvereinbarung, die der Monteur auf der Baustelle abschließt**; mit Mengen ist er
  ein Leistungsnachweis. Ein Schema-Test durchsucht `information_schema` nach Geldspalten und
  hält die Regel auch gegen künftige Migrationen.
- **Ein unterzeichneter Bericht ist versiegelt — auch seine Positionen und seine Anhänge.**
  Ohne den Anhang-Trigger war er nur scheinbar unveränderlich: die Fotos, auf die er sich
  beruft, ließen sich danach noch tauschen.
- **Trägt eine Position eine Herkunft, wird ihre Identität aus der Angebotszeile ABGELEITET,
  nie vom Client geglaubt** (fünf Gleichungen im Trigger). Die Service-Prüfung war umgehbar,
  indem der Client die Felder wegließ — dann ließ sich ein fremdes Soll auf ein versiegeltes
  Kundendokument schieben („angeboten: 500" neben 5 Stück). Präzisierungen des Monteurs gehören
  in die **Notiz**, nicht in die Bezeichnung.
- **Der Soll-Ist schlüsselt über die QUELLZEILE, nicht über den bearbeitbaren Text.** Sonst
  zerfällt eine präzisierte Position („Rohr" → „Rohr DN20") in ENTFALLEN + ZUSATZ, und das Büro
  fakturiert **14 Einheiten Zusatzleistung statt 2 Einheiten Mehrverbrauch**.
- **Kein Projekt-Fallback fürs Soll-Angebot** — bei mehreren Aufträgen am selben Projekt wäre
  dasselbe Angebot Soll für jeden.
- **`work_order_id` ist von der Beleg-Festschreibung ausgenommen** — ein interner Verweis ist
  kein Beleginhalt, sonst wäre die Zuordnung gesperrt, genau wenn man sie braucht.

## 4. § 35a-Arbeitskostenausweis

- **LEITINVARIANTE: unbestimmt ist NICHT null.** Für PAUSCHALE, FREMDLEISTUNG und ZUSCHLAG ist
  der Lohnanteil nicht ableitbar — dort bleibt er NULL und der Beleg weist **gar nichts** aus,
  statt eine geratene Zahl gegenüber dem Finanzamt zu behaupten. Default 0 verschenkt still den
  Bonus, Default „voll" ist Steuerverkürzung.
- **Pflichtangabe genau dort, wo Schaden entsteht** — eine einzige unklassifizierte Zeile macht
  den Ausweis für die **ganze** Rechnung unbestimmbar (Privatkunde verliert 20 % Steuerbonus).
  Wo der Anteil ehrlich nicht ermittelbar ist, ist der Ausweg das **bewusste Abschalten**.
- **Die Prüfung auf Belegebene ist NICHT redundant zum CHECK je Position** — die Anrechnung
  eines Abschlags konnte den Ausweis negativ machen oder über den Rechnungsbetrag treiben.
  Ergebnis ist `UNSTIMMIG` mit Grund, **kein Veröffentlichungsverbot**.
- **Ein abgeleiteter Wert geht NIE in den Payload**, sonst erstarrt er und steht nach einer
  Mengenänderung falsch (600 € Lohn auf einer 1.200-€-Position).

## 5. Rechte & Sichtbarkeit

- **Der KERN einer Entität ist hart getort (403), jeder weitere Baustein prüft SEIN Modul weich.**
  Fehlt das Recht, fehlt der **Baustein** (`null` + `_sichtbar=false`), nicht die Antwort.
- **„Nicht sichtbar" wird ausgesprochen, nie als 0 gezeigt.** Ein fehlender *Wert* ist
  „unbekannt", nie 0,00 €.
- **Der Monteur sieht sein ganzes Objekt, aber nie Preise:** Rechnungen nie, Angebote nur
  preisfrei (eigenes Schema, EK/Marge strukturell ausgeschlossen). Fremdes Objekt → **404**.
- **Der Server darf nie laxer sein als sein eigenes UI.** (Ein Endpunkt mit Einzelpreisen hing
  nur an `workflow/LESEN`, während das Frontend längst auf `invoicing/LESEN` gatete.)
- **`row_scope='EIGENE'` ist nur für Aufgaben und Einsätze umgesetzt — überall sonst
  fail-closed.** Niemals auf `require` zurückfallen, das wäre ein stiller Datenleak. Drei
  Reviews haben hier je ein Loch gefunden; das Muster bleibt gefährlich.
- **`require_create` ist NUR zulässig, wenn die erzeugte Zeile kein Ziel-/Elternfeld trägt.**
  Sonst gehört ein **Ziel-Guard je Zielart** dazu: erlaubte Ziele positiv aufzählen, Rest
  ablehnen. (Ein Monteur konnte ein Foto an einen fremden Bericht hängen — verifiziert 201.)
- **Der Monteur kann keine Räume erfassen — bewusst.** `property` kennt kein `EIGENE`; wer Räume
  anlegen darf, dürfte jede Liegenschaft ändern. Begehungen durch Monteure brauchen einen echten
  Zeilen-Scope, keinen aufgeweichten Guard.
- **Die EIGENE-Sicht bei Einsätzen hängt allein an der Zuweisung, nie am Auftrag** — ein freier
  Termin wird dadurch nicht öffentlich.

## 6. Vier-Augen-Flow

- **Die Genehmigung ist an den `payload` gebunden, nicht nur an Aktion + Ziel.** Storno und
  Rechnungskorrektur teilen sich einen `action_code`; ohne Payload-Bindung ließ sich eine
  genehmigte Teilgutschrift als **Vollstorno** einlösen (im Review reproduziert).
- **`claim()` verbraucht die Genehmigung in DERSELBEN Transaktion wie die Aktion.** Nicht auf
  „Aktion, danach `consume()`" zurückbauen. Scheitert die Aktion fachlich, rollt das Verbrauchen
  mit zurück.
- **Entscheidungen filtern auf `status='ANGEFORDERT'` und prüfen `updated == 1`** — sonst
  überschreibt ein zweiter Genehmiger den Entscheider und treibt den Applier erneut.
- **Der `payload` geht nur an Antragsteller und Entscheider** — sonst läse jede Nur-Lese-Rolle
  die beantragte IBAN mit.
- **Arbeitstag-Freigabe und kaufmännisches Tor B-28 sind zwei unabhängige Schlösser, nicht eins.**

## 7. Zeit & Zeitzone

- **Betriebsdatum ≠ UTC-Datum.** Fachdaten rechnen in `BETRIEBS_TZ`. Zwischen 00:00 und 02:00
  MESZ liegen beide einen Tag auseinander — eine nachts erfasste Rechnungsadresse galt „erst
  morgen", der Beleg wäre **ohne Empfängeranschrift** rausgegangen.
- **`publish_invoice` bleibt bewusst bei UTC** — deckungsgleich mit dem DB-Trigger. Nicht „mitziehen".
- **Ein Handwerkstermin ist eine Uhrzeit auf der WANDUHR.** Serientakte rechnen in Europe/Berlin,
  sonst verschiebt die Sommerzeit den Termin um eine Stunde.
- **Nachtschicht zählt zum Anfangstag** (22:00–06:00 = ein Arbeitstag).
- **Ein Regressionstest, der nicht rot wird, wenn der Fehler drin ist, wäre wertlos** — die Uhr
  wird auf 00:30 Berlin eingefroren und gegengeprüft.

## 8. Zeiterfassung, HR & Gesundheitsdaten

- **EIN Zeitstrahl, zwei Auswertungen.** `workflow.time_entry` ist die einzige Wahrheit;
  `is_work_time` ist das einzige harte Attribut. Auswertung geht über `category__is_work_time`,
  nicht über einzelne Kategorien (sonst fallen Fahrt- und Bereitschaftszeit unter den Tisch).
- **Stundenausgleich wird in MINUTEN geführt**, append-only mit Storno. 20 min sind 0,333… h und
  in einer Dezimalspalte nicht verlustfrei — ein Konto, das bei jeder Drittelstunde rundet, ist
  eine Schätzung, keine Aufzeichnung. **Saldo bleibt abgeleitet.**
- **Niemand gleicht sein eigenes Arbeitszeitkonto aus — auch nicht über den STORNO.** Ein Storno
  *ist* eine Ausgleichsbuchung; die Regel liegt physisch im Trigger. (Reproduziert: 30 h aufs
  eigene Konto wurde abgewiesen, dieselben 30 h über den Storno gingen durch.)
- **Vergessenes Ausstempeln wird NICHT automatisch beendet.** Ein erfundenes Ende wäre eine
  Falschaussage in einer gesetzlichen Aufzeichnung (§ 17 MiLoG). Kein „Auto-Stopp nach 12 h".
- **Die gesetzliche Pause wird VOLL abgezogen, nicht auf die Schwelle gekappt** — eine
  1-Minuten-„Pause" wäre keine Ruhepause nach § 4 ArbZG.
- **Resturlaubs-Übertrag SETZT, er addiert nicht.** **Verfall standardmäßig AUS**: § 7 Abs. 3
  BUrlG *erlaubt* den 31.03.-Verfall, ordnet ihn nicht an. Es wird nichts weggerechnet, was der
  Betrieb nicht ausdrücklich einstellt.
- **Ein Attest bekommt IMMER ein eigenes Speicherobjekt**, Dedup aus, Guard fail-closed. (Hängte
  der Monteur das Attest zusätzlich an seinen Einsatz, war es über den Dedup für die ganze
  Disposition lesbar.) Fremdzugriff → **404, nicht 403** — ein 403 bestätigte die Existenz der
  Krankmeldung. Keine Diagnose gespeichert.
- **Gesundheitsdaten gehören hinter `hr/LESEN`, nie in eine `workflow`-Schnittstelle.** Die
  Plantafel zeigt „abwesend, von–bis", **nicht** die Abwesenheitsart (DSGVO Art. 9).
- **Der Feiertagskalender gilt NICHT für die Urlaubstage-Zählung** — wer das umstellt, ändert
  rückwirkend Urlaubssalden. Bewusst noch nicht getan.

## 9. Raumaufmaß, Heizlast & Bauteile

- **Der Raum ist Objektstammdatum, kein Werkzeug-Zwischenwert** — er hängt an der Liegenschaft,
  nicht am Vorgang. Räume tragen No-Delete (nur INAKTIV).
- **Die Fläche ist die Wahrheit, `länge × breite` nur die Herleitung.** Kein CHECK erzwingt das
  Rechteck — der L-förmige Raum ist genau der Fall, für den man ein Aufmaß braucht.
- **Die Nettowandfläche wird nie negativ** — geprüft **je Wand und je Raum**, per INSERT-Trigger,
  nicht per Service-Reihenfolge. (25 m² Fenster gegen 10 m² Wand → −15 m², das wäre als **Menge
  in ein Angebot** gelaufen.) Serialisierungspunkt ist die **Raumzeile**. Hat der Raum noch keine
  Hüllfläche, ist sie **unbekannt, nicht 0**.
- **Der Anker ist unveränderlich** — eine Wand wandert nicht in einen anderen Raum, sonst umginge
  ein `UPDATE room_id` beide Grenzen.
- **Eine Öffnung sitzt IN ihrer Wand** (zusammengesetzter FK) — bei zwei Außenwänden wäre sonst
  undefiniert, aus welcher das Fenster ausgeschnitten wird.
- **Heizlast: unbestimmt ist NICHT null.** Fehlt ein U-Wert, ist das Ergebnis `null` **mit
  benanntem Grund**, niemals 0 — ein fehlender U-Wert als 0 hieße „diese Wand verliert keine
  Wärme". Eine `BEHEIZT`-Fläche trägt dagegen definitionsgemäß 0 W bei.
- **Auslegungsdaten gehören ans OBJEKT, nicht an den Aufruf.** Als Query-Parameter wurden sie
  nirgends abgefragt — das Feature war inert. Rangfolge: Raum → Objekt → `null`.
- **Der Was-wäre-wenn-Pfad ist kein Nebeneingang.** Über Query-Parameter ließ sich die Heizlast
  auf 0 kW rechnen (`kennwert_w_m2=0`) oder negativ (`aussentemperatur_c=500`). Jetzt dieselbe
  Prüfstelle → 422 mit Grund, nie ein gerechnetes Ergebnis.
- **KEINE DIN-Tabellen im Produkt.** Keine Klimadaten, U-Werte, f-Faktoren, Luftwechselraten —
  alles Eingaben des Betriebs. Die Anwendung einer Rechenvorschrift ist frei, das Mitliefern von
  Norm-Tabellenwerten nicht. Beide Verfahren sind **überschlägig, kein Nachweis nach DIN EN 12831**.
- **Die Vorlage ist eine KOPIERQUELLE, kein Verweis.** Der U-Wert wird beim Erfassen kopiert;
  eine spätere Katalogkorrektur darf nicht rückwirkend die Heizlast eines Objekts ändern, das
  der Betrieb dem Kunden vorgerechnet hat.
- **Der Katalog wird OHNE U-Werte ausgeliefert** (29 Seed-Zeilen, nur Namen) — der Betrieb
  unterschreibt die Auslegung und soll keine Zahlen unterschreiben, die eine Software geraten hat.
- **Leitungslängen sind eine deklarierte SCHÄTZUNG**, ohne erfundenen Zuschlagsfaktor.
- **Das Aufmaß liefert eine Menge, keinen Preis** (`unit_price: null`). Eine Menge darf aus dem
  Rechner kommen, eine Geldzahl nie.

## 10. Grundriss

- **Koordinaten sind ganzzahlige Millimeter im System des GESCHOSSES**, nicht des Raumes —
  Gleitkomma erzeugt Kanten, die „fast" aufeinander liegen, und die Etagenübersicht entsteht
  ohne weitere Daten.
- **Wer zeichnet, misst nicht doppelt.** Mit Umriss rechnet der Server Fläche (Gauß, **Betrag**)
  und Umfang und **verwirft den Client-Wert**.
- **Zwei Wände auf derselben Kante sind gesperrt** (sie zählten dieselbe Fläche doppelt in die
  Heizlast). **Eine fehlende Öffnungslage ist unbekannt, NICHT 0** — die Öffnung zählt in Fläche
  und Heizlast, sie wird nur nicht gezeichnet.
- **Ein `edge_index` gehört nur an eine Wand** — als CHECK auf der Zeile. Über die API ließ sich
  einer **Decke** ein `edge_index` geben: ihre Fläche wuchs fortan mit der Raumhöhe.
- **`area_is_derived`: die Zeile weiß selbst, woher ihr Wert stammt.** Abgeleitet ⇒ wird bei
  jeder Änderung neu gerechnet; Handeingabe ⇒ wird **nie** überschrieben (Giebel, Erker,
  Dachschräge sind legitime Übersteuerungen). Der Client darf für eine abgeleitete Wand
  `gross_area_m2` **nicht** mitschicken, sonst erstarrt die Fläche.
- **Die Kantenliste ist gleichwertig zur Zeichnung** — der Raum ist vollständig ohne Maus
  erfassbar (WCAG). Ein Handwerker misst 4,37 m mit dem Laser: er tippt sie, er zieht sie nicht.
- **Der Nordpfeil wird abgeleitet — widersprechen sich die Wandausrichtungen, erscheint KEINER.**
  Ein erfundener wäre schlimmer.

## 11. Plantafel & Termine

- **Der Auftragsbezug eines Einsatzes ist unveränderlich** — sonst ließe sich ein laufender
  Einsatz nachträglich an einen abgerechneten Auftrag hängen und beide Tore umgehen. Deshalb
  gibt es **kein „freien Termin hochstufen"**; wer das will, braucht einen eigenen getorten
  Servicepfad.
- **Das Grundraster ist eine ANZEIGE-Einstellung, KEIN Filter.** Liegt ein Termin außerhalb,
  weitet sich das Band. **Ein unsichtbarer Termin wäre der gefährlichste Fehler einer Plantafel.**
- **Gegen den Tag DER SPALTE rechnen, nicht gegen den Tag des Termins** — sonst kollabiert ein
  16-Stunden-Termin auf einen 25-Pixel-Stummel.
- **Die Reihen-Packung nimmt dieselbe Geometrie an wie das Rendering** und rechnet in
  **gezeichneten** Koordinaten — sonst überlappen geklemmte Kacheln wieder.
- **Die gezeichnete Mindestbreite muss ≥ der CSS-`min-width` sein**, sonst wächst die Kachel aus
  ihrer Grid-Area heraus. **rem wird gemessen, nicht mit 16 px geraten** (WCAG 1.4.4).
- **Konflikt- und Statusmarke bleiben in JEDER Kachelbreite sichtbar** — sonst hinge beides nur
  noch an der Farbe.
- **Gestaucht wird die Zeit nie** — reicht der Platz nicht, scrollt das Board.
- **Auslastung ohne Vertrag ist `null` = unbekannt, nie 0.**
- **Der Qualifikations-Abgleich WARNT, er BLOCKIERT NICHT** (wie die Doppelbelegung). Stichtag
  ist der **Terminbeginn in Ortszeit**. Das Board zeigt die FOLGE („kein Nachweis für X"), nicht
  das Gültig-bis aus der Personalakte.
- **Doppelbelegung bleibt eine WARNUNG, keine Sperre** — nicht aufweichen, auch nicht „nur für
  Ressourcen". **Nicht zu verwechseln mit der Zeiterfassung:** dort ist die Überlappung eigener
  Buchungen per EXCLUDE hart gesperrt.
- **Ein Serientermin ist eine Reihe echter, eigenständiger Einsätze — keine Regel.** Ein
  abgesagter Dienstag macht den Mittwoch nicht kaputt.
- **`series_anchor` ist der Taktgeber** — nie aus dem Vorgänger (der geklemmte 28.02. weiß nicht
  mehr, dass „der 31." gemeint war) und nie aus dem Bestand (sonst wird aus „jeden Montag"
  dauerhaft „jeden Dienstag"). Ein zweites „Wiederholen" **verlängert** die Reihe.
- **Die Default-Dauer je Terminkategorie ist ein VORSCHLAG** — eine geänderte Kategoriedauer
  verschiebt keinen bestehenden Termin (das wäre eine stille Umplanung zugesagter Termine).

## 12. Fälligkeiten & Fristen

- **Die Idempotenz garantiert die DATENBANK, nicht der Code** — drei partielle UNIQUE über
  (Anker, `due_date`), **statusunabhängig**, damit auch ein VERWORFENER Eintrag nicht wieder
  aufersteht. Jeder Insert im eigenen Savepoint.
- **Ein Fälligkeitsdatum wird NIE verschoben** — eine Frist ist eine Frist. Verschoben wird nur
  der abgeleitete Wunschtermin (**Samstag bleibt bewusst Arbeitstag**).
- **Verwerfen schreibt die Quelle fort**, sonst stünde der Vertrag für immer auf demselben Datum
  und wäre still tot.
- **Kein Rechtsrat im Produkt.** `basis` (BGB/VOB) ist ein **Etikett**; maßgeblich ist allein
  `duration_months`. Keine Fristen aus Normen hart verdrahten.
- **STORNIEREN ist das Tor fürs Verwerfen** — DISPOSITION darf erledigen, aber eine Frist nicht
  bewusst verstreichen lassen.
- **Wartungsvertrag ohne Anlagenzuordnung heißt „gilt fürs ganze Objekt", nicht „deckt nichts ab"**
  (`maintenance.contract_asset`, 0135). Bestandsverträge bleiben damit gültig, ohne dass ihnen
  jemand eine Anlage andichtet. Ein Vertrag, der Anlage A **nennt**, erscheint bei Anlage B
  **nicht** mehr — genau dieser Fehlschluss („irgendein Vertrag gilt fürs Haus, also ist meine
  Therme versorgt") war der Befund aus dem Praxistest.
- **Vertrag und Anlage müssen zur selben Liegenschaft gehören** — physisch erzwungen über zwei
  zusammengesetzte FKs auf dieselbe `property_id`-Spalte der Zuordnungstabelle. Ein Service-Check
  allein wäre umgehbar.
- **Wartungs-Fälligkeiten erscheinen an einer Anlage nur, wenn der Vertrag sie nennt.** Ein
  objektweiter Vertrag hängt seine Fälligkeit an keine bestimmte Therme.

## 13. Stammdaten, Import & Anbindungen

- **Keine zweite Wahrheit:** Mieter über `tenure.occupancy_party`, Verwaltung über ein **Mandat**
  — bewusst **keine** `occupancy.party_id`-Spalte. Auch die Anlagenkarte und die Gebäudeansicht
  zeigen Bewohner nur **aus der Belegung**; sie bekommen deshalb kein eigenes Kontaktfeld.
- **Die Etage (`property.unit.storey`) ist Freitext und wird nie umgeschrieben.** Für das Bild
  eines Hauses wird eine Reihenfolge **abgeleitet**, nicht gespeichert; was sich nicht deuten
  lässt, landet sichtbar in einem eigenen Band ganz unten und wird **nicht geraten**. Wer wegen
  einer falsch einsortierten Etage im falschen Stock klingelt, hat die Fahrt umsonst gemacht.
- **Mieterdaten hängen am Modul `tenure`, egal wo sie auftauchen.** Anlagenliste und
  Gebäudeansicht prüfen es einzeln (weiches `check()`) und sprechen ein fehlendes Recht aus
  (`belegung_sichtbar = false`) — eine leere Bewohnerliste ohne dieses Flag hieße „steht leer".
- **Zutrittshinweise gibt es nur am Einsatz, nicht an der Liegenschaft** — das Dossier zeigt sie
  mit benannter Herkunft, statt ein Objektfeld zu erfinden.
- **Preis-Semantik ist Anbindungs-Konfiguration, nie geraten** (`net_price_semantics`). Hintergrund:
  GC liefert `NetPrice` als Positionssumme, obwohl `PriceBasis=1.0` „je Einheit" behauptet.
  Zusätzlich eine **Plausibilitäts-Warnung, kein Auto-Umrechnen**.
- **Die Shop-/Connector-URL ist Konfiguration, kein Code.**
- **Der Warenkorb-Hook ist token-gesichert und nur einmal einlösbar** (Token nur als SHA-256-Hash).
- **DATANORM-Import löscht nicht, sondern setzt INAKTIV** — mit Zip-Bomben-Schutz und Dry-run.
- **Dossier-Aggregate müssen projektgefiltert rechnen** — sonst skaliert ein Einzel-Dossier mit
  der Firmengröße.
- **Löschregel für Altdaten:** erst löschen, wenn der Import nachweislich steht.

## 14. Werkzeuge

- **Ein Werkzeug-Ergebnis geht nur als Textposition in einen Beleg.** Kein in JavaScript
  gerechneter Wert wird je Menge oder Preis.
- **Portierte Rechner sind fachlich 1:1 übernommen** (kein Rechenfehler im Original gefunden),
  **ohne** Normtabellen. Das MAG-Ergebnis ist Auslegungshilfe, kein Nachweis.
- **Der Name „Aufmaß" gehört dem Raumwerkzeug**; der alte Rechner heißt „Mengenermittlung"
  (Dateinamen bewusst nicht umbenannt).

## 15. Bewusst offen — nicht „reparieren"

- **`db/migrations/*.sql` ist NICHT mehr die Quelle der Wahrheit für Trigger.** Maßgeblich ist
  die zuletzt angewandte Django-Migration in `backend/db_core/migrations/`. Wer `db/` liest,
  sieht die **alten** Tore.
- **Board-Einstellungen liegen im `localStorage`** — erst klären, ob sie firmenweit oder je
  Benutzer gehören, dann umziehen.
- **Tagesansicht:** `slots()` baut Stundenspalten per `setHours` — an den zwei
  Zeitumstellungstagen sitzt jeder Balken eine Spalte daneben, wenn ein Nachttermin das Band öffnet.
- **Kachel-Aktionen hängen an `:hover`/`:focus-within`** — auf Touch vom Board aus nicht erreichbar.

---

## Lehren zur Arbeitsweise

- **Eine konsolidierte Rechenstelle ist ein Fehlerdetektor, nicht Kosmetik.** Sieben von neun
  Fehlern saßen nicht im neuen Code, sondern im Bestand — sichtbar erst, als der neue Code die
  Grenze richtig zog.
- **Nicht der Code war naiv, sondern der Testumfang.** „Grün" hieß zuverlässig „der Normalfall
  stimmt"; die Fehler wohnten in den Sonderfällen, die Geld bewegen.
- **Teste Interaktionsketten, nicht nur Funktionen.** Das Zeichnen mit der Maus war komplett tot,
  während alle Einzelteile getestet und grün waren — der Fehler lebte in der Kette.
- **Die Bruchfälle dem Implementierer NAMENTLICH vorgeben**, statt „schreib Tests" zu sagen.
- **Was deterministisch geht, gehört deterministisch gebaut** — kein KI-Anteil in der Rechenarbeit.
- **Gotcha Parallel-Agent:** vor eigenen Migrationen auf uncommittete Fremdarbeit prüfen, sonst
  kollidiert der Migrations-Graph. Beim Commit nur eigene Slice-Dateien stagen.
- **PL/pgSQL-Falle:** `FOR UPDATE` geht nicht mit Aggregaten; eine `NEW.<feld>`-Referenz wird
  **beim Planen** aufgelöst, auch in einem CASE-Zweig, der nie zutrifft — Felder vorher in
  lokale Variablen heben.
- **Quelldateien dürfen keine NUL-Bytes tragen** (sie sind für git binär und nicht diffbar).
