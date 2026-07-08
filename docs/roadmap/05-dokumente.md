# 05 — Dokumente (Hero: Dokumente)

## Zweck & Hero-Entsprechung

Dies ist der größte und meistgenutzte Bereich der Software: alle Geschäftsdokumente
(Angebot, Auftragsbestätigung, Rechnung inkl. Teil-/Abschlags-/Schluss-/kumulierter
Rechnung, Gutschrift/Storno, Lieferschein, Bestellschein, Aufmaß, Baustellenbericht/
Rapport, Sammeldokument, Kalkulationsdokument, allgemeiner Brief) werden hier erstellt,
kalkuliert, gestaltet, versioniert, ausgegeben und verwaltet. Zentrum ist der
**Dokumenten-Editor** (Entwurfsmodus) — laut Hero der „meistgenutzte Screen der ganzen
Software" — flankiert vom **Dokumentenkonfigurator** (Layout/Dokumententypen, in Hero
das „Herzstück"). Im MCN-Leitstand heißt der Sidebar-Punkt **Dokumente** (Empfehlung
aus `00`, statt „Belege"); er spiegelt Heros Bereich 1:1 (Übersicht, Editor,
Konfigurator, Texte & Titel, Vorlagen), gehoben auf die Navy/Orange-Oberfläche und
gehärtet nach GoBD/No-Delete.

**Wichtige MCN-Architektur-Besonderheit gegenüber Hero:** Unser Schema trennt den
**strukturierten Beleg** (`invoicing.quote`/`invoicing.invoice` mit Positionen und
Kalkulation — der „Builder") vom **gerenderten Dokument** (`content.document`: der
PDF-Umschlag, Layout, Versionierung, das an eine Builder-Quelle gekoppelt ist). Der
Editor arbeitet fachlich auf quote/invoice, das abgeschlossene PDF lebt als
`content.document` (Status ENTWURF→VEROEFFENTLICHT→ERSETZT). Dieser Split ist beim
Bau durchgängig zu beachten.

**Abgedeckte Hero-Quelldateien (81):**

_Gruppe A (27):_
- `Gibt es eine Löschfunktion für Dokumente/Gibt es eine Löschfunktion für Dokumente.txt`
- `Kann ich ein erstelltes Angebot löschen oder bearbeiten/Kann ich ein erstelltes Angebot löschen oder bearbeiten.txt`
- `Dokument in ein anderes Projekt verschieben/Dokument in ein anderes Projekt verschieben.txt`
- `Dokumente kopieren/Dokumente kopieren.txt`
- `Auftragsbestätigung erstellen/Auftragsbestätigung erstellen.txt`
- `Ein Angebot erstellen/Ein Angebot erstellen.txt`
- `Eine Rechnung erstellen/Eine Rechnung erstellen.txt`
- `Dokument ohne Briefpapier erstellen/Dokument ohne Briefpapier erstellen.txt`
- `Im Dokument Artikel in Leistungen anzeigen/Im Dokument Artikel in Leistungen anzeigen.txt`
- `Kann ich ein Dokument mit Zwischenüberschriften (Titel) gliedern/… .txt`
- `Kann ich Fotos in eine Rechnung einfügen/Kann ich Fotos in eine Rechnung einfügen.txt`
- `Darstellungsänderungen bei Titelsummen/Darstellungsänderungen bei Titelsummen.txt`
- `GAEB Leistungsverzeichnisse in HERO importieren und kalkulieren/… .txt`
- `Kann ich eine kumulierte Rechnung erstellen/Kann ich eine kumulierte Rechnung erstellen.txt`
- `Kann ich eine Teilrechnung in eine Abschlussrechnung umwandeln und umgekehrt/… .txt`
- `Die Rechnungs- und Zahlungsübersicht in der HERO Software/… .txt`
- `Gutschrift für Rechnung erstellen/Gutschrift für Rechnung erstellen.txt`
- `Kann ich in einer Rechnung Bezug auf einen Baustellenbericht nehmen/… .txt`
- `Kann HERO E-Rechnungen (XRechnung  ZUGFeRD, eRechnung) erstellen und empfangen/… .txt`
- `Der Dokumentenkonfigurator/Der Dokumentenkonfigurator.txt`
- `Dokumententypen (Basistypen) und Ihre Eigenschaften/… .txt`
- `Dokumententypen aus Typ kopieren/Dokumententypen aus Typ kopieren.txt`
- `Bankverbindung in Fußzeile anpassen/Bankverbindung in Fußzeile anpassen.txt`
- `Kann ich ein Skonto hinterlegen/Kann ich ein Skonto hinterlegen.txt`
- `Kann ich mein eigenes Briefpapier mit eigenem Logo verwenden/… .txt`
- `Ausgedruckte PDF-Dateien werden nicht richtig dargestellt (Mozilla Firefox)/… .txt`
- `Gibt es eine Möglichkeit ein Angebot als abgelehnt zu markieren/… .txt`

_Gruppe B (27):_
- `Dokumente/Wie erstelle ich ein Dokument des Typs Allgemein/… .txt`
- `Dokumente/Wie erstelle ich einen Lieferschein/Wie erstelle ich einen Lieferschein.txt`
- `Dokumente/Wie erstelle ich einen Reparaturauftrag/… .txt`
- `Dokumente/Wie erstelle ich einen Bestellschein/… .txt`
- `Dokumente/Sammeldokument erstellen/Sammeldokument erstellen.txt`
- `Dokumente/Mehrere Baustellenberichte zusammenfassen/… .txt`
- `Dokumente/Wie erstelle ich eine Teilrechnung  Akonto  Abschlagsrechnung in HERO/… .txt`
- `Dokumente/Teil‑ und Abschlussrechnungen aus einem anderen Programm in HERO übertragen/… .txt`
- `Dokumente/Wie erstelle ich Rechnungen mit 0% MwSt/… .txt`
- `Dokumente/Wie erstelle ich ein Aufmaßdokument/… .txt`
- `Dokumente/Warum werden in meinem Aufmaß keine Positionen angezeigt/… .txt`
- `Dokumente/Was ist ein Kalkulations-Dokument/Was ist ein Kalkulations-Dokument.txt`
- `Dokumente/Warum wird mir für meine Artikel kein Aufschlag angezeigt/… .txt`
- `Dokumente/Übersicht- und Gliederungskachel im Editor für Dokumente/… .txt`
- `Dokumente/Leistungen zusammenführen/Leistungen zusammenführen.txt`
- `Dokumente/Titelbeschreibungen hinzufügen/Titelbeschreibungen hinzufügen.txt`
- `Dokumente/Wie ändere ich die Größe von Fotos in Dokumenten/… .txt`
- `Dokumente/Wie drucke ich ein Dokument aus/… .txt`
- `Dokumente/Wie erstelle ich eigene Dokumententypen/… .txt`
- `Dokumente/Vorlagen in HERO/Vorlagen in HERO.txt`
- `Dokumente/Wie erstelle ich mir Dokumentenvorlagen/… .txt`
- `Dokumente/Schriftart in Dokumenten einstellen/… .txt`
- `Dokumente/QR-Code in Dokumente oder E-Mails einfügen/… .txt`
- `Dokumente/Platzhalter in der HERO Software/… .txt`
- `Dokumente/Optimierte Darstellung von PDF-Dokumenten - Neuer PDF-Renderer/… .txt`
- `Dokumente/Kann ich nachträglich ein Angebot zu einem Projekt hinzufügen/… .txt`
- `Dokumente/Speicherkapazität und Exportieren von Dokumenten in HERO/… .txt`

