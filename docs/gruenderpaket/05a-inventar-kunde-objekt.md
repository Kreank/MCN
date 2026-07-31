# Funktionsinventar A — Kunde, Objekt und Beteiligte

> Teil der Funktions- und Reifegradanalyse. Einstieg und Methodik:
> `05-funktions-und-reifegradanalyse.md`. Stichtag der Erhebung: **28.07.2026**,
> Arbeitsstand `develop` @ `0281db9`.

Abgedeckte Rechte-Module: `identity`, `property`, `tenure`, `management`,
`content`. Zusammen **79 der 405 API-Operationen**.

Legende Reifegrad: **P** produktiv ausgerollt · **U** umgesetzt und getestet ·
**T** teilweise · **G** geplant · **F** fehlt.
Spalte „Live" = im ausgerollten Stand (Commit `0fb1ae1`, Migrationskopf 0134)
enthalten.

---

## A1 Kontakte und Beteiligte (`identity`, 20 Operationen)

| Funktion | Reife | Live | Evidenz | Anmerkung |
|---|:--:|:--:|---|---|
| Personen und Organisationen als getrennte Partei-Arten | U | ✔ | `POST /identity/parties/person\|organization`, `services/identity.py` | Gemeinsame `party`-Wurzel, keine Doppelpflege |
| Kontakt bearbeiten (Person/Organisation getrennt) | U | ✔ | `PATCH /identity/parties/{id}/person\|organization` | |
| Ansprechpartner an Organisationen | U | ✔ | `…/contact-persons` (3 Op.) | Beziehung, kein Feld — beendbar statt löschbar |
| Kontaktwege (Telefon, E-Mail, Fax …) mit Deaktivierung | U | ✔ | `…/contact-points` (4 Op.) | Kein Löschen, nur `deactivate` |
| Adressen mit Historie: anlegen, ändern, beenden, ersetzen | U | ✔ | `…/addresses` (5 Op.) | „Ersetzen" hält den Umzug als Historie fest |
| Akquisekanal je Kontakt | U | ✔ | `PUT …/acquisition-source`, Stammdaten in `company` | Vertriebsauswertung möglich |
| Freitext-Notiz am Kontakt | U | ✔ | `PUT …/note` | |
| Dublettenprüfung bei der Erfassung | U | ✔ | `services/kontakt_steckbrief.py`, `api/tests/test_dubletten_api.py` | Adress- und Steckbriefsuche vor dem Anlegen |
| Kontakt-Steckbrief (Kurzsicht für Auswahl-Dialoge) | U | ✔ | `services/kontakt_steckbrief.py`, `db_core/tests/test_kontakt_steckbrief.py` | |
| Kontakt-Dossier (eine Antwort, rechtegefiltert) | U | ✔ | `GET /dossier/kontakt/{id}` | Kern hart getort, Bausteine weich |
| Kunden-Frontend | U | ✔ | `features/kontakte`, `features/kontakt-detail` | |

**Nicht vorhanden:** Lead-/Opportunity-Trichter, Kampagnen, Serienmail,
Kontakt-Import aus Fremdsystemen (CSV/vCard/Outlook). Für den Markteintritt ist
der **Import** die relevanteste Lücke — siehe `05h`.

---

## A2 Liegenschaften, Gebäude, Einheiten, Räume (`property`, 27 Operationen)

