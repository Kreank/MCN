# Funktionsinventar C — Angebot, Rechnung, Buchhaltung, Auswertung

> Teil der Funktions- und Reifegradanalyse. Einstieg: `05-funktions-und-reifegradanalyse.md`.
> Stichtag **28.07.2026**, Arbeitsstand `develop` @ `0281db9`.

Abgedeckte Rechte-Module: `invoicing`, `buchhaltung`, `accounting`,
`auswertungen`. Zusammen **71 der 405 API-Operationen**.

Dies ist der **GoBD-relevante Teil** des Systems und damit der Bereich, in dem
Fehler Geld und Rechtssicherheit kosten. Entsprechend dicht sind hier die
Datenbank-Tore.

Legende: **P** produktiv ausgerollt · **U** umgesetzt und getestet · **T** teilweise ·
**G** geplant · **F** fehlt. „Live" = im ausgerollten Stand (`0fb1ae1`, Migration 0134).

---

## C1 Angebote (`invoicing`, 14 Operationen)

| Funktion | Reife | Live | Evidenz | Anmerkung |
|---|:--:|:--:|---|---|
| Angebot anlegen, ändern, kopieren | U | ✔ | `/invoicing/quotes` (5 Op.), `services/beleg.py` | |
| Positionen: Artikel, Leistung, Text, Rubriken | U | ✔ | `db_core/tests/test_beleg_rubriken.py` | Position ist eine **Kopie**, kein Verweis |
| Kalkulation je Beleg (EK, Marge, Zuschläge) | U | ✔ | `GET …/kalkulation`, `services/kalkulation.py` | |
| Arbeitskostenanteil je Position (§ 35a EStG) | U | ✔ | `services/beleg_arbeitskosten.py`, `api/tests/test_beleg_arbeitskosten_api.py` | **Unbestimmt ist nicht null** — bei PAUSCHALE/FREMDLEISTUNG bleibt der Ausweis leer, statt zu raten |
| Mengenangebot / Aufmaßbezug | U | ✔ | `/invoicing/quotes/mengen` (2 Op.), `features/angebot-mengen` | |
| Statuswechsel und Versand | U | ✔ | `POST …/status`, `POST …/send` | |
| PDF und PDF-Vorschau | U | ✔ | `GET …/pdf`, `…/pdf/vorschau`, `services/beleg_pdf.py` | fpdf2, eingebettete DejaVu (PDF/A verbietet Kernfonts) |
| Versand per E-Mail | **T** | ✔ | `POST …/send-email`, `services/beleg_versand.py` | Technisch fertig, **in der Live-Instanz durch `MCN_EMAIL_BACKEND=console` stillgelegt** |
| Angebots-Editor im Frontend | U | ✔ | `features/angebot-editor` | Rechnet **keine Summen** — der Server ist verbindlich |

## C2 Rechnungen (`invoicing`, 21 Operationen)

| Funktion | Reife | Live | Evidenz | Anmerkung |
|---|:--:|:--:|---|---|
| Rechnung frei anlegen | U | ✔ | `POST /invoicing/invoices` | Lehnt Kredit-Typen ab — Folgebelege nur über eigene Funktionen |
| Rechnung aus Angebot / Auftrag / Nachtrag | U | ✔ | `POST …/aus-angebot\|aus-auftrag\|aus-nachtrag` | Die drei realen Wege aus der Leistung in den Beleg |
| Abschlagsrechnung und Anrechnung | U | ✔ | `PUT …/advances`, `GET …/anrechenbare-abschlaege` | Anrechnung als **negative Positionen je Steuersatz**, nicht als Kopffeld |
| Schlussrechnung | U | ✔ | `db_core/tests/test_schlussrechnung_service.py`, `…_ausgabe.py` | Doppelanrechnung physisch ausgeschlossen (Service **und** DB) |
| Storno und Rechnungskorrektur | U | ✔ | `POST /buchhaltung/invoices/{id}/cancel\|correction` | Vollgutschrift auf gebundene Rechnung verboten, Teilgutschrift erlaubt |
| Veröffentlichung (Festschreibung) | U | ✔ | `POST …/publish`, `db_core/tests/test_beleg_publish_service.py` | Danach unveränderlich; Snapshot wird eingefroren |
| Doppelabrechnungssperre | U | ✔ | `invoicing.billing_link`, drei partielle UNIQUE; `services/abrechnung.py` | Sperre in der **Datenbank**; Storno löst die Bindung |
| Skonto | U | ✔ | `services/beleg.py::zahlungsbedingungen`, `api/tests/test_beleg_skonto_api.py` | Genau eine Rechenstelle für PDF, XML, API und Frontend |
| PDF-Ausfertigung + GoBD-Archivierung in MinIO | U | ✔ | `GET …/pdf`, `db_core/tests/test_beleg_pdf_archiv.py`, Migration 0032/0059 | Einmaligkeits-Index; degradiert auf On-the-fly statt zu scheitern |
| ZUGFeRD / Factur-X (PDF/A-3B + CII-XML) | U | ✔ | `GET …/zugferd.pdf`, `…/zugferd.xml`, `services/erechnung.py` | siehe C5 |
| Rechnungsversand per E-Mail | **T** | ✔ | `POST …/send-email` | wie C1: technisch fertig, betrieblich stillgelegt |

