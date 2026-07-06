# Datenbank — Phasen 1 bis 6 (vollständiger MVP-Umfang)

Stand: 5. Juli 2026
Grundlage: `docs/database/CRM-DATENBANKENTWURF-PHASE-1.md` und `CRM-ENTSCHEIDUNGSPROTOKOLL-A.md`
(Teil A vollständig beschlossen am 5. Juli 2026)

## Voraussetzungen

- PostgreSQL **13 oder neuer** (`gen_random_uuid()` in Core, `gcd`/`lcm` für `numeric`)
- Contrib-Modul `btree_gist` (Teil der offiziellen PostgreSQL-Distribution; keine externe
  Abhängigkeit)

## Ausführung

Migrationen in numerischer Reihenfolge gegen eine leere Datenbank:

```bash
createdb mitra_crm_dev
for f in db/migrations/*.sql; do
  psql -v ON_ERROR_STOP=1 -d mitra_crm_dev -f "$f"
done
```

Akzeptanztest (läuft in einer Transaktion, rollt sich selbst zurück, hinterlässt keine Daten):

```bash
psql -v ON_ERROR_STOP=1 -d mitra_crm_dev -f db/tests/akzeptanztest_phase1.sql
```

Erwartet: `NOTICE`-Zeilen `OK Test …` und abschließend `ALLE AKZEPTANZTESTS BESTANDEN`.
Weitere Suiten analog: `akzeptanztest_phase2.sql` (Workflow), `phase3` (Belege, Zeiten,
Material), `phase4` (Dateien, Dokumente, Kommunikation), `phase5` (Preise, Zahlungen,
Mahnwesen), `phase6` (Rechte, KI), `phase7` (Artikelstamm/IDS).

Statuswechsel mit Begründungspflicht und Benutzerzuordnung erwarten je Transaktion:
`SET app.current_user_id = '<uuid>'` und bei Rücksprüngen `SET app.status_reason = '<Text>'`.

Nebenläufigkeitstest (zwei echte Sessions, paralleler 100-%-Konflikt; hinterlässt synthetische
Testdaten, nur gegen Wegwerf-Datenbanken ausführen):

```bash
bash db/tests/nebenlaeufigkeitstest_phase1.sh <container> <datenbank>
bash db/tests/nebenlaeufigkeitstest_phase2.sh <container> <datenbank>
```

## Migrationsübersicht

