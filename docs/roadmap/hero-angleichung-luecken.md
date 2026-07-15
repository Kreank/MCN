# HERO-Angleichung — Lückenliste (Workflow/Infos, NICHT Optik)

**Grundsatz:** MCN behält sein eigenes Design. HERO ist Referenz für Workflow,
Vollständigkeit der Infos und Einfachheit — nicht fürs Aussehen. Quelle: Analyse
der HERO-Screenshots in `Hero Wissen/` gegen die MCN-Angular-Komponenten
(Stand 2026-07-15). Reihenfolge der Rubriken lt. User: Planung → Projekte →
Dokumente → Kontakte. **Planung/Termine ist bereits umgesetzt** (Zieladresse auf
Einsatz-Detail + Plantafel-Kachel, Drehscheibe, Auffindbarkeit, Dedup).

## Umsetzungsstand (2026-07-15)

**Erledigt (umgesetzt, getestet, reviewt, committet):**
- Projekte: 1 (Kontaktkarte), 2 (Gewerk im Dialog), 4 (Verantwortlicher), 6 (Aktionspanel),
  7 (internal_note), 8 (Ort in Liste).
- Dokumente: 2 (Kopieren), 3 (Statuswechsel/Ausgang), 4 (Verschieben), 5 (Übersichtskachel),
  9 (Anschreiben/cover_letter).
- Kontakte: 1 (Dokumente-Tab konsolidiert), 2 (Kontaktkarte), 3 (Notizfeld),
  6 (Objektadress-Label), 8 (Vorgangs-Chip Ansprechpartner), 9 (Anlage-Überleitung).
- (In Arbeit derselbe Durchgang: Dokumente-1 „Rechnung aus Angebot"-Direktweg, Kontakte-7 Mehrfachauswahl.)

**Verschoben — DB-Schema, KOLLISIONSRISIKO:** Kontakte-4 (customer_number, Sequence),
Kontakte-5 (Geschäftsrollen Kunde/Lieferant/Partner), Dokumente-8 (Firmenvorgaben
Zahlungsziel/Skonto) brauchen `models.py`- und Migrations-Änderungen. Im Repo lief
zeitgleich eine **parallele KI-Schema-Arbeit** (neue ai/-Module + Migrationen); zwei
Prozesse, die gleichzeitig `models.py` ändern und Migrationsnummern ziehen, kollidieren.
Diese drei bewusst zurückgestellt, bis die Migrationskette wieder in einer Hand liegt —
dann als eigene sequenzielle Migrationen (Muster: 0109_hero_notiz_label_felder.py).

**Verschoben — Epics (kein Quick-Win, eigene Design-Runde nötig):** Projekte-5
(Ablaufphasen/Stage-Automat), Projekte-9 (Soll/Ist-Kostenerfassung), Dokumente-6
(neue Dokumenttypen/Baustellenbericht), Dokumente-7 (Dokumentvorlagen), Dokumente-10
(Sammeldokument), Kontakte-4/5 (s. o.), Kontakte-10 (strukturierte Upload-Metadaten,
braucht Schema), Projekte 3/10-12 (Logbuch-SYSTEM-Automatik + niedrigprioritär).

## Rubrik-übergreifende Muster (dreifach aufgefallen)
- **Kontext-Karte fehlt überall:** weder Projekt noch Kontakt zeigen dauerhaft die
  Kernkontaktdaten (mailto:/tel:). Ein kleines Karten-Widget löst Projekte-1 + Kontakte-2.
- **Logbuch bleibt manuell + freies Notizfeld fehlt** (Projekte-3/7, Kontakte-3).
- **„Aus dem Kontext heraus"-Aktionen fehlen** (Dokument erzeugen/kopieren/verschieben,
  Aktionspanel, Dokument am Kontakt) — bestehende Dialoge kontextsensitiv anbieten
  statt tief in Reitern vergraben.

**Top-Quick-Wins (klein, isoliert, hoher Nutzen):** Projekte-2 (Gewerk im Dialog),
Dokumente-3 (Statuswechsel-Button), Dokumente-5 (Übersichtskachel), Kontakte-1
(Dokumente-Tab beleben), Kontakte-2/Projekte-1 (Kontaktkarte).

