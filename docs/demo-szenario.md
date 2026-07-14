# Demo-Szenario (Vorführung beim Chef) — verbindlich

**Zweck:** Der Datenbestand für die Vorführung. **Nicht** zu verwechseln mit
`seed_demo` — das ist Entwicklerfutter (berührt jeden Codepfad einmal) und
wirkt in einer Vorführung wie fremder Beispielkram. Dieses Szenario spiegelt die
Welt des Users (SHK, Berlin) und wird als eigener Befehl `seed_szenario`
umgesetzt. `seed_demo` bleibt daneben für die Entwicklung bestehen.

**Leitgedanke:** Jedes Szenario führt eine **andere** Fähigkeit vor. Eine Demo,
in der fünfmal dasselbe passiert, langweilt. Der Chef muss einen Vorgang
**wiedererkennen** — sonst heißt es „was ist das für ein Dreck".

Stand: 2026-07-14. Szenarien vom User freigegeben.

---

## 1. Stammdaten (vom User geliefert)

### Verwaltung
**Stegos Immobilien GmbH**
info@stegos.net · Klingsorstraße 7, 12167 Berlin · Tel. 030 79085327

### Liegenschaft A — WEG
**Badensche Straße 53**, 10825 Berlin
Verwaltung: Stegos Immobilien GmbH
Mieter (6 Einheiten):

| Einheit | Mieter |
|---|---|
| EG links | Picolino |
| EG rechts | Robco |
| 1. OG links | Musili |
| 1. OG rechts | Ruboni |
| 2. OG links | Lufnik |
| 2. OG rechts | Kutzi |

### Liegenschaft B — EFH
**Peter Borm**
Ringelnatzstraße 22 · Tel. 017662147248 · sascha-richter@homtail.de

---

## 2. Abbildung im Schema (geprüft, nicht geraten)

**Die Verwaltung ist KEINE Beteiligtenrolle an der Liegenschaft.**
`property.property_party_role` kennt nur `COMMUNITY_OF_OWNERS`, `PROPERTY_OWNER`,
`OPERATOR`, `CARETAKER`. Die Verwaltung läuft **ausschließlich über ein Mandat**
(`management.management_mandate`, Kommentar in `db/migrations/0004_property.sql`):

- Party „WEG Badensche Straße 53" (Organisation) → Rolle `COMMUNITY_OF_OWNERS`
  an der Liegenschaft. **Sie ist der Auftraggeber.**
- Party „Stegos Immobilien GmbH" → `management_mandate` mit
  `mandate_type='WEG_MANAGEMENT'`, `scope_type='ENTIRE_PROPERTY'`,
  `principal_party_id` = die WEG. **Sie ist der Ansprechpartner.**
- Der Unterschied wird bei der Rechnung scharf: wer beauftragt, wer zahlt, wer
  den Beleg bekommt (`PRINCIPAL` / `INVOICE_DEBTOR` / `INVOICE_RECIPIENT`).

**Peter Borm:** Liegenschaft Typ EFH, Party Person, Rolle `PROPERTY_OWNER`.

### ⚠ LÜCKE: Mieter können heute NICHT namentlich an der Einheit hängen

`tenure.occupancy` trägt nur die **Nutzungsart** (`RENTED`, `OWNER_OCCUPIED`,
`VACANT`, `COMMERCIAL_USE`, `OTHER`, `UNKNOWN`) und eine `contract_reference`
als Freitext — **keinen Beteiligten**. Picolino, Robco, Musili & Co. sind damit
nicht hinterlegbar.

Das trifft den Kernfall: Der Monteur fährt zur Badenschen Straße, muss in die
Wohnung EG rechts — und braucht Name und Telefonnummer von Robco, um einen
Termin zu machen und hineinzukommen. **Ohne das ist die Demo genau dort stumm,
wo der Chef hinschaut.**