## C3 Offene Posten, Zahlungen, Mahnwesen (`buchhaltung`, 14 Operationen)

| Funktion | Reife | Live | Evidenz | Anmerkung |
|---|:--:|:--:|---|---|
| Offene-Posten-Liste und Detail | U | ✔ | `/buchhaltung/invoices` (2 Op.) | Offener Betrag **abgeleitet**, nicht gespeichert |
| Zahlung erfassen und stornieren | U | ✔ | `POST …/payments`, `POST /payments/{id}/reverse` | Erhaltungssatz Σ offen = Σ Brutto − Σ Gezahltes, cent-genau |
| Forderungsgrenze an genau einer Stelle | U | ✔ | `services/buchhaltung.py`, `api/tests/test_forderung_grenze_api.py` | Vorher blieb eine stornierte Rechnung offener Posten **und** Mahnkandidat |
| Mahnstufen konfigurierbar | U | ✔ | `/buchhaltung/dunning-levels` (2 Op.) | Aktive Stufen müssen lückenlosen Präfix bilden; `fee`/`interest_note` bleiben NULL (Steuerberater-Vorbehalt) |
| Einzelmahnung | U | ✔ | `POST /invoices/{id}/dunning` | |
| Mahnlauf mit Vorschau | U | ✔ | `GET /mahnlauf/vorschau`, `POST /mahnlauf`, `services/mahnlauf.py` | Vorschau == Ausführung (derselbe Code) |
| Mahnungsversand per E-Mail | **T** | ✔ | `POST /dunning-notices/{id}/send-email` | **Die einzige Aktion im System, die nach außen wirkt** — betrieblich stillgelegt |
| Frontend | U | ✔ | `features/buchhaltung`, `features/mahnwesen`, `features/mahnlauf`, `features/mahnstufen` | |

**Nicht vorhanden:** Bankanbindung/Kontoauszug-Import (CAMT/MT940), automatischer
Zahlungsabgleich, SEPA-Lastschrift. Zahlungen werden heute **von Hand erfasst**.
Das ist für den Markteintritt eine spürbare Lücke — siehe `05h`.

## C4 Eingangsbelege und Kontenrahmen (`accounting`, 11 Operationen)

| Funktion | Reife | Live | Evidenz | Anmerkung |
|---|:--:|:--:|---|---|
| Eingangsbelege erfassen, ändern, Status fortschreiben | U | ✔ | `/accounting/receipts` (5 Op.), `services/belegerfassung.py` | **Eigene Tabelle** `accounting.receipt` — bewusst keine gerichtete `invoice` |
| Sachkonten pflegen | U | ✔ | `/accounting/ledger-accounts` (3 Op.) | |
| Kostenstellen pflegen | U | ✔ | `/accounting/cost-centers` (3 Op.) | |
| Frontend | U | ✔ | `features/belegerfassung`, `features/accounting-stammdaten` | |

**Reifegrad ehrlich:** Das ist ein **Grundstock**, keine Kreditorenbuchhaltung.
Es fehlen Belegscan/OCR, Zahlungsvorschlag, Lieferantenkontenabstimmung und ein
Freigabe-Workflow für Eingangsrechnungen.

## C5 E-Rechnung und DATEV