## Projekte (`features/projekt-detail`, `features/projekte`)
1. Kein Kunden-/Kontaktbezug auf der Übersicht → kompakte Kontaktkarte (mailto:/tel:) aus Hauptkontakt der ersten Liegenschaft.
2. **Gewerk/Kategorie fehlt im Anlage-Dialog** (Backend `ProjectCreate.category_id` kann's schon) → `category_id`-Select. *(Quick-Win)*
3. Logbuch rein manuell trotz `SYSTEM`-Enum → Schreibpfade (Auftrag/Dokument/Status) hängen serverseitig `SYSTEM`-Eintrag an.
4. Verantwortlicher nicht sichtbar/änderbar → `responsible_user_id` in `ProjectOut` ausgeben, auf Übersicht mit Zuweisung.
5. Nur OPEN/CLOSED, keine Ablaufphasen (bewusst vertagt) → klein: nicht-blockierendes `stage`-Tag + Zählerleiste in der Liste.
6. Aktionen über Tabs verstreut → kleines Aktionspanel auf der Übersicht (nur Bündelung vorhandener Dialoge/Routen).
7. Kein freies Notizfeld getrennt vom Logbuch → `internal_note` am Projekt, editierbar.
8. Liste zeigt nur Name/Kategorie/Status/Nummer → Ort der ersten Liegenschaft ergänzen.
9. Soll/Ist-Vergleich fehlt (größer, hängt an Zeit-/Materialkostenerfassung) → Folgeschritt, kein Quick-Win.
10-12. Inline-„+Neu" aus Dialogen, Erinnerungs-/Wiedervorlage-Button, rollenbasierte Logbuch-Sichtbarkeit — niedrige Priorität.

## Dokumente (`features/dokumente`, `features/angebot-editor`)
1. **Kein „Dokument erzeugen" (Angebot → Auftragsbestätigung/Rechnung mit Rückverweis).** Rechnung entsteht heute nur aus leerem Formular → Aktion „Aus diesem Angebot erzeugen" kopiert Kopf+Positionen, setzt Referenzfeld. (Teilweise: „Rechnung aus Angebot/Auftrag" existiert im Abrechnung-Tab.)
2. Kein Dokument-Kopieren → „Kopieren" dupliziert Rubriken+Positionen in neuen Entwurf (Zielprojekt wählbar).
3. **Kein „Als abgelehnt/angenommen markieren"** (Status-Enum existiert, keine UI) → Statuswechsel-Button im Angebotskopf. *(Quick-Win)*
4. Kein Verschieben zwischen Projekten → „Verschieben"-Aktion mit `app-referenz-wahl`.
5. **Keine Übersicht-/Gliederungskachel im Editor** → kompakte Kachel aus vorhandenen `lines()`/`kalk()`-Signalen. *(Quick-Win)*
6. Nur zwei Dokumenttypen → klein starten: „Baustellenbericht" als Foto+Freitext-Dokument.
7. Keine Dokumentvorlagen → Positions-Set als benanntes Preset speichern/laden.
8. Keine Firmenvorgaben für Zahlungsziel/Skonto → Firmenprofil um Defaults erweitern.
9. Kein Anschreiben-Freitextfeld im Beleg → mehrzeiliges Feld im Kopfformular.
10. Sammeldokument/Zusammenführen — nachrangig, setzt 6 voraus.

## Kontakte (`features/kontakt-detail`, `features/kontakte`)
1. **Dokumente-Tab ist toter Platzhalter** (größte funktionale Lücke) → vorhandene `app-dateien` mit Kategorie-Filter statt Platzhalter. *(Quick-Win)*
2. Kein dauerhaft sichtbarer Kontaktdaten-Überblick → kleine Info-Karte im `app-mappe`-Kopf (Name, primärer Kontaktweg, Adresse), tab-unabhängig.
3. Logbuch-Platzhalter ohne manuelle Notizen → Freitext-Notizfeld an `identity.party` im Stammdaten-Tab.
4. Keine sprechende Kundennummer → fortlaufende `customer_number` (Sequence), in Liste + Detail.
5. Keine Geschäftsrollen-Kategorie (Kunde/Lieferant/Partner) → `party_role`-Feld mit Segment-Filter.
6. Objektadressen ohne freien Titel/Beschreibung → optionales `label` an `PartyAddress`.
7. Keine Mehrfachauswahl/Gruppenaktion + kein Spaltenfilter → Checkbox-Spalte + eine einfache Aktion.
8. Ansprechpartner ohne Projekt-/Vorgangsbezug → Spalte/Chip mit verknüpften Projekten.
9. Getrennte Anlage-Schritte statt einem Workflow → nach Org-Anlage optional direkt „Ansprechpartner hinzufügen".
10. Keine strukturierten Metadaten beim Upload (Typ/Nummer/Betrag) → optionale Felder im Upload-Dialog bei Ziel „Kontakt".