_Gruppe C (27):_
- `Dokumente/Wie kann ich ein Aufmaß in einer Position hinterlegen/… .txt`
- `Dokumente/Wie erzeuge ich einen Seitenumbruch/… .txt`
- `Dokumente/Wie kann ich den Zeilenabstand verändern/… .txt`
- `Dokumente/Wie kann ich Alternative Positionen oder Bedarfspostionen (Eventualposition) kenntlich machen/… .txt`
- `Dokumente/Wie kann ich automatische Zwischen- und Übertragssummen in einem Angebot anzeigen lassen/… .txt`
- `Dokumente/Wie kann ich einen Rabatt  Aufschlag in einem Dokument geben/… .txt`
- `Dokumente/Wie kann ich mir den Aufschlag anzeigen lassen/… .txt`
- `Dokumente/Wie kann ich in einem Angebot Einzelpreise ausblenden/… .txt`
- `Dokumente/Wie kann ich Lohn- und Maschinenkosten ausweisen/… .txt`
- `Dokumente/Wie kann ich meinen Liefer- und Leistungszeitraum in einem Dokument anzeigen lassen/… .txt`
- `Dokumente/Wie kann ich mehrere Lieferscheine Zusammenfassen/… .txt`
- `Dokumente/Wie kann ich Positionen von einem Dokument in ein anderes übernehmen/Microsoft Word-Dokument (neu).txt`
- `Dokumente/Wie übernehme ich Preisänderungen im Artikelstamm in meine Dokumente/… .txt`
- `Dokumente/Zeiten in Dokumente importieren/Zeiten in Dokumente importieren.txt`
- `Dokumente/Wie kann ich eine alte Version eines Dokuments wiederherstellen/… .txt`
- `Dokumente/Wie mache ich Aktionen in einem Dokument wieder rückgängig/… .txt`
- `Dokumente/Wie richte ich nach DIN ein/Wie richte ich nach DIN ein.txt`
- `Dokumente/Wo kann ich die Fälligkeit einer Rechnung anpassen/… .txt`
- `Dokumente/Zahlungsübersicht anzeigen/Zahlungsübersicht anzeigen.txt`
- `Dokumente/Wie kann ich EPC QR-Codes auf einer Rechnung anzeigen lassen/… .txt`
- `Dokumente/Wie Funktioniert die Übernahme von Teil-Abschlagsrechnungen in die Schlussrechnung/… .txt`
- `Dokumente/Wie kann ich ein Angebot als Vorlage speichern/… .txt`
- `Dokumente/Wie kann ich TexteTextbausteineTextvorlagen für meine Dokumente erstellen/… .txt`
- `Dokumente/Wie kann ich die Textbausteine für meine Dokumente ändern/… .txt`
- `Dokumente/Wie kann ich Platzhalter einfügen und welche Platzhalter gibt es/… .txt`
- `Dokumente/Wie kann ich Dokumente in HERO hochladen/… .txt`
- `Dokumente/Wie kann ich einen Baustellenbericht  Rapport erstellen/… .txt`

## Ziel-Navigation & Routen

Sidebar-Hauptpunkt **Dokumente** (`/dokumente`). Unterpunkte spiegeln Hero
(Übersicht · Konfigurator · Texte & Titel · Vorlagen); der Editor ist Vollbild.

```
/dokumente                              → Redirect auf /dokumente/uebersicht
/dokumente/uebersicht                   → Dokumentenübersicht (Liste, Filter, Miniaturen, [+ Neu])
/dokumente/neu                          → Erstell-Wizard (Modal-Route; 4 Einstiege)
/dokumente/:id/editor                   → Dokumenten-Editor (Entwurfsmodus, Vollbild)
      ├─ Tab Positionen / Artikel & Leistungen
      ├─ Tab Texte & Titel
      ├─ Tab Kalkulation (Rabatt/Aufschlag, Preise aktualisieren, Aufschlag anzeigen)
      ├─ (Panel) Übersichts-/Gliederungskachel
      ├─ (bei Rechnung) Rechnungs-/Zahlungsübersicht am Dokumentende
      ├─ (bei Angebot+GAEB) GAEB-Import/Kalkulations-Erweiterung
      ├─ (bei Aufmaßblatt) Tab Aufmaß (Mengen-Tabelle)
      ├─ Aktion „Mehr" → Einstellungen-Dialog (Layout je Dokument)
      ├─ Aktion „Positionen übernehmen" / „Zeiten importieren"
      ├─ Live-PDF-Vorschau (rechts)
      └─ [Dokument abschließen] → Abschluss-Dialog
/dokumente/konfigurator                 → Dokumententypen-Liste [+ Dokumententyp]
/dokumente/konfigurator/:typId          → Layout-Editor mit Reitern + Live-Vorschau
      (Allgemeine Einstellungen · Allgemeine Gestaltung · Gestaltung Erste Seite ·
       Gestaltung Folgeseite · Zusätzliche Optionen · Buchung · Dokumentenübersicht)
/dokumente/texte-und-titel              → Textbaustein- & Titelverwaltung (Reiter Texte | Titel)
/dokumente/vorlagen                     → Dokumentvorlagen (z. B. Angebotsvorlagen)
/dokumente/kalkulation/:id              → Kalkulationsdokument (read-only)
/dokumente/ausschreibungen              → GAEB-Verwaltung (Import 83/81, Export 84)
```

