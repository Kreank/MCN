# Claim-Evidenz-Matrix

> Diese Matrix verhindert, dass geplante Funktionen als fertiges Produkt oder
> qualitative Vorteile als gemessene Einsparung dargestellt werden.
>
> **Stand 28.07.2026:** Alle Einträge sind gegen die Funktions- und
> Reifegradanalyse (`05-funktions-und-reifegradanalyse.md`) abgeglichen. Zahlen
> und Belegstellen dort sind maßgeblich.

| Aussage | Klasse | Evidenz | Zulässige externe Formulierung |
|---|---|---|---|
| MCN läuft mit echten Betriebsdaten | PRODUKTIV | `docs/HANDOFF.md`, Deploymentstand | „MCN wird in einem SHK-Referenzbetrieb mit echten Daten eingesetzt.“ |
| rund zwei Millionen Artikel | PRODUKTIV | `docs/HANDOFF.md`, Importchronik | „Der produktive Artikelstamm umfasst rund zwei Millionen Datensätze.“ |
| Kontakte, Objekte, Aufträge, Planung und Abrechnung sind verbunden | UMGESETZT | API-, Service- und Frontendmodule | „MCN bildet die Prozesskette in einem gemeinsamen Daten- und Arbeitskontext ab.“ |
| Datenbank erzwingt Fachregeln | UMGESETZT | `db/migrations`, `docs/INVARIANTEN.md` | „Zentrale Status-, Beleg- und Schutzregeln werden in PostgreSQL technisch durchgesetzt.“ |
| KI hat keinen direkten Schreibweg | UMGESETZT | AI Proposal/Executor, DB-Trigger | „KI-Vorschläge durchlaufen dieselben fachlichen Tore wie menschliche Aktionen.“ |
| KI kann vollständig lokal laufen | TEILWEISE | Adapter (`ai/llm.py`), Profile über `MCN_AI_PROFILES`, `LOCAL_ONLY`. **Ohne gesetztes Profil greift ein `FakeBackend`** — kein Lauf mit realem Modell nachgewiesen | „Die Architektur unterstützt lokal betriebene Modelle ohne zwingende Cloud-Übertragung.” |
| Sprachmemo erzeugt Baustellenbericht | TEILWEISE | Workflow und Tests; echtes ASR-Gerät offen | „Der Workflow ist implementiert; die Validierung mit realer ASR-Hardware ist Teil der nächsten Phase.“ |
| MCN ist GoBD-konform | NICHT FREIGEGEBEN | technische Maßnahmen, keine Gesamtprüfung | Nicht pauschal behaupten; einzelne Maßnahmen konkret benennen. |
| MCN ist mandantenfähig | FALSCH HEUTE (belegt) | `company.company_profile.is_singleton boolean NOT NULL DEFAULT true` — eine Firma je Datenbank, keine `tenant_id` | „Der aktuelle Betrieb ist je Firma isoliert; die Skalierungsform wird produktstrategisch festgelegt.” |
| MCN spart X Prozent Zeit | HYPOTHESE | Messdaten fehlen | Erst nach Vorher-/Nachher-Messung beziffern. |
| MCN reduziert Medienbrüche | UMGESETZT/QUALITATIV | durchgängiges Domänenmodell | „Informationen aus mehreren Prozessschritten werden im gemeinsamen Arbeitskontext geführt.“ |
| ZUGFeRD/Factur-X ist umgesetzt | **UMGESETZT + EXTERN VALIDIERT** | API, Tests, Archivierung; **veraPDF 1.30.2 (PDF/A-3B) und Mustang 2.24.0 (EN16931-Schematron) an sechs Belegformen ohne Verstoß** (`services/erechnung.py`, `docs/erechnung-validierung.md`) | „MCN erzeugt und archiviert ZUGFeRD/Factur-X-Rechnungen im Profil EN16931; PDF/A-3B und EN16931 sind mit den Referenzvalidatoren geprüft.” |
| XRechnung ist vollständig umgesetzt | NICHT FREIGEGEBEN | dokumentierte offene B2G-Regeln | Nicht behaupten. |
| DATEV-Export ist vorhanden | UMGESETZT | `services/datev.py`, API, Tests | „MCN erzeugt DATEV-EXTF-Buchungsstapel; der reale Steuerberater-Roundtrip ist zu validieren.“ |
| DATANORM und IDS-Connect sind vorhanden | UMGESETZT | Import, Punchout, Warenkorb, Tests | „Branchenübliche Artikel- und Händlerprozesse sind technisch integriert.“ |
| System skaliert auf Tausende Kunden | HYPOTHESE | Single-Tenant, Produktisierung offen | „Die technische Produktisierung für viele getrennte Kundeninstanzen ist Bestandteil der Skalierungsroadmap.” |
| Fachregeln sind physisch in der Datenbank durchgesetzt | UMGESETZT (gemessen) | 168 Fachregel-Trigger, 321 Schutz-Trigger, 660 CHECK-Constraints, 17 EXCLUDE-Constraints, 341 PL/pgSQL-Funktionen (Erhebung 28.07.2026) | „Zentrale kaufmännische und arbeitsrechtliche Regeln werden in PostgreSQL physisch durchgesetzt und sind auch für die KI nicht umgehbar.” |
| Umfang der Testabsicherung | UMGESETZT (gemessen) | 187 Testdateien, **4.187 ausgeführte Testfälle bestanden**, 15 übersprungen, 19 Teardown-Artefakte, Laufzeit 18:05 (Lauf vom 28.07.2026) | „Die Backend-Testsuite umfasst 187 Dateien und 4.187 Testfälle und läuft ohne fachlichen Fehlschlag durch.” **Nicht** „fehlerfrei” ohne Erläuterung der 19 Teardown-Fehler. |
| Benutzerverwaltung im Produkt | **FEHLT** | kein Endpunkt, keine Oberfläche; Benutzer entstehen nur über das gesperrte `/admin/` | Nicht behaupten. Als offener Produktisierungsschritt benennen. |
| RAG / durchsuchbares Firmenwissen | GEPLANT | `ai.embedding` existiert als Tabelle, **pgvector ist nicht installiert**; kein Vektorindex, keine Ähnlichkeitssuche | „Eine rechtegefilterte Wissensbasis ist architektonisch entschieden (pgvector im bestehenden Postgres) und für eine spätere Ausbaustufe vorgesehen.” |
| Mobile App für den Außendienst | GEPLANT | Geräte-Token-Anmeldung und rechtegefilterte Monteur-Objektsicht vorhanden; **keine App** | „Die Schnittstellen für native Clients sind vorbereitet; die Android-App ist Teil der Roadmap.” |
| Mailversand (Angebot, Rechnung, Mahnung) | TEILWEISE | Versandpfad vollständig implementiert und getestet, in der Live-Instanz durch `MCN_EMAIL_BACKEND=console` stillgelegt | „Der Versandpfad ist implementiert; die Scharfschaltung erfolgt bewusst kontrolliert je Betrieb.” |
| Zahlungsabgleich mit der Bank | **FEHLT** | kein CAMT/MT940-Import; Zahlungen werden manuell erfasst | Nicht behaupten. |