| Funktion | Reife | Live | Evidenz | Anmerkung |
|---|:--:|:--:|---|---|
| ZUGFeRD/Factur-X, Profil **EN16931** | U | ✔ | `services/erechnung.py` | XML über die Referenzbibliothek `factur-x`, gegen XSD validiert |
| **PDF/A-3B extern bestätigt** | U | ✔ | veraPDF 1.30.2, `isCompliant=true` für alle geprüften Belegformen | inkl. Logo mit Alphakanal (SMask-Falle) |
| **EN16931 extern bestätigt** | U | ✔ | Mustang 2.24.0, XSD + EN16931-Schematron, kein BR-/BR-CO-Verstoß | geprüft an **sechs** Belegformen: Skonto, ohne Skonto, zwei Steuersätze, Schlussrechnung mit negativen Anrechnungen, Storno, Logo |
| Archivierung der E-Rechnung | U | ✔ | eigene `link_category='E_RECHNUNG'`, Migration 0059 | Quelle ist ausschließlich der eingefrorene `billing_snapshot` |
| **XRechnung / PEPPOL (B2G)** | **F** | — | bewusst ausgeklammert, dokumentiert | Leitweg-ID, BT-10/BT-24/BT-41 fehlen. **Nicht behaupten.** |
| DATEV-EXTF-Buchungsstapel | U | ✔ | `GET /buchhaltung/datev-export.csv`, `services/datev.py`, `db_core/tests/test_datev_service.py` | Sammeldebitor, Automatikkonten, cent-genaue Reconciliation |
| DATEV-Modus ANZAHLUNG | U | ✔ | Leistungsteil wird als **Rest** ermittelt, nie neu gerechnet | Moduswechsel bei offenen Abschlägen → 422 |
| Lexware-Export | **F** | — | entschieden: später | |

**Wichtige Einschränkung, die in jede externe Unterlage gehört:** Die
Anzahlungs-Standardkonten (SKR03 1718 / SKR04 3272 bei 19 %) sind eine
**begründete Annahme, kein DATEV-Standard** — mit dem Steuerberater zu klären.
Und: Der **reale Steuerberater-Roundtrip ist nicht durchgeführt**. Belegbar ist
„MCN erzeugt EXTF-Buchungsstapel", nicht „die Buchhaltung übernimmt sie
reibungslos".

## C6 Auswertungen (`auswertungen`, 11 Operationen)

| Dashboard | Reife | Live | Evidenz |
|---|:--:|:--:|---|
| Umsatz-/Projektübersicht | U | ✔ | `GET /auswertungen/umsatz-projektuebersicht` + CSV |
| Kunden | U | ✔ | `GET /auswertungen/kunden` + CSV |
| Projekte | U | ✔ | `GET /auswertungen/projekte` + CSV |
| Artikel | U | ✔ | `GET /auswertungen/artikel` + CSV |
| Mitarbeitende | U | ✔ | `GET /auswertungen/mitarbeitende` + CSV |
| Dashboard-Verzeichnis | U | ✔ | `GET /auswertungen/dashboards` |

Alle fünf Dashboards sind **CSV-exportierbar**. Frontend: `features/auswertungen*`
(sechs Komponenten).

**Bekannte Lücke:** Die **Marge** ist aus Belegzeilen nicht ableitbar; sie
bräuchte die Einkaufspreis-Ebene (`article_supplier_reference.last_purchase_price`)
oder den `billing_snapshot`. Solange das offen ist, zeigt das System Umsatz, aber
keinen belastbaren Deckungsbeitrag — für einen Handwerksbetrieb die eigentlich
interessante Zahl.

---

## Zusammenfassung Block C

| Bereich | Operationen | Reife | Wesentliche Lücke |
|---|---:|---|---|
| Angebote | 14 | hoch | — |
| Rechnungen | 21 | hoch | Versand betrieblich abgeschaltet |
| Offene Posten / Mahnwesen | 14 | hoch | kein Bank-/Kontoauszug-Import |
| Eingangsbelege | 11 | Grundstock | kein OCR, kein Freigabe-Workflow |
| E-Rechnung / DATEV | — | hoch (ZUGFeRD extern validiert) | XRechnung fehlt; Steuerberater-Roundtrip offen |
| Auswertungen | 11 | mittel | keine Marge |

**Belastbare externe Formulierung:** „MCN erzeugt ZUGFeRD/Factur-X-Rechnungen im
Profil EN16931; PDF/A-3B und EN16931 sind mit den Referenzvalidatoren veraPDF und
Mustang an sechs Belegformen ohne Verstoß geprüft. Ein DATEV-EXTF-Buchungsstapel
wird erzeugt."
**Nicht behaupten:** „GoBD-konform" pauschal, „XRechnung-fähig", „automatischer
Zahlungsabgleich".