**Kontext-Einstiege außerhalb `/dokumente`** (identisch zu Hero nachbauen, führen auf
denselben Wizard/Editor):
- Globaler **[+ Neu]**-Button (überall) → Dokument-Wizard.
- Projektmappe `/vorgaenge/:id` → Reiter „Dokumente" je Typ → [Erstellen] / [Dokument
  erstellen] (siehe `04`).
- Kontaktmappe `/kontakte/:id` → Reiter „Dokumente" → Typ → [Erstellen] (siehe `02`).
- Reiter „Dokumente" auch bei Auftrag/Einsatz und Mitarbeiter (Upload-Kontext, siehe `12`).

## Screens & Komponenten

Wegen der Größe in fünf Blöcke gegliedert: **Übersicht & Wizard**, **Editor**,
**Konfigurator**, **Vorlagen & Textbausteine**, **Ausgabe (PDF/E-Rechnung)** — plus
Katalog der **Belegarten** (kein eigener Screen, sondern Ausprägungen von Wizard/Editor/
Konfigurator).

### A) Dokumentenübersicht (Liste)

- **UI-Typ & Aufbau:** Ressourcen-Liste (shared, siehe `00`) mit Suche + Filter-Segmenten
  (Typ, Status, Projekt/Kontakt, Zeitraum) und **[+ Neu]** oben rechts. Zeilen zeigen
  Miniaturansicht (Klick öffnet PDF in neuem Tab), Dokument-Nr., Typ-Badge, Status-Badge
  (Text+Icon, nie nur Farbe), Betrag. Rechts Icon-Aktionen + Drei-Punkte-Menü.
- **Aktionen kontextabhängig vom Status:** Entwurf → [Bearbeiten (Stift)], [Vorschau
  (Auge)], [Archivieren]. Abgeschlossen → [Stift] (öffnet schreibgeschützt/neue Version)
  + Drei-Punkte: [Versenden], [Dokument erzeugen] (Folgedokument), [Kopieren],
  [Verschieben], [Exportieren als], [xRechnung herunterladen] (Rechnung),
  [Rechnungskorrektur] (Rechnung → Gutschrift/Storno).