| Nr. | Datei | Inhalt |
|---|---|---|
| 0001 | `0001_schemas_und_hilfsfunktionen.sql` | Schemas, `btree_gist`, `util`-Hilfsfunktionen |
| 0002 | `0002_security_und_identity_kern.sql` | `app_user`, `party`, `person`, `organization`, Merge-Kanonik |
| 0003 | `0003_identity_kontakte_referenzen_merge.sql` | Adresse, Kontakte, Beziehungen, externe Referenzen, Merge-Nachweis |
| 0004 | `0004_property.sql` | Liegenschaft, Gebäude, Einheit, Anlage, Objektrollen, externe Referenzen |
| 0005 | `0005_tenure.sql` | Eigentumsstände, Anteile (exakte LCM-Prüfung), Belegung |
| 0006 | `0006_management.sql` | Mandate, Mandatseinheiten, Zuständigkeiten, Befugnisse |
| 0007 | `0007_billing.sql` | Abrechnungsvorgaben, Beteiligtenrollen, Verantwortungsregeln |
| 0008 | `0008_audit.sql` | Audit und Domain Events, Append-only-Durchsetzung |
| 0009 | `0009_historienschutz_und_haertung.sql` | Löschverbot + Audit-Trigger auf historisierten Tabellen, Typwechsel-Schutz, MERGED-Party-Sperre, TRUNCATE-Schutz |
| 0010 | `0010_workflow_infrastruktur.sql` | Schema `workflow`: Nummernkreise, Prioritäten, Statusautomaten, Statusprotokoll |
| 0011 | `0011_projekt.sql` | Projekt + Projekt-Liegenschafts-Zuordnung (B-09/B-10) |
| 0012 | `0012_vorgang.sql` | Vorgang (service_case) mit Statusautomat und Verantwortungsbestätigung |
| 0013 | `0013_auftrag.sql` | Auftrag, Auftragsrollen, Freigabe-/Abrechnungstore (B-01/B-08) |
| 0014 | `0014_einsatz.sql` | Einsatz (service_job) und Einsatzzuordnung |
| 0015 | `0015_workflow_schutz.sql` | Historienschutz und Audit für das Workflow-Modul |
| 0016 | `0016_invoicing_infrastruktur.sql` | Schema `invoicing`: Steuercodes (STB-Vorbehalt), Belegkreise AN/RE/GS, Angebots-Statusautomat |
| 0017 | `0017_zeiten_material.sql` | Zeit-/Materialerfassung mit B-28-Korrekturfenster und Lösch-Audit |
| 0018 | `0018_angebot.sql` | Angebot mit Positionen, B-19-Summenprüfung, Versand-Einfrieren |
| 0019 | `0019_rechnung.sql` | Rechnung mit Beteiligten, Veröffentlichungstoren, Unveränderlichkeit, Folgebelegen |
| 0020 | `0020_invoicing_schutz.sql` | Löschverbote und Audit für das Belegmodul |
| 0021 | `0021_dateien.sql` | Schema `content`: Datei-Steckbriefe (Object Storage, inkl. Videos) und Ein-Ziel-Links |
| 0022 | `0022_dokumente.sql` | Dokumentenbuilder: Dokumente mit Versionierung, Veröffentlichung, Unterschrift (B-29/B-30/B-34) |
| 0023 | `0023_kommunikation.sql` | Kommunikation mit Zuordnungskaskade und Klärungskorb (B-31/B-32/B-33) |
| 0024 | `0024_content_schutz.sql` | Historienschutz und Audit für das Content-Modul |
| 0025 | `0025_preise_zahlungen_mahnwesen.sql` | Schema `pricing`, Zahlungsspiegel (B-23), Mahnstruktur (B-22) |
| 0026 | `0026_rechte_stammdaten.sql` | Rollen, Rechtematrix-Startbelegung, Vier-Augen-Liste (B-35–B-38) |
| 0027 | `0027_ki_grundlagen.sql` | Schema `ai`: content_item, Embeddings (modellagnostisch), ai_run, ai_proposal |
| 0028 | `0028_artikelstamm.sql` | Lokaler Artikelstamm (GTIN, Hersteller) + Großhändler-Referenzen für IDS-Connect-Importe |
| 0029 | `0029_ids_haendler.sql` | IDS-Händler-Registry: Anbindungen als offene Stammdaten (G.U.T., Vaillant, Reisser, Viessmann, ...) |
| 0030 | `0030_merge_antrag.sql` | Dubletten-Merge im Vier-Augen-Verfahren (B-38): Antrag + Bestätigung durch andere Person, Anträge unveränderlich |
| 0031 | `0031_merge_unveraenderlich.sql` | Merge-Zustand einer Partei physisch unveränderlich (Nachreview N-1); Begründungs-Meldung ohne Konfig-Interna (N-6) |
| 0032 | `0032_beleg_pdf_einmalig.sql` | Genau eine PDF-Ausfertigung je Beleg (partielle Unique-Indizes, Review P-1) |
| 0033 | `0033_kalkulation_grundlagen.sql` | Builder-Fundament (Beschlüsse 2026-07-05): VK-Gruppen (formelbasiert), Lohngruppen, Leistungen als Stücklisten, Rubriken je Beleg (einfrierend), Positions-Kalkfelder (EK-/Aufschlag-Snapshot, Herkunft) |
| 0034 | `0034_lohngruppe_kostensatz.sql` | Lohngruppen: Kostensatz getrennt vom Verrechnungssatz (ehrliche Gewinnrechnung) |
| 0035 | `0035_projekt_cockpit.sql` | Projekt-Cockpit: Logbuch (append-only), Notizen (Audit, archivieren statt löschen), Checklisten (Vorlagen + Instanzen), file_link.project_id |
| 0036 | `0036_checklisten_schutz.sql` | Schutzstandard Checklisten-Stammdaten (No-Delete/Audit/No-Truncate, Review PC-2) + Index file_link.project_id |

## Wichtige Regeln (physisch abgesichert)

- Zeitraumsemantik `[valid_from, valid_until)`; `valid_until > valid_from`.
- Keine überlappenden Eigentumsstände oder primären Belegungen je Einheit (GiST-Exclusion).
- Vollständige Eigentumsstände: exakt 100 % über ganzzahlige LCM-Prüfung
  (`Σ num·(D/den) = D`), keine Dezimaldivision, keine Toleranz (OPUS-01).
- `COMMON_AREA`/`TECHNICAL_ROOM` tragen keinen Eigentumsstand (A-08-Präzisierung).
- Einheitsnummern eindeutig je Liegenschaft; Liegenschaftsnummern `OBJ-#####` global (A-09).
- Merge ohne Ketten/Zyklen; Ziel ist immer kanonisch; neue fachliche Referenzen auf
  zusammengeführte Parties werden abgelehnt.