| Funktion | Reife | Live | Evidenz | Anmerkung |
|---|:--:|:--:|---|---|
| Liegenschaft anlegen/ändern/lesen, inkl. EFH-Typ | U | ✔ | `/property/properties` (5 Op.), `services/property.py` | |
| Gebäude an der Liegenschaft | **T** | ✔ | `POST …/buildings`, `PATCH /buildings/{id}` | **Lücke: `BuildingIn` kennt kein Adressfeld** (`api/property.py:572`); die Spalte `building.address_id` existiert, ist über die API aber nicht befüllbar |
| Einheiten (Wohnungen/Gewerbe) inkl. Etage | U | ✔ | `POST /buildings/{id}/units`, `PATCH /units/{id}`, Migration 0124 | `storey` ist bewusst Freitext |
| Räume als Objektstammdatum | U | ✔ | `/property/properties/{id}/rooms`, `services/raum.py` | No-Delete, nur INAKTIV |
| Raumaufmaß (Flächen, Wände, Öffnungen) | U | ✔ | `PUT /rooms/{id}/aufbau`, `db_core/tests/test_raumaufmass.py` | Fläche ist die Wahrheit, l×b nur Herleitung |
| Grundriss-Editor (Zeichnen **und** Kantenliste) | U | ✔ | `PUT /rooms/{id}/grundriss`, `services/…`, `frontend/…/grundriss` | Ganzzahlige mm im Geschoss-System; tastaturbedienbar (WCAG) |
| Überschlägige Heizlast + Auslegungsdaten am Objekt | U | ✔ | `PATCH …/auslegung`, `GET …/aufmass` | **Ausdrücklich kein DIN-EN-12831-Nachweis**, keine Normtabellen im Produkt |
| Bauteilkatalog als Kopierquelle | U | ✔ | `/property/component-templates` (3 Op.), `services/bauteilkatalog.py` | Wird **ohne** U-Werte ausgeliefert (29 Zeilen, nur Namen) |
| Technische Anlagen an Objekt/Einheit | U | ✔ | `/property/properties/{id}/assets`, `services/anlage.py` | |
| Anlagenkarte mit Etage und Bewohner | U | **✘** | Commit `1579063`, `features/anlage-detail` | Erst nach dem letzten Deploy entstanden |
| Gebäudeansicht (Objekt als Haus, Etagen, Technik) | U | **✘** | `GET …/gebaeudeansicht`, `services/gebaeudeansicht.py`, Commit `0281db9` | Etagenreihenfolge wird **abgeleitet, nie gespeichert** |
| Kopfzeile Liegenschaft (wer gehört dazu, wer darf beauftragen) | U | ✔ | `GET …/kopfzeile`, `services/property_steckbrief.py` | |
| Adress-Dublettenprüfung | **T** | ✔ | `GET /properties/adress-dubletten` | Trefferart `GEBAEUDE` greift praktisch nicht, weil Gebäudeadressen nicht erfassbar sind (siehe oben) |
| Monteur-Objektsicht (ganzes Objekt, nie Preise) | U | ✔ | `services/objektsicht.py`, `api/tests/test_monteur_objektsicht.py` | Fremdes Objekt → 404 |
| Liegenschafts-Frontend | **T** | ✔ | `features/liegenschaft-detail` | **11 Reiter** — im Praxistest als zu viel bewertet; Verschlankung auf 6 ist recherchiert, aber nicht entschieden |

**Bewertung:** Der Objektteil ist das fachliche Alleinstellungsmerkmal und
technisch der tiefste Bereich des Systems (Liegenschaft → Gebäude → Einheit →
Raum → Wand/Öffnung → Anlage). Zwei Einschränkungen sind ehrlich zu benennen:
die fehlende Gebäudeadresse (blockiert die WEG-über-mehrere-Adressen-Struktur)
und die Bedienlast der Objektmappe.

---

## A3 Belegung und Eigentum (`tenure`, 13 Operationen)

| Funktion | Reife | Live | Evidenz | Anmerkung |
|---|:--:|:--:|---|---|
| Belegung (Mietverhältnis) je Einheit/Objekt | U | ✔ | `/tenure/properties/{id}/belegung`, `services/belegung.py` | |
| Mieter an der Belegung, beendbar | U | ✔ | `POST …/mieter`, `POST /mieter/{id}/beenden` | Keine `party_id` am Belegungssatz — **eine** Wahrheit |
| Eigentumsperioden mit Anteilen | U | ✔ | `/tenure/properties/{id}/eigentum` (5 Op.), `services/eigentum.py`, Migration 0133 | Anteile als Bruch, Bestätigung als eigener Schritt |
| Eigentümer je Periode | U | ✔ | `POST …/eigentuemer`, `PATCH /eigentuemer/{id}` | |
| Übernahme der Eigentümer aus der Belegung | U | ✔ | `db_core/tests/…test_belegung_eigentuemer_uebernahme.py` | |
| Kein Eigentum oberhalb der Einheit an Gemeinschaftsflächen | U | ✔ | Trigger `trg_ownership_no_common_area` | Physisch erzwungen |
| Frontend Belegung/Eigentum | U | ✔ | `features/belegung`, `features/eigentum` | |

**Offene fachliche Lücken (benannt, nicht behoben):**

1. **Eigentümer doppelt erfassbar.** Die Objektrolle `PROPERTY_OWNER`
   (`property_party_role`) und der Eigentumsstand (`tenure.ownership_*`) kennen
   einander nicht; es gibt weder Abgleich noch Hinweis. Ein Betrieb kann
   denselben Eigentümer zweimal verschieden pflegen. → Datenqualitätsrisiko.