- **Zustände:** Laden (Skeleton-Zeilen), Leer („Noch keine Dokumente — [+ Neu]"), Fehler
  (Retry). Rollen: Lesen offen (Dev); schreibende Aktionen nur mit Session +
  entsprechendem Recht.
- **Shared vs. neu:** Ressourcen-Liste + Export-Menü wiederverwenden; **neu**: Miniatur-
  Rendering, Typ-/Status-Badge-Set, statusabhängiges Aktionsmenü.

### B) Dokument-Erstell-Wizard (4 Einstiege)

- **UI-Typ & Aufbau:** Mehrschritt-Modal. Schritt 1: [Art des Dokuments] + [Projekt/
  Kontakt] (inkl. [+ Neuer Kunde]). Schritt 2 (optional): **Vorlagenauswahl** — Liste
  Vorlage-Dokumente mit blauem Haken (Mehrfachauswahl → „gesammeltes" Dokument),
  [Übernehmen] / [Ohne Vorlage fortfahren]. Schritt 3 (typabhängig): Rechnung →
  [Teilrechnung]/[Abschlussrechnung]/[kumulierte Teilrechnung] + Positions-Checkboxen aus
  Referenzdokument; Lieferschein/Reparaturauftrag/Bestellschein/Sammeldokument/
  Baustellenbericht → Positions-/Quelldokument-Übernahme (Checkboxen). → [Erstellen] öffnet
  Editor.
- **Sonderfälle:** Bestellschein → zusätzlich [Lieferant auswählen] (übernimmt
  Lieferanten-Kontaktdaten). Sammeldokument / „mehrere Baustellenberichte/Lieferscheine
  zusammenfassen" → identisches Mehrfachauswahl-Muster.
- **Shared vs. neu:** Mehrstufiger Dialog-Baustein (shared); **neu**: Vorlagen-Picker,
  Referenzdokument-Positionsauswahl.

### C) Dokumenten-Editor (Entwurfsmodus) — Kernscreen

Der wichtigste Screen. Vollbild-Arbeitsfläche: Hauptbereich = Positionstabelle/
Dokument-Canvas; rechts Seitenleiste mit Reitern; oben Toolbar (Undo, Mehr, Vorlagen,
Abschließen); optional Live-PDF-Vorschau.

**C1 · Positionen / Artikel & Leistungen**
- Positionstabelle; Zeilen = Positionen (`quote_line`/`invoice_line`, `line_type`
  MATERIAL/ARBEITSZEIT/PAUSCHALE/FREMDLEISTUNG/FAHRT/ZUSCHLAG/TEXT/ZWISCHENSUMME).
- Artikel/Leistungen per Drag & Drop aus Seitenleiste (Suche im Artikel-/Leistungsstamm,
  siehe `08`). Position-Detail über Stift → Modal mit Reitern **Kalkulation** und
  **Aufmaß**.
- **Positions-Detail Kalkulation:** Material-/Lohn-/Gerätekosten, Lohngruppe, Zeitbasis
  (Min/Std), Aufschlag %, VK-Berechnung, Feld Rabatt. **Leistungen zusammenführen**:
  [+ Leistung hinzufügen] verschmilzt Material-/Lohnelemente einer weiteren Leistung.
- **Positions-Detail Aufmaß-Tab:** [+ Aufmaß] mit Feldern Bezeichnung + Formel
  (Grundrechenarten/Klammern), Zeilen-Duplizieren; Summe wird als Menge übernommen (im
  Kalkulations-Tab gesperrt, mit „A" markiert); Position in Liste mit „A" gekennzeichnet,
  Klick aufs Mengenfeld springt in Aufmaß-Tab.
- **Alternative/Bedarfsposition:** Checkboxen im Positions-Detail; Betrag erscheint in
  Klammern, zählt nicht zur Gesamtsumme. (MCN-Bugfix: Zusammenfassung mit Zwischentiteln
  muss korrekt bleiben — Heros Lösch/Neuanlage-Workaround nicht übernehmen.)
- **Toolbar-Aktion „Artikel in Leistungen anzeigen"** (Layout-Flag, s. Einstellungen).

**C2 · Texte & Titel (Gliederung)**
- Rechte Seitenleiste listet wiederverwendbare Textbausteine + Titel; Einfügen per Drag &
  Drop an Zielposition. [+ Titel] / [+ Text] inline. Rich-Text-Felder mit Formatierungs-
  leiste: Schriftart, [Bild hinzufügen] (Foto einfügen; Resize per Eckgriff),
  [Seitenumbruch erzwingen] (graue Trennlinie), Zeilenabstand via SHIFT+ENTER vs. ENTER.
- **Titel-Detail:** Freitext „Beschreibung" (erscheint im PDF unter Titelnamen).
- **Titelsummen/Zwischensummen:** ab 2 Titeln, wenn Layout „Titelsumme/Zusammenfassung der
  Titel" aktiv; Entwurf zeigt Summe in Titelzeile (Label „Summe"), PDF-Label „Summe
  {Ordnungszahl} {Titelname}". Kein automatischer Seitenumbruch-Übertrag.

**C3 · Kalkulation (Dokument-/Titel-/Positionsebene)**
- Taschenrechner-/Stift-Symbol beim Hover über Titelzeile oder Gesamtsumme öffnet
  **Kalkulationsmenü**: Rabatt/Aufschlag auf Gesamtdokument, einzelnen Titel oder Position
  (Bezeichnung + Typ % oder Pauschale). Alternativ Rabatt als negative Einzelposition.
- **Aufschlag anzeigen** (interne Marge, nur Entwurf, nicht Kunden-PDF): Layout-Flag,
  Voraussetzung Einkaufspreise. **Einzelpreise ausblenden** (nur Gesamtsumme im PDF).
  **Lohn-/Maschinenkosten ausweisen** (§35a EStG; Voraussetzung Lohngruppe).
- **Preise aktualisieren** → Modal „Aus Artikelstamm": Tabelle abweichender Artikel;
  Strategie [VK-Preis anpassen (Aufschlag bleibt)] vs. [Aufschlag anpassen (VK bleibt —
  wichtig bei unterschriebenen Angeboten)]. Quelle Artikelstamm/Datanorm/IDS-Connect
  (siehe `08`). **KI-Andockpunkt** (s. u.).

**C4 · Übersichts-/Gliederungskachel**
- Ein-/ausklappbares Panel: Aufteilung Artikel vs. Leistungen, Arbeitszeiten je Position,
  automatischer Gesamtwert, Titel-Hierarchie/Gliederung. Einklappen vergrößert Canvas.

**C5 · Zeiten importieren**
- Button in Editor-Kopf → Modal: Liste bestätigter Arbeitszeiten des Projekts/Auftrags
  (vorläufige erscheinen nicht), Filter, Gruppierung (u. a. nach Lohngruppe), Option
  Kommentare, Checkbox je Eintrag. Übernahme als Leistungsposition (Preis aus Lohngruppe);
  Original-Zeiteintrag bleibt unverändert; Badge „Importiert" mit Link. (Zeiterfassung
  siehe `06`/`12`.)

**C6 · Positionen aus anderem Dokument übernehmen**
- Modal mit Dokumenten-Checkbox-Liste (z. B. mehrere Lieferscheine/Baustellenberichte) →
  [Übernehmen] bündelt Positionen. Basis für Sammel-/Zusammenfassungsdokumente.

**C7 · Aufmaßblatt (Sonderform des Editors)**
- Mengen-Tabelle (Positionsnr., Bezeichnung, Mengensumme; keine Preise), Positionen mit
  Aufmaß mit „A" markiert. Layout-Checkbox „Positionen mit und ohne Aufmaß in PDF anzeigen"
  (einzeldokument via Mehr→Einstellungen; dauerhaft im Konfigurator). Grundlage für
  Schlussrechnung.

**C8 · Rechnungs-/Zahlungsübersicht (Editor-Unterkomponente, Rechnungstypen)**
- Automatisch am Rechnungsende; Sichtbarkeit über Hover → [Auge], Bearbeiten → [Stift]
  (Dialog 2 Reiter: [Rechnungen] = Vorrechnungen ein/ausblenden; [Übersicht] = Positionen
  manuell [+] mit Titel/Datum/Netto/MwSt/Skonto/Brutto). [Zahlungen aktualisieren].
  Manuelle Pflege nötig, wenn kein gemeinsames Referenzdokument (Aufmaß-/Berichtsbasis).

**C9 · Editor-Toolbar: Undo & Versionen**
- [Pfeil] = Undo letzte Aktion. Mehr → Einstellungen → Reiter **Versionen**: Liste
  früherer Stände, [Übernehmen] setzt zurück. (No-Delete: Zurücksetzen erzeugt neuen Stand,
  überschreibt nicht.)

**C10 · „Mehr" → Einstellungen-Dialog (Layout je Einzeldokument)**
- Spiegelt Konfigurator-Optionen dokumentlokal: Briefpapier an/aus, Artikel-als-Leistungen,
  MwSt. ausweisen, Titelsummen/-zusammenfassung, Einzelpreise ausblenden, Aufschlag
  anzeigen, Lohn-/Maschinenkosten, Liefer-/Leistungszeitraum/-datum, DIN-Seitenränder,
  Rechnungsart-Umschaltung, E-Rechnung-Flag, „als Vorlage speichern".

**C11 · GAEB-Import (Editor-Erweiterung, nur Angebot)**
- Mehr → [GAEB-Import] → Upload X83/X81 (auch 1990 .D83/.D81, 2000 .P83/.P81) oder
  vorhandene GAEB-Datei. Importierte Positionen als Leistungen; Badge für Positionsart
  (Bedarf/Alternative/Pauschal/Nachtrag) mit Tooltip. Klick auf Einheitspreis öffnet
  Kalkulation. Reiter „Textergänzung" (Bietertext), Bereich „Nachtragspositionen".
  Abschluss erzeugt PDF **und** GAEB-84. Verwaltung unter `/dokumente/ausschreibungen`.
  Einschränkungen: nur Basistyp Angebot; GAEB-LV-Dokumente nicht kopier-/erzeugbar; nicht
  unterstützt: Zuschlags-/Vorhalte-/„freie Menge"-Positionen.

**C12 · Abschluss-Dialog**
- [Dokument abschließen] → Name final, [nur erstellen] oder direkter E-Mail-Versand;
  optional Checkboxen „als Vorlage speichern", „zusätzliches Kalkulationsdokument
  erstellen", „E-Rechnung erstellen". Abschluss = Statuswechsel VEROEFFENTLICHT +
  PDF-Erzeugung + (Rechnung) Nummernvergabe.

- **Zustände (Editor gesamt):** Laden (Dokument+Stammdaten), Leer-Entwurf (nur Rahmen),
  Speichern/Konflikt (optimistisches Sperren), Schreibgeschützt (abgeschlossen → nur neue
  Version). Rolle: Editieren nur mit Schreibrecht; Freigabe/Vier-Augen bei Angebot (Status
  INTERN_GEPRUEFT/FREIGEGEBEN).
- **Shared vs. neu:** Rich-Text-Editor, Modal-Dialoge, Statuswechsel-Steuer, Export-Menü
  wiederverwenden. **Neu (Kernaufwand XL):** Positionstabelle mit Drag & Drop, Kalkulations-
  Engine-Anbindung, Gliederungskachel, Aufmaß-Formelfeld, Zahlungsübersicht, GAEB, Live-
  PDF-Vorschau.

### D) Dokumentenkonfigurator

- **UI-Typ & Aufbau:** Zweispaltig — links Dokumententyp-Liste + [+ Dokumententyp], Mitte
  Reiter-Editor, rechts **immer Live-Vorschau**. Neuanlage-Dialog: Name, Basis (Basistyp),
  Standardordner, Nummernkreis. Reiter (1:1 spiegeln):
  - **Allgemeine Einstellungen:** Name, Aktiv/Inaktiv, Standardordner, autom.
    Projektstatuswechsel bei Erstellung.
  - **Allgemeine Gestaltung:** Seitenränder (DIN 5008, [Vorlagen]=Reset), Layout
    Modern/Klassik, Schriftart/-größe, Falzmarken; Hauptblock (Position, QR-Standard-
    vorlage); **Positionen** (Checkboxen: MwSt. ausweisen, Einzelpreise, Artikel in
    Leistungen, Titelsumme, Aufschlag anzeigen, Lohn-/Maschinenkosten); Fußzeile;
    Briefpapier-Upload (PDF, 2-seitig für Erst/Folge), Checkbox „Briefpapier anzeigen".
  - **Gestaltung Erste Seite:** Adresszeile (mm), Betreffzeile 1 (BV-Präfix)/2 (Dok-Nr.)
    je mit Anzeige/Fett/Font, Logo & Firmenanschrift, Dokumenteninformation & Kontaktdaten
    (Liefer-/Leistungsdatum, Zahlungsart), Freitext-Informationsblock (EPC-QR/SEPA-QR
    positionierbar).
  - **Gestaltung Folgeseite:** Logo/Anschrift + Kopf-Informationsblock ein/aus + Position.
  - **Zusätzliche Optionen:** Betreff-Präfix, Skonto/Zahlungsziel (Werktage), Bankdaten
    **nur für ZUGFeRD/xRechnung** (getrennt von Firmenprofil-Bankdaten!), Lohn-/Maschinen-
    kosten-Ausweisungstext.
  - **Buchung** (buchungsrelevante Typen): Buchhaltungskonto, Buchungskategorie (SKR-03
    8290/SKR-04 4290 für 0% MwSt./PV, §13b), Zahlungsziel, Soll/Haben (Gutschrift).
  - **Dokumentenübersicht:** Liste aller Dokumente dieses Typs.
  - Aktion **[Aus Typ kopieren]** (Layout + optional Einleitungs-/Abschlusstext).
- **Zustände:** Live-Vorschau muss jede Änderung sofort spiegeln; Speichern je Reiter.
  Rolle: nur Admin/Konfigurationsrecht.
- **Shared vs. neu:** Formular-/Tab-Baustein; **neu:** Live-Layout-Vorschau-Renderer,
  Briefpapier-Upload/Overlay, mm-Positionierung.

### E) Vorlagen & Textbausteine

- **Texte & Titel (`/dokumente/texte-und-titel`):** Reiter [Texte] und [Titel]. Liste mit
  [+ Text]/[+ Titel], Zeilen-[Stift]/[Archivieren]. Editor: Namensfeld, Rich-Text,
  Tabellen-Tool (Zeilen/Spalten oder HTML), **Platzhalter-Picker** ([Vorlagen oder
  Platzhalter] → Gruppenliste, fügt `{{Gruppe.feld}}` ein), Schriftart, [Speichern].
  Zuordnung zu Dokumenttyp im Konfigurator (Allg. Gestaltung → Textblöcke-Dropdown;
  Änderung wirkt nicht rückwirkend auf bestehende Dokumente).
- **Platzhalter-Referenz:** Gruppen Firma/Kunde/Kundenadresse/Projekt/Niederlassung/
  Mitarbeitende/Dokument/Referenzdokument/Auftrag/… (Feld-Mapping gegen MCN-Schemas prüfen,
  siehe Offene Punkte). Als Nachschlage-Panel im Editor, nicht zwingend eigene Route.
- **Dokumentvorlagen (`/dokumente/vorlagen`):** gespeicherte fertige Dokumente
  (Angebotsvorlagen mit konkreten Artikel-Sets). Anlage über Abschluss-Checkbox „als
  Vorlage speichern"; Anwendung über [Vorlagen] im Wizard/Editor. DB: `content
  .document_template` (Status AKTIV/INAKTIV, Name unique).
- **QR-Code:** externer QR per Drag & Drop in Textbaustein/E-Mail-Template; EPC/SEPA-QR
  dagegen als Platzhalter (auto-generiert aus Rechnungsdaten, IBAN/Betrag ohne Skonto).

### F) Ausgabe: PDF, Druck, E-Rechnung

- **PDF-Vorschau/Druck:** Miniatur/PDF-Klick öffnet PDF (neuer Tab). Anforderung an
  Rendering-Pipeline: erzwungene Seitenumbrüche + lange Artikeltexte fehlerfrei (Heros
  „neuer PDF-Renderer"). Genau **eine** PDF-Ausfertigung je veröffentlichtem Beleg
  (Migration 0032, `file_link` BELEG_PDF unique) — Belege verweisen auf MinIO-Objekt.
- **E-Rechnung (XRechnung/ZUGFeRD):** Abschluss-Checkbox „E-Rechnung erstellen" → PDF+XML
  (ZUGFeRD 2.0 Container); XML separat via Drei-Punkte → „xRechnung herunterladen".
  Voraussetzung Zahlungsdaten + Leitweg-ID (B2G) am Kontakt (siehe `02`). Empfang: als
  Eingangsrechnung/Beleg hochladen (siehe `09`).
- **Export:** Einzeldokument oder komplette Projektmappe (Export-Menü, shared).

### Belegarten-Katalog (Ausprägungen, kein eigener Screen)

Basistypen aus Hero, abgebildet auf `content.document.document_type` bzw.
`invoicing`-Belege: **Angebot** (Preise, Soll/Ist, GAEB, Alternative/Bedarf, Vorlage) ·
**Auftragsbestätigung** · **Rechnung** (buchungsrelevant, DATEV; Varianten Normal, §13b,
Teil-, Abschlags-/Akonto-, Abschluss-, kumulierte, E-Rechnung) · **Teil-/Abschlags-/
Schlussrechnung** (Referenzdokument-Kette, Vorkassen-Verrechnung) · **Gutschrift/
Stornorechnung** (Rechnungskorrektur) · **Lieferschein** (keine Preise) · **Bestellschein**
(Lieferant) · **Aufmaß** (Mengen) · **Baustellenbericht/Rapport** (Zeiten statt Preise) ·
**Sammeldokument** · **Kalkulationsdokument** (intern, read-only, 2 Tabellenblöcke) ·
**Reparaturauftrag** · **Zahlungserinnerung** (→ `09`) · **Brief/Allgemein**.

## API-Endpunkte (django-ninja)

Alle schreibenden Endpunkte laufen über `business_transaction` (siehe `backend/README.md`).
Lesend offen (Dev), schreibend Session + Recht.

| Methode | Pfad (`/api/…`) | Zweck | Auth | Service-Funktion |
|---|---|---|---|---|
| GET | `/dokumente` | Übersicht (Filter Typ/Status/Projekt/Kontakt/Zeitraum) | offen | `document.list` |
| GET | `/dokumente/{id}` | Dokument-/Beleg-Detail inkl. Positionen | offen | `document.get` |
| POST | `/dokumente` | Neues Dokument (Wizard: Typ, Projekt/Kontakt, Vorlage/Referenz) | Session | `document.create` |
| GET | `/dokumente/{id}/preview` | Live-PDF-Vorschau (Entwurf) | offen | `document.render_preview` |
| POST | `/belege/{id}/positionen` | Position anlegen (quote_line/invoice_line) | Session | `beleg.add_line` |
| PATCH | `/belege/{id}/positionen/{lid}` | Position ändern (Kalkulation/Aufmaß/Rabatt/Flags) | Session | `beleg.update_line` |
| POST | `/belege/{id}/positionen/uebernehmen` | Positionen aus anderen Dokumenten/Zeiten | Session | `beleg.import_lines` |
| POST | `/belege/{id}/kalkulation` | Rabatt/Aufschlag Dokument/Titel/Position | Session | `beleg.apply_pricing` |
| POST | `/belege/{id}/preise-aktualisieren` | Aus Artikelstamm (VK- vs. Aufschlag-Strategie) | Session | `beleg.refresh_prices` |
| POST | `/belege/{id}/titel` | Titel/Zwischensumme einfügen | Session | `beleg.add_title` |
| POST | `/dokumente/{id}/abschliessen` | Abschließen → VEROEFFENTLICHT + PDF (+ E-Rechnung) | Session | `document.finalize` |
| POST | `/dokumente/{id}/versenden` | Versand per E-Mail | Session | `document.send` |
| POST | `/dokumente/{id}/kopieren` | Kopie (nur Positionen / + Texte) | Session | `document.copy` |
| POST | `/dokumente/{id}/verschieben` | In anderes Projekt/Kontakt (nicht buchungsrelevant) | Session | `document.move` |
| POST | `/dokumente/{id}/erzeugen` | Folgedokument (z. B. Rechnung aus Bericht) | Session | `document.derive` |
| POST | `/rechnungen/{id}/korrektur` | Rechnungskorrektur → Gutschrift/Storno | Session | `invoice.create_correction` |
| GET/PATCH | `/rechnungen/{id}/zahlungsuebersicht` | Rechnungs-/Zahlungsübersicht | Session | `invoice.payment_overview` |
| GET | `/dokumente/{id}/versionen` | Versionsliste | offen | `document.versions` |
| POST | `/dokumente/{id}/version-wiederherstellen` | Alte Version als neuen Stand | Session | `document.restore_version` |
| POST | `/dokumente/{id}/archivieren` | Archivieren (statt Löschen) | Session | `document.archive` |
| GET | `/dokumente/{id}/xrechnung` | XML-Download (ZUGFeRD/XRechnung) | offen | `document.export_xrechnung` |
| POST | `/dokumente/{id}/gaeb-import` | GAEB 83/81 importieren | Session | `gaeb.import` |
| GET | `/dokumente/{id}/gaeb-export` | GAEB 84 erzeugen | offen | `gaeb.export` |
| GET/POST/PATCH | `/dokumenttypen` (`/{typId}`) | Konfigurator: Typen + Layout | Session | `document_type.*` |
| POST | `/dokumenttypen/{typId}/aus-typ-kopieren` | Layout aus anderem Typ | Session | `document_type.copy_from` |
| GET/POST/PATCH | `/textbausteine`, `/titel` | Texte & Titel | Session | `content_block.*` |
| GET/POST | `/dokumentvorlagen` | Dokumentvorlagen | Session | `document_template.*` |
| GET | `/platzhalter` | Platzhalter-Katalog (kontextabhängig) | offen | `placeholder.list` |
| POST | `/dokumente/upload` | Externe Datei hochladen (Kontext Projekt/Auftrag/…) | Session | `document.upload` |

## DB-Bezug

- **`content.document`** — gerendertes Dokument. Status `ENTWURF`→`VEROEFFENTLICHT`→
  `ERSETZT` (Trigger `enforce_initial_status`, `log_status_change`). `document_type` ∈
  {ANGEBOT, AUFTRAGSBESTAETIGUNG, RECHNUNG, GUTSCHRIFT, EINSATZBERICHT, PROTOKOLL,
  WARTUNGSBERICHT, EIGENTUEMERLISTE, VERTRAG_MANDAT, SCHRIFTVERKEHR, …}. Kopplung an genau
  eine Builder-Quelle via `quote_id`/`invoice_id`/`service_job_id` (CHECKs erzwingen
  Typ↔Quelle). Neue Version: nur auf VEROEFFENTLICHT/ERSETZT, gleicher Typ, Version+1 —
  **UI muss „bearbeiten" nach Abschluss als neue Version führen** (B-30 Einfrieren).
  `content.document_link`, `content.signature`.
- **`invoicing.quote`/`quote_line`** — Angebot mit Statusautomat ENTWURF, INTERN_GEPRUEFT,
  FREIGEGEBEN, VERSENDET, ANGENOMMEN, **ABGELEHNT**, ABGELAUFEN, ERSETZT
  (`validate_status_change`; `replaced_by_quote_id` bei ERSETZT). `line_type`
  MATERIAL/ARBEITSZEIT/PAUSCHALE/FREMDLEISTUNG/FAHRT/ZUSCHLAG/TEXT/ZWISCHENSUMME; Text-/
  Zwischensummenzeilen tragen keine Beträge (CHECK). Rundung B-19. **Hinweis:** MCN hat
  einen echten `ABGELEHNT`-Status auf Angebotsebene — anders als Hero, das Ablehnung nur
  über Projekt-Archivierung abbildet (siehe Offene Punkte).
- **`invoicing.invoice`/`invoice_line`/`invoice_party`** — Rechnung. Status ENTWURF→
  VEROEFFENTLICHT (kein Rückweg). `invoice_number` erst bei VEROEFFENTLICHT (B-14, CHECK).
  Nach Veröffentlichung unveränderlich (B-30); Korrektur nur via Gutschrift/Storno
  (eigener Belegkreis, P3-01: GS-Kreis nur Gutschrift/Storno). `tax_code` (0016).
- **`invoicing`-Beleg-Infrastruktur:** `workflow.number_range` (Nummernkreise/Belegart-
  Präfix), Migration 0032 (genau eine PDF je veröffentlichtem Beleg), 0030/0031 (Merge/
  Unveränderlichkeit), `beleg_rubrik`.
- **`pricing`** — `wage_group` (Lohngruppe/Kostensatz 0034), `sale_price_group`,
  `article_sale_price`, `assembly`/`assembly_component`, `kalkulation` (0033), Datanorm
  (0037), IDS (0029/0040), Preiseinheit (0039) → speisen Positions-Kalkulation und
  „Preise aktualisieren".
- **`content.document_template`** (0041) — Dokumentvorlagen; `dokumentvorlagen`/
  `pipeline_editor` (0042). Layout/Textbausteine/Platzhalter im `content`-Schema.
- **Schutz/Audit:** `audit` (0008), Historienschutz/Härtung (0009), invoicing-Schutz
  (0020), content-Schutz (0024), workflow-Schutz (0015) — No-Delete/Append-only/
  No-Truncate für alle Belegtabellen; UI darf nie DELETE anbieten.

## KI-Andockpunkte (`ai.ai_proposal`)

Die KI durchläuft dieselben Service-Tore wie der Mensch (`business_transaction`,
Statusautomaten, Freigaben). Konkrete Vorschlags-Punkte in diesem Bereich:
- **Positionsvorschläge im Editor:** KI schlägt Positionen (Artikel/Leistungen) für ein
  Angebot vor — z. B. aus Vorgangsbeschreibung, Fotos, ähnlichen Altangeboten — als
  `ai_proposal`, das der Mensch im Positions-Tab annimmt/ablehnt.
- **Textvorschläge:** Einleitungs-/Abschlusstexte, Positionsbeschreibungen, Titel-
  Beschreibungen, E-Mail-Anschreiben (Platzhalter-aufgelöst).
- **Preise aktualisieren:** proaktiver `ai_proposal`, wenn Artikelpreise (Datanorm/IDS)
  signifikant abweichen — inkl. Strategie-Empfehlung (VK vs. Aufschlag fix); bei bereits
  signierten Angeboten mit Vier-Augen-Gate.
- **Folgedokument-Vorschlag:** „aus Baustellenbericht Rechnung erzeugen", „Schlussrechnung
  fällig" als Vorschlag.
- **GAEB-Kalkulation:** Vorbelegung der Einheitspreise importierter LV-Positionen aus
  Leistungsstamm.
- **Plausibilitätsprüfung vor Abschluss:** KI markiert fehlende Pflichtangaben (Liefer-/
  Leistungsdatum GoBD, Leitweg-ID bei E-Rechnung) als Hinweis-Proposal.

## No-Delete/Audit/GoBD-Übersetzung

Hero bietet vielerorts „Löschen/Bearbeiten"; MCN übersetzt konsequent:
- **„Dokument löschen" (Entwurf):** → **Archivieren** (Status/Flag), nie physisch löschen.
  Buchungsrelevante Rechnungen sind in Hero bereits nicht löschbar → bei uns generell
  No-Delete.
- **„Rechnung bearbeiten" nach Abschluss:** unmöglich (B-30). Fachliche Korrektur nur über
  **Gutschrift/Stornorechnung** (`invoice.create_correction`, eigener Belegkreis) + ggf.
  neue Rechnung. Heros „Teil-↔Abschluss umwandeln bei unbezahlt + Überschreiben" wird bei
  uns als **neue Version/neuer Beleg** modelliert, nicht als In-place-Edit.
- **„Angebot bearbeiten":** Entwurf frei editierbar; nach VERSENDET → neue Version
  (`replaced_by_quote_id`, Status ERSETZT), Historie bleibt.
- **„Angebot als abgelehnt markieren":** MCN hat `quote.status = ABGELEHNT` (Grundsatz-
  entscheidung, weicht bewusst von Heros Projekt-Archiv-Weg ab) — zusätzlich Logbuch-
  Begründung (`status_reason`). Prüfen, ob Projekt-Archivierung parallel ausgelöst wird.
- **Version zurücksetzen / Undo:** erzeugt neuen Stand bzw. Snapshot, überschreibt nichts;
  Audit-Trigger protokolliert jede Statusänderung.
- **Verschieben/Kopieren:** Logbucheintrag an Quell- und Zielort (Audit); Kopieren ersetzt
  **keine** Platzhalter (Warnung nötig — sensible Daten aus Quelldokument).

## Offene Punkte / Entscheidungen

1. **Angebots-Ablehnung:** MCN-Schema hat `quote.status = ABGELEHNT`; Hero löst es über
   Projekt-Archivierung. Entscheiden: primärer Weg = Dokumentstatus, Projekt-Archiv als
   Folgeaktion? (Empfehlung: Dokumentstatus führend, Projekt optional.)
2. **Rechnungs-Bearbeitbarkeit:** Heros Quellen widersprechen sich (Entwurf frei vs.
   „Veränderung erstellter Rechnungen nicht erlaubt"). MCN-Regel steht fest (B-30:
   VEROEFFENTLICHT immutabel, Korrektur via Gutschrift) — UI klar so führen.
3. **Zwei Bankdaten-Quellen:** Firmenprofil-Bankdaten (normale Fußzeile) vs. Konfigurator
   „Zusätzliche Optionen" (nur ZUGFeRD/xRechnung) eindeutig beschriften.
4. **Mehrere Dokumente → eine Rechnung:** Hero unterstützt es „nicht vollständig"
   (Workaround Sammel-Baustellenbericht). Entscheiden: generisches Mehrfach-Dokument-zu-
   Rechnung-Feature bauen (empfohlen) oder Workaround spiegeln.
5. **Positionshierarchie/Mehrebenen-Gliederung:** Heros GAEB-Einschränkung (andere
   Dokumenttypen können LV nicht mehrstufig gliedern) beim `line`-Datenmodell von Anfang
   an mitdenken/bewusst vermeiden.
6. **Platzhalter-Feldmapping:** Heros Platzhalterliste (Company/Customer/Partner/Document/
   …) gegen MCN-Schemas (`identity`/`content`/`invoicing`/`workflow`) mappen; „Eigene
   Felder"/„Auftragsadresse" in Quelle unvollständig.
7. **Referenzdokument-Pflicht Teil-/Schlussrechnung:** harte Geschäftsregel (gleiches
   Referenzdokument) als Integritätsbedingung in `invoicing` prüfen.
8. **EPC-QR ohne Skonto:** Verhalten bei Skonto-Angeboten explizit testen.
9. **Zahlungsziel (Werktage):** Feiertags-/Kalenderlogik ungeklärt.
10. **Baustellenbericht/Aufmaß als Quelle:** in Specs nur als Referenz erwähnt; Kopplung
    an `workflow`/`content.document` (EINSATZBERICHT) mit `04`/`06` abstimmen.
11. **PDF-Renderer:** Server-Rendering-Pipeline (Seitenumbrüche, lange Texte, ZUGFeRD-XML,
    GAEB-84) als eigene Backend-Komponente; Tech-Auswahl offen.

## Abhängigkeiten

- **DB (vorhanden):** `content` (0021/0022/0024/0041/0042), `invoicing` (0016–0020,
  0030–0032), `pricing` (0028/0033/0034/0037/0039), `workflow.number_range`, `audit`.
- **Sektionen:** `08` Artikel & Leistungen (Positionsstamm, Preise/Datanorm/IDS —
  Voraussetzung für Angebots-Editor), `02` Kontakte (Empfänger, Zahlungsdaten, Leitweg-ID,
  Skonto), `04` Vorgänge/Projekte (Projektbezug, Soll/Ist, „Dokument erstellen"-Einstieg,
  Projekt-Archivierung), `06` Planung/Zeiten (Zeitenimport), `09` Buchhaltung (DATEV,
  Eingangsrechnung, Mahnwesen), `13` Einstellungen (Firmenprofil/Logo/Bankdaten,
  E-Mail-Templates, Nummernkreise).
- **Shared components (aus `00`, vorher bauen):** Ressourcen-Liste, Detail-Mappe, Mehr-
  stufiger Dialog, Statuswechsel-Steuer, Logbuch/Feed, Export-Menü, Rich-Text-Editor.
- **Auth/Rechte** (`security`) für alle Schreib-UIs; **KI-Layer** (`ai_proposal`) sobald
  Service-Tore stehen. **MinIO** für PDF-/Upload-Objekte. **PDF-/E-Rechnungs-Renderer** und
  **GAEB-Parser** als Backend-Bausteine.

## Aufwand & Priorität

Bereich in Phase 2 (Belegwesen, siehe `00`); `08` teils vorziehen. Reihenfolge innerhalb:

| Baustein | Größe | Reihenfolge |
|---|---|---|
| Dokumentenübersicht (Liste) | M | 1 |
| Erstell-Wizard | M | 2 |
| Editor — Positionen/Artikel & Leistungen | **XL** | 3 |
| Editor — Texte & Titel + Gliederungskachel | L | 4 |
| Editor — Kalkulation (Rabatt/Aufschlag, Preise aktualisieren) | L | 5 |
| Abschluss + PDF-Ausgabe + No-Delete/Versionierung | L | 6 |
| Rechnungslogik (Teil/Abschlag/Schluss/kumuliert, Zahlungsübersicht) | **XL** | 7 |
| Konfigurator (Layout, 6+ Reiter, Live-Vorschau) | **XL** | 8 |
| Vorlagen & Textbausteine + Platzhalter | L | 9 |
| E-Rechnung (ZUGFeRD/XRechnung) + EPC-QR | L | 10 |
| Aufmaß (Position + Aufmaßblatt) | M | 11 |
| GAEB-Import/Export | **XL** | 12 (nachgelagert) |
| Belegarten-Feinausprägungen (Lieferschein/Bestell/Bericht/Gutschrift) | M | 13 |
| Upload-Kontexte / Export / Kalkulationsdokument | M | 14 |

Gesamtcharakter: der größte Slice des Projekts; Editor + Konfigurator + Rechnungslogik +
GAEB sind je für sich XL. Empfehlung: als Serie kleinerer vertikaler Slices bauen (erst
Angebot-Entwurf end-to-end, dann Rechnung, dann Konfigurator-Tiefe, dann GAEB).

## Screenshots zur Vorlage (Wiedererkennung)

Höchste Layoutrelevanz (beim Bau als visuelle Vorlage heranziehen):
- **Rechnung erstellen** — 26 Bilder (`Eine Rechnung erstellen`), größter Satz; prägend für
  Editor-Gesamtlayout.
- **Angebot erstellen** — 13 Bilder (`Ein Angebot erstellen`); Positionstabelle + Seiten-
  leiste.
- **Dokumentenkonfigurator** — 18 Bilder inkl. GIF (`Der Dokumentenkonfigurator`); Reiter-
  Hierarchie + Live-Vorschau.
- **GAEB-Import** — 18 Bilder (`GAEB Leistungsverzeichnisse …`); Positionsart-Badges,
  Kalkulations-Detail.
- **Teil-/Abschlags-→Schlussrechnung** — 18 Bilder (`Wie Funktioniert die Übernahme …`);
  End-to-End-Rechnungskette, Vorkassen-Block.
- **Gutschrift/Rechnungskorrektur** — 11 Bilder (`Gutschrift für Rechnung erstellen`).
- **Aufmaßdokument** — 12 Bilder (7+5); Mengen-Tabelle mit „A"-Kennzeichnung.
- **Übersichts-/Gliederungskachel** — 3 Bilder; ein-/ausklappbares Editor-Panel.
- **Rabatt/Aufschlag-Kalkulationsmenü** — 9 Bilder; Taschenrechner-Interaktion.
- **Rechnungs-/Zahlungsübersicht** — 5 Bilder; 2-Reiter-Dialog.
- **Aufmaß in Position** — 8 Bilder; Formelfeld-Aufbau.
- **Textbausteine/Texte & Titel** — 10 Bilder; Editor + Tabellen-Tool + Platzhalter.
- **E-Rechnung** — 3 Bilder; Abschluss-Checkbox + XML-Download.
- **EPC-QR** — 8 Bilder; Freitext-Informationsblock-Positionierung.