**Den Mieternamen NICHT in `contract_reference` schmuggeln.** Das rächt sich in
der ersten Minute der Vorführung („und wie ruft der Monteur den an?").

**Lösung (eigener Slice, DB-Änderung):** Die Belegung bekommt einen Beteiligten
(nullable — „leerstehend" muss weiter gehen). Der Mieter ist damit ein normaler
Kontakt mit Telefon und E-Mail: auffindbar, verknüpfbar, in der
Liegenschaftsmappe sichtbar. **Wartet auf den Commit der Dossier-Arbeit**, sonst
kollidiert der Migrationsgraph (zwei Blätter).

---

## 3. Die Szenarien (vom User freigegeben)

### WEG Badensche Straße 53 — Auftraggeber WEG, Mandat bei Stegos

**A) Wartungsvertrag Zentralheizung, jährlich.**
Die Fälligkeit läuft automatisch auf und **erzeugt den Auftrag von selbst**.
→ Führt vor: Wartungsverträge + Fälligkeiten-Engine. Der Moment, in dem das
System mitdenkt statt nur zu speichern.

**B) Trinkwasseruntersuchung auf Legionellen** (TrinkwV, 3-Jahres-Frist).
→ Führt vor: Prüffristen. Die Fälligkeit, die man vergisst und die teuer wird.

**C) Havarie: Rohrbruch 1. OG links (Musili).**
Anruf → Schnellerfassung → Monteur auf der Plantafel → Zeit stempeln → Material
buchen → Baustellenbericht → **Unterschrift des Mieters** → Abrechnung in
**REGIE** (Zeit + Material) an die WEG.
→ Führt die **ganze Kette** vor. Und der Grund, warum die Mieter namentlich an
den Wohnungen hängen müssen (siehe Lücke oben).

**D) Thermostatventile tauschen, alle 6 Einheiten.**
Angebot **PAUSCHAL**, angenommen, ausgeführt — vor Ort hängt in einer Wohnung
**ein Heizkörper mehr** als angeboten. Der Monteur trägt die Ist-Menge ein, das
System zeigt den **Soll-Ist-Abgleich** (MEHRVERBRAUCH), das Büro fakturiert die
Abweichung.
→ Führt vor: Soll-Ist. **Das ist Geld, das im Handwerk heute regelmäßig
verlorengeht** — das stärkste Argument der Demo.

### EFH Peter Borm

**E) Badsanierung.**
Großes Angebot → Auftrag → **Abschlagsrechnung** → **Schlussrechnung mit
Anrechnung**. Privatkunde: **§ 35a-Arbeitskostenausweis** (Steuerbonus) und
Versand als **E-Rechnung (ZUGFeRD)**.
→ Führt den Kernprozess vor.

**F) Heizungsstörung im Winter (Therme fällt aus).**
Kleiner, schneller Einsatz mit Notdienstzuschlag — **Rechnung bleibt unbezahlt**.
→ Führt vor: offene Posten + Mahnlauf.

---

## 4. Artikelstamm

