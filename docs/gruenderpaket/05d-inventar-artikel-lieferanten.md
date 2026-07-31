# Funktionsinventar D — Artikel, Preise, Lieferanten

> Teil der Funktions- und Reifegradanalyse. Einstieg: `05-funktions-und-reifegradanalyse.md`.
> Stichtag **28.07.2026**, Arbeitsstand `develop` @ `0281db9`.

Abgedeckte Rechte-Module: `pricing`, `geraetewissen`.
Zusammen **47 der 405 API-Operationen**.

Dieser Block trägt das quantitativ auffälligste Merkmal des Systems: den
produktiven Artikelstamm von rund **zwei Millionen Datensätzen** (Quelle:
`docs/HANDOFF.md`, Live-Instanz — aus dem Repository selbst nicht nachprüfbar).

Legende: **P** produktiv ausgerollt · **U** umgesetzt und getestet · **T** teilweise ·
**G** geplant · **F** fehlt. „Live" = im ausgerollten Stand (`0fb1ae1`, Migration 0134).

---

## D1 Artikel- und Leistungsstamm (`pricing`, 14 Operationen)

| Funktion | Reife | Live | Evidenz | Anmerkung |
|---|:--:|:--:|---|---|
| Artikel anlegen, ändern, kopieren, deaktivieren | U | ✔ | `/pricing/articles` (6 Op.), `services/artikel.py` | Kein Löschen — nur Statuswechsel |
| Leistungen (Lohnpositionen) im selben Stamm | U | ✔ | `features/leistung-detail`, `db_core/tests/test_artikel_status.py` | **Ein** Artikelstamm, mehrere Anbindungen — kein Silo je Quelle |
| Artikelhistorie | U | ✔ | `GET …/historie` | |
| Kalkulation je Artikel | U | ✔ | `GET …/kalkulation`, `db_core/tests/test_kalkulation_service.py` | |
| GTIN mit Prüfziffer | U | ✔ | `api/artikel.py::_gtin_gueltig` (Client spiegelt die Regel) | |
| Suche über den Millionenbestand | U | ✔ | `services/textsuche.py`, `db_core/tests/test_artikel_suche.py`, `…/test_suche_index.py` | Volltextindex |
| Preiseinheit (`price_unit`) korrekt behandelt | U | ✔ | Migration 0039, `db_core/tests/test_einkaufspreis_genauigkeit.py` | Klassische Fehlerquelle bei DATANORM-Daten |
| Baugruppen / Sets | U | ✔ | `/pricing/assemblies` (4 Op.) | |
| Bauteilkatalog | U | ✔ | `services/bauteilkatalog.py` | siehe `05a` |
| Frontend | U | ✔ | `features/artikel`, `features/artikel-detail` | Reiter Informationen · Kalkulation · Historie |

## D2 Verkaufspreislogik (`pricing`, 16 Operationen)

Die Preisbildung ist einer der am dichtesten abgesicherten Bereiche des Systems.

| Funktion | Reife | Live | Evidenz | Anmerkung |
|---|:--:|:--:|---|---|
| Aufschlagsmatrix (Regeln + Staffeln) | U | ✔ | `/pricing/markup-rules` (7 Op.), `services/aufschlagsmatrix.py` | |
| VK-Preisgruppen und Handpreise | U | ✔ | `/pricing/sale_price_groups` (3 Op.), `…/verkaufspreise` (2 Op.) | |
| VK-Vorschlag mit fester Rangfolge | U | ✔ | `GET …/vk-vorschlag` | Handpreis → VK-Gruppe → Matrix (Artikel > Warengruppe+Lieferant > Warengruppe > Lieferant > Standard) → Staffel → Mindestmarge → sonst `null` |
| Genau **eine** Rechenstelle für den VK | U | ✔ | `services/aufschlagsmatrix.py` | Ein gespeicherter MATRIX-Preis wird nirgends gelesen, sondern live nachgerechnet |
| Mindestmarge als Untergrenze, `ROUND_CEILING` | U | ✔ | Invariante + Test | Abrunden hätte die Untergrenze aufgehoben |
| Massenpflege mit Vorschau | U | ✔ | `POST /markup-rules/massenpflege` | Vorschau == Anwenden (derselbe Code, `dry_run`), idempotent, Handpreise unangetastet |
| Warengruppen-Liste | U | ✔ | `GET /markup-rules/warengruppen` | |
| Lohngruppen und Verrechnungssätze | U | ✔ | `/pricing/wage-groups`, `/pricing/wage_groups` (5 Op.), `services/lohngruppe.py`, Migration 0034 | **Zwei Endpunktfamilien mit Bindestrich und Unterstrich** — historisch gewachsen, sollte vor externer Nutzung vereinheitlicht werden |
| Fehlender Preis ist niemals 0 € | U | ✔ | `_ist_preis`, 422 `preis_unbekannt` | Vorschläge werden **nie** vorausgefüllt, es gibt **keinen** „später"-Knopf |
| Kein Schreibpfad Beleg → Artikelstamm | U | ✔ | statisch getestet | Ausnahme: transientes Häkchen mit eigenem Recht `pricing/AENDERN`; der **EK wird bewusst nie** übernommen |