- `party_merge`, `audit_entry`, `domain_event` sind append-only: UPDATE/DELETE/TRUNCATE per
  Trigger blockiert (wirkt auch für Owner/Superuser); der zusätzliche Rechteentzug wird erst
  mit dem Rollenmodell (B-35) wirksam. Restrisiko: Ein Tabellen-Owner kann Trigger
  deaktivieren — echte Härtung folgt durch die Trennung App-Rolle ≠ Owner.
- Historisierte Kerntabellen (Eigentum, Belegung, Mandate, Rollen, Beziehungen,
  Abrechnungsvorgaben): DELETE per Trigger verboten; jede Änderung wird automatisch mit
  Vorher-/Nachher-Auszug in `audit.audit_entry` protokolliert (`ROW_UPDATE`). Setzt die
  Anwendung `SET app.current_user_id = '<uuid>'`, wird der Benutzer erfasst, sonst `SYSTEM`.
- `COMMON_AREA`/`TECHNICAL_ROOM` tragen weder Eigentumsstand noch Belegung; ein
  `unit_type`-Wechsel dorthin ist blockiert, solange solche Daten existieren.
- Mandats-/Einheitskonsistenz über zusammengesetzte Fremdschlüssel und verzögerte
  Constraint-Trigger.

## Betriebsannahmen (verbindlich)

- **Isolationsstufe `READ COMMITTED`** (PostgreSQL-Standard). Die zeilenübergreifenden
  Trigger-Prüfungen (100-%-Regel, Mandatskonflikte) sind unter `REPEATABLE READ`/`SERIALIZABLE`
  nicht durch die verwendeten Sperren abgesichert; der Verbindungspool darf die Isolationsstufe
  nicht anheben.
- Aufrufer müssen Abbrüche durch Sperrenkonflikte (`40P01` Deadlock, Trigger-Exceptions bei
  parallelen Änderungen) als wiederholbaren Fehler behandeln (Retry der Transaktion).
- `property_number` wird ausschließlich über den Default (Sequenz) vergeben; manuelle Vergabe
  ist zu unterlassen (Kollisionsgefahr mit der Sequenz). Sequenzlücken durch Rollbacks sind
  zulässig; eine Wiederverwendung gelöschter Nummern verhindert praktisch das Löschverbot der
  referenzierenden Historie.
- Nummernkreise (`V`/`P`/`AU`/`E` sowie `AN`/`RE`/`GS`): `workflow.next_number()` serialisiert
  die Vergabe je Kreis und Jahr über eine gesperrte Zählerzeile bis zum Transaktionsende —
  Transaktionen, die Nummern ziehen (insbesondere Beleg-Veröffentlichungen), kurz halten.
  Jahreszuordnung in UTC. Die Funktion ist bis zum Rollenmodell (B-35/B-36) von jedem
  Schreiber aufrufbar; RE-/GS-Nummern nur über die Veröffentlichung ziehen (organisatorisch
  absichern, P3-10).
- `app.status_reason` ist einmalig gültig: Die Begründung wird mit dem protokollierten
  Statuswechsel automatisch verbraucht; jeder begründungspflichtige Übergang setzt sie neu.
  **Verbindlich `SET LOCAL`** (transaktionslokal) verwenden — gilt ebenso für
  `app.current_user_id`. Ein Session-`SET` überlebt COMMIT und kann bei Connection-Pooling
  in fremde Transaktionen durchsickern (NR2-02).
- `is_emergency` am Auftrag ist bis zum Rechtemodell (B-35/B-36) von jedem Schreiber setzbar
  und öffnet das Freigabe-Tor (Pflichtdokumentation bleibt); organisatorisch absichern.

## Rückwärtsstrategie

Jede Migration dokumentiert ihre Rückwärtsstrategie am Dateiende. Grundsatz: Rückwärts nur,
solange keine Fachdaten entstanden sind; danach ausschließlich vorwärts gerichtete
Korrekturmigrationen. Auditdaten werden niemals rückwärts migriert.

## Bewusst noch nicht enthalten

- **Aufbewahrung und Löschung** (C-06 bis C-10): Löschkonzept je Tabelle folgt vor
  Produktivbetrieb; bis dahin gelten die Löschverbote.
- **pgvector mit fester Dimension**: erst nach der B-47-Modellauswahl (Embeddings sind
  bis dahin modellagnostisch als `real[]` gespeichert).
- **Durchsetzung der Rechtematrix und echte DB-Rollentrennung**: App-Schicht bzw.
  Betriebskonzept (C-11); die Matrix liegt als beschlossene Stammdaten vor.
- **Wertgrenzen-Automatik** (B-16/A-26): Grenzen sind pflegbare Stammdaten ohne
  erfundene Beträge; die Prüfung greift, sobald GF-Werte gepflegt sind (App-Schicht).