Die DATANORM-Dateien liegen unter `d:\Mitra\MCN\DATANORM\` — **alle drei vom
selben Lieferanten** (Kopfsatz: **BÄR & OLLENROTH KG, Berlin**):

| Datei | Inhalt | Entpackt |
|---|---|---|
| `3STAMM.ZIP` | Artikelstamm (Vollkatalog), Stand 02.07.26 | **1,63 GB** |
| `3AENDARTI (1).ZIP` | Änderungsdienst zum Artikelstamm | 95 MB |
| `DATANORM (1).ZIP` | Preispflege (`datpreis.001`) | 68 MB |

**Junkers/Bosch und Vaillant (Ersatzteilkataloge) liegen NICHT in diesem Ordner.**
Der User hat sie samt Artikelstamm bereits **auf dem Server in einer eigenen
Datenbank** (mit eigenen Skripten aufgebaut).

### ENTSCHIEDEN: ein Artikelstamm, mehrere Anbindungen — kein zweites Silo

Das „Gerätewissen" wird **kein eigener Datentopf**. Bär & Ollenroth,
Junkers/Bosch und Vaillant sind **drei Lieferantenanbindungen**
(`pricing.supplier_connection`); ihre Artikel landen alle in `pricing.article`,
nur mit unterschiedlicher Herkunft.

**Warum:** Nur so sind die Ersatzteile **automatisch überall auffindbar** —
Angebot, Baustellenbericht, Rechnung — ohne dass es dreimal gebaut wird. Der
Reiter „Gerätewissen" ist dann ein **Blick** auf diesen Stamm (gefiltert nach
Hersteller, verknüpft mit dem Gerät in der Liegenschaft): Der Monteur öffnet die
Vaillant-Therme an der Anlage, sieht die passenden Ersatzteile und übernimmt sie
in Bericht oder Angebot — weil es normale Artikel sind. **Ein zweiter Topf würde
genau das verhindern.**

### ⚠ OFFEN, bevor die alte Server-DB gelöscht wird

Der User erwog, die bestehenden Datenbanken zu löschen („Skripte reichen ja").
**Erst prüfen, was drinsteckt:**

- Enthalten sie eine **Gerät→Ersatzteil-Zuordnung** (Baureihe, zugehörige Teile,
  Explosionszeichnungen)? Dann ist das **Arbeit, die in DATANORM nicht drinsteht**
  und nicht nachimportierbar ist — der schnellere Weg wäre dann, die Daten
  **direkt aus der bestehenden DB** nach `pricing.article` zu ziehen, statt
  1,6 GB neu zu parsen.
- Oder ist es nur DATANORM in Tabellenform? Dann ist die alte DB entbehrlich.

Dafür gebraucht: **Tabellenliste (`\dt`) + die Import-Skripte.** Klärt der User
mit dem Server-Agenten.

**LÖSCHREGEL: Die alten Datenbanken werden erst gelöscht, wenn der Import in MCN
nachweislich steht** — Artikel gezählt, Suche geprüft, ein Vaillant-Ersatzteil im
Angebot gefunden. Vorher nicht.

### Offene Messfrage: Vollkatalog in der Demo?

Ungeklärt (nicht raten — messen): Importdauer, Artikelanzahl, DB-Größe,
**Suchlatenz**. Eine Artikelsuche, die vor dem Chef zwei Sekunden hängt, ist
schlimmer als ein kleinerer Katalog.

Vermutung: Der Vollkatalog macht Eindruck („der komplette B&O-Stamm ist drin")
und wird als **fertiges Postgres-Volume** auf den Server geschoben, statt dort
importiert zu werden.

---

## 5. Die Belegschaft (vom User geliefert)

| Person | Funktion | Rolle in der Rechtematrix |
|---|---|---|
| Patrick van Dalen | Geschäftsführer | `ADMINISTRATION` |
| Robin Paul | Buchhaltung + Gesellschafter | **`BUCHHALTUNG`** (neu, siehe unten) |
| Tina Radtke | Disponentin | `DISPOSITION` |
| Sascha Richter | Disponent | `DISPOSITION` |
| Murat Emektar | Monteur | `MONTEUR` (`row_scope='EIGENE'`) |
| Julian Hoffmann | Monteur | `MONTEUR` (`row_scope='EIGENE'`) |
| Rojhat Beyaz | Monteur | `MONTEUR` (`row_scope='EIGENE'`) |

**Neue Rolle `BUCHHALTUNG`.** Die vier Rollen aus `seed_demo` (ADMINISTRATION /
DISPOSITION / MONTEUR / NUR_LESEN) haben keine Entsprechung für Robin Paul: Ihn
mit `ADMINISTRATION` auszustatten hieße, ihm die Rechtematrix und die
Benutzerverwaltung mitzugeben. Die Rechtematrix ist **Daten, kein Code** — die
Rolle wird im Szenario-Seed angelegt (Buchhaltung/Belegerfassung/Dokumente
schreibend, Rechteverwaltung nicht).

**Nebeneffekt für die Demo:** Damit lässt sich die **Rollentrennung** vorführen —
der Monteur sieht nur seine eigenen Einsätze (fail-closed), die Disponentin die
Plantafel, die Buchhaltung die offenen Posten. Und das **Vier-Augen-Prinzip** wird
plastisch: Robin beantragt den Storno, Patrick gibt ihn frei.

**Offen:** Die E-Mail-Adressen der Logins folgen bis auf Weiteres der Konvention
aus `seed_demo` (`vorname.nachname@mitra-sanitaer.de`). Falls die Firma anders
heißt: hier ändern. Auf der Demo-Instanz ist der Mailversand ohnehin totgelegt
(siehe `HANDOFF.md` Abschnitt 10), es geht also nichts hinaus.

## 5b. Noch offen vom User

- **Gewerke** und typische Leistungen mit Preisen (kommen ggf. aus dem
  Artikelstamm).

## 6. Reihenfolge

1. Dossier-Arbeit des Parallel-Agenten wird eingecheckt.
2. **Mieter-Slice** (DB): Belegung bekommt einen Beteiligten.
3. **`seed_szenario`** aus diesem Dokument.
4. **Docker/Deployment** (siehe `HANDOFF.md` Abschnitt 10) — inkl. der Falle,
   dass `seed_demo` Passwörter **nur bei DEBUG** setzt (ohne DEBUG bekommen alle
   Seed-Logins ein `unusable_password` — der Chef stünde vor einem Login, durch
   das er nicht kommt).