2. **Die Schnellaufnahme kennt keine Mieter.** `quick_intake`
   (`api/projekt.py:1310`) trägt den Melder bei neuer Liegenschaft **immer** als
   `PROPERTY_OWNER` ein; `tenure.occupancy` wird nie angefasst. Genau der
   häufigste Anruf (Mieter meldet Störung) erzeugt damit einen falschen
   Beteiligtentyp, den jemand später korrigieren muss.

Beides ist **kein Architekturfehler, sondern eine offene Erfassungsstrecke** —
die Zieldaten existieren, nur der Weg dorthin fehlt an einer Stelle.

---

## A4 Verwaltung und Vollmachten (`management`, 10 Operationen)

| Funktion | Reife | Live | Evidenz | Anmerkung |
|---|:--:|:--:|---|---|
| Verwaltungsmandat je Liegenschaft | U | ✔ | `/management/properties/{id}/mandate`, `services/verwaltung.py` | Hausverwaltung als eigenes Rechtsverhältnis, nicht als Kontaktrolle |
| Zuständigkeiten im Mandat, beendbar | U | ✔ | `POST …/zustaendigkeiten`, `POST /zustaendigkeiten/{id}/beenden` | |
| Vollmachten anlegen und widerrufen | U | ✔ | `/management/vollmachten`, `services/vollmacht.py`, Migration 0134 | |
| Auskunft „Wer darf hier beauftragen?" | U | ✔ | `GET /properties/{id}/darf-beauftragen` | Beantwortet die Frage, an der im Handwerk Aufträge platzen |
| Frontend | U | ✔ | `features/verwaltung`, `frontend/…/verwaltung.spec.ts` | |

**Bewertung:** Dieser Block ist im Wettbewerbsvergleich ungewöhnlich — die
meisten Handwerker-Systeme kennen nur „Kunde". WEG-Verwaltung, Vollmacht und
Beauftragungsberechtigung sind hier eigene, prüfbare Entitäten. Für das
Zielsegment Gebäudeservice/Mehrfamilienhaus ist das ein echtes Argument.

---

## A5 Dateien und Dokumente (`content`, 9 Operationen)

| Funktion | Reife | Live | Evidenz | Anmerkung |
|---|:--:|:--:|---|---|
| Upload/Download gegen MinIO | U | ✔ | `/content/files`, `db_core/storage.py`, `db_core/tests/test_storage_minio_e2e.py` | Download über Blob (Auth-Cookie/CSRF), nicht `window.open` |
| Verknüpfung Datei ↔ Entität, lösbar ohne Datenverlust | U | ✔ | `DELETE /content/links/{id}` | Datei bleibt, nur die Verknüpfung geht |
| Dateikategorien pflegbar | U | ✔ | `/content/file-categories` (5 Op.), Migration 0127/0128 | |
| Verdrahtet in den Arbeitsmappen | U | ✔ | Projekt, Kontakt, Liegenschaft, Angebot, Rechnung, Offener Posten, Vorgang, Auftrag, Einsatz | **Offen:** Einheit und Anlage haben keine eigene Dateiablage |
| Dedup gleicher Inhalte | U | ✔ | `db_core/storage.py` | **Ausnahme Attest:** Dedup aus, eigenes Objekt (DSGVO Art. 9) |

**Betriebsrisiko (dokumentiert, in `05g` bewertet):** Unterschriften unter
Baustellenberichten, Baustellenfotos und Atteste existieren **nur** als Datei in
MinIO. Ohne MinIO bleibt ein versiegelter Bericht ohne die Unterschrift, wegen
der er existiert.

---

## Zusammenfassung Block A

| Bereich | Operationen | Reife | Wesentliche Lücke |
|---|---:|---|---|
| Kontakte | 20 | hoch | kein Import aus Fremdsystemen |
| Liegenschaften/Räume/Anlagen | 27 | hoch | Gebäudeadresse nicht erfassbar; 11 Reiter |
| Belegung/Eigentum | 13 | mittel-hoch | doppelte Eigentümerpflege; Mieter in der Schnellaufnahme |
| Verwaltung/Vollmacht | 10 | hoch | — |
| Dateien | 9 | hoch | Einheit/Anlage ohne eigene Ablage |

**Belastbare externe Formulierung:** „MCN führt Kunde, Liegenschaft, Gebäude,
Einheit, Raum, Anlage, Mietverhältnis, Eigentum und Verwaltungsmandat in einem
gemeinsamen Datenmodell — nicht als Adressliste mit Anhängen." Nicht behaupten:
vollständige WEG-Abbildung über mehrere Gebäudeadressen (dafür fehlt die
Erfassung).