**Bewertung:** Die Preislogik ist die technisch anspruchsvollste Einzelkomponente
und zugleich die mit dem klarsten wirtschaftlichen Schaden bei Fehlern (eine
Position mit 0,00 € auf einer plausibel aussehenden Rechnung). Dass „unbekannt"
konsequent von „null" getrennt wird, ist ein verkaufbares Qualitätsargument.

## D3 Lieferanten und Beschaffung (`pricing`, 14 Operationen)

| Funktion | Reife | Live | Evidenz | Anmerkung |
|---|:--:|:--:|---|---|
| Lieferantenanbindungen anlegen/ändern | U | ✔ | `/pricing/supplier-connections` (3 Op.), `services/anbindung.py` | Quellsystem und Namespace nach dem Anlegen **unveränderlich** |
| Zugangsdaten verschlüsselt hinterlegen | U | ✔ | `…/credentials` (2 Op.), `db_core/cred_crypto.py`, `db_core/tests/test_cred_crypto.py` | Fernet; der Schlüssel `MCN_MAIL_KEY` gehört in den Passwortmanager, nicht nur in die `.env` |
| Preis-Semantik als Konfiguration | U | ✔ | `net_price_semantics` (EINHEIT\|GESAMT), `services/anbindung.py:70` | Hintergrund: GC liefert `NetPrice` als Positionssumme, obwohl `PriceBasis=1.0` „je Einheit" behauptet. **Plausibilitäts-Warnung statt Auto-Umrechnung** |
| **DATANORM-Import** (Datei-Upload) | U | ✔ | `POST …/imports/datanorm`, `services/datanorm_import.py`, `api/tests/test_datanorm_import_api.py` | Zip-Bomben-Schutz, Dry-run, Vorschau; **löscht nicht, sondern setzt INAKTIV** |
| DATANORM-Parser (Satzarten, Hersteller, Blöcke) | U | ✔ | `services/datanorm.py`, `db_core/tests/test_datanorm_parser.py`, `…_bloecke.py`, `…_hersteller.py` | Vollkataloge laufen bewusst über das CLI (`manage.py datanorm_import`) |
| **IDS-Connect Punchout** | U | ✔ | `POST …/punchout`, `…/punchout-session`, `GET /punchout-sessions/{id}`, `services/punchout_session.py` | |
| **IDS-Warenkorb-Rückweg** | U | ✔ | `POST /warenkorb-return/{token}`, `POST …/warenkorb/preview`, `services/ids_warenkorb.py`, `db_core/tests/test_ids_warenkorb.py`, `api/tests/test_ids_roundtrip_api.py` | Token **einmalig einlösbar**, nur als SHA-256-Hash gespeichert |
| VK aus dem zurückgegebenen EK | U | ✔ | Invariante | Nicht aus dem Stamm-EK — sonst wäre der Tagespreis wertlos |
| Frontend | U | ✔ | `features/haendler-anbindungen`, `features/aufschlagsmatrix` | |

**Der einzige offene Punkt in diesem Block:** Der **Live-Test gegen eine echte
Großhandels-URL** (z. B. G.U.T.) ist nicht durchgeführt — die Shop-/Connector-URL
ist bewusst Konfiguration und muss vom Betrieb eingetragen werden. Belegt ist der
vollständige Roundtrip gegen die IDS-Schemata (`IDS/*.xsd`, Version 2.5); nicht
belegt ist das Verhalten eines konkreten Händlerportals.

## D4 Gerätewissen (`geraetewissen`, 3 Operationen)

| Funktion | Reife | Live | Evidenz | Anmerkung |
|---|:--:|:--:|---|---|
| Hersteller-Ersatzteilsuche (read-only) | U | ✔ | `/geraetewissen/*`, `services/geraetewissen.py`, Migration 0038 | Sicht auf `pricing.article`, gefiltert auf Hersteller-Namensräume — **kein zweiter Datentopf** |
| Frontend | U | ✔ | `features/geraetewissen` | |

**Offen:** Einkaufspreise für Vaillant-/Bosch-Ersatzteile über die passende
Rabattgruppe nachziehen (in `docs/HANDOFF.md` als optionaler Punkt notiert).

---

## Zusammenfassung Block D

| Bereich | Operationen | Reife | Wesentliche Lücke |
|---|---:|---|---|
| Artikelstamm | 14 | hoch (≈ 2 Mio Datensätze live) | — |
| Verkaufspreislogik | 16 | sehr hoch | doppelte Endpunktfamilie `wage-groups`/`wage_groups` |
| Lieferanten/DATANORM/IDS | 14 | hoch | kein Live-Test gegen ein echtes Händlerportal |
| Gerätewissen | 3 | hoch | EK für einzelne Hersteller fehlt |

**Was in diesem Block bewusst fehlt:** **Lagerverwaltung.** Bestände sind per
Datenbank-Beschluss verboten (kein `stock`-Feld). Das ist eine Festlegung, keine
Lücke — aber sie muss im Vertrieb aktiv kommuniziert werden, weil Wettbewerber
damit werben und mancher Betrieb es erwartet.

**Belastbare externe Formulierung:** „MCN führt einen Artikel- und
Leistungsstamm mit branchenüblicher Preislogik (Aufschlagsmatrix, Staffeln,
Mindestmarge), importiert DATANORM-Kataloge und wickelt IDS-Connect-Punchout
inklusive Warenkorb-Rückweg ab."
**Nicht behaupten:** produktive Anbindung an einen bestimmten Großhändler,
Lagerhaltung.
