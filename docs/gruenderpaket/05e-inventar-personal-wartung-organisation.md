# Funktionsinventar E — Personal, Wartung, Organisation, Rechte

> Teil der Funktions- und Reifegradanalyse. Einstieg: `05-funktions-und-reifegradanalyse.md`.
> Stichtag **28.07.2026**, Arbeitsstand `develop` @ `0281db9`.

Abgedeckte Rechte-Module: `hr`, `maintenance`, `company`, `security` sowie die
Querschnittsfunktionen Suche, Dossier und Authentifizierung.
Zusammen **87 der 405 API-Operationen** (`hr` 24, `maintenance` 20, `company` 18,
`security` 11, `auth` 9, `suche` 1, `dossier` 4).

Legende: **P** produktiv ausgerollt · **U** umgesetzt und getestet · **T** teilweise ·
**G** geplant · **F** fehlt. „Live" = im ausgerollten Stand (`0fb1ae1`, Migration 0134).

---

## E1 Personal (`hr`, 24 Operationen)

Eigenes Fachschema `hr` — bewusst getrennt von `security`: `security` beantwortet
„darf dieser Account etwas?", `hr` „welche arbeitsrechtliche Beziehung besteht?".
Personendaten werden nicht dupliziert; `hr.employee` ankert per FK auf
`security.app_user` und `identity.person`.

| Funktion | Reife | Live | Evidenz | Anmerkung |
|---|:--:|:--:|---|---|
| Mitarbeitende anlegen, lesen, Status setzen | U | ✔ | `/hr/employees` (4 Op.), `services/mitarbeiter.py` | Personalnummer `MA-00001` aus eigener Sequenz (kein GoBD-Belegkreis); AUSGETRETEN ist final |
| Arbeitsverträge, versioniert und überlappungsfrei | U | ✔ | `POST …/contracts`, `POST /contracts/{id}/terminate` | EXCLUDE über `daterange`; Beginn, Sollstunden-Raster, Urlaubsanspruch und Lohngruppe sind nach dem INSERT **physisch unveränderlich** |
| Abwesenheiten mit Statusautomat | U | ✔ | `/hr/absences` (5 Op.) | ENTWURF→EINGEREICHT→GENEHMIGT\|ABGELEHNT (+ZURÜCKGEZOGEN); Ablehnung begründungspflichtig (CHECK) |
| Eigene Abwesenheitsanträge stellen | U | ✔ | Migration 0130, `api/tests/test_eigene_abwesenheit_api.py` | |
| Urlaubsbudget je Jahr | U | ✔ | `PUT …/vacation-budget` | Verbrauch ist **abgeleitet**, nicht gespeichert |
| Resturlaubs-Übertrag mit Vorschau | U | ✔ | `/hr/urlaubsuebertrag` (2 Op.), Migration 0131 | **Setzt**, addiert nicht. **Verfall standardmäßig AUS** (§ 7 Abs. 3 BUrlG erlaubt den 31.03.-Verfall, ordnet ihn nicht an) |
| Attest-Upload | U | ✔ | `api/tests/test_attest_api.py` | **Immer eigenes Speicherobjekt, Dedup aus.** Fremdzugriff → **404, nicht 403** (ein 403 bestätigte die Existenz der Krankmeldung). Keine Diagnose gespeichert |
| Qualifikationen und Nachweise | U | ✔ | `/planung/mitarbeiter/{id}/qualifikationen` (3 Op.) | Nachweise hängen am Recht `hr`, nicht `workflow` |
| Gewerke je Mitarbeiter | U | ✔ | Migration 0120/0121 (`hr.employee_trade`) | |
| Abwesenheiten als CSV | U | ✔ | `GET /hr/abwesenheiten.csv` | |
| Eigene Personalakte | U | ✔ | `GET /hr/self`, `features/meine-personalakte` | |
| Frontend | U | ✔ | `features/mitarbeiter`, `features/mitarbeiter-detail`, `features/planung-abwesend` | |

**Bewusst ausgeklammert (keine Lücke, sondern Entscheidung):** Steuer- und
Bankdaten (DSGVO Art. 9/32 — `security.four_eyes_action` kennt bereits
`'BANKDATEN'`, app-seitig nicht durchgesetzt). **Echte Lücken:** kein
Lohnexport, die Urlaubstage-Zählung kennt keine Feiertage (bewusst, weil eine
Umstellung rückwirkend Urlaubssalden ändern würde), jahresübergreifende Urlaube
werden dem Startjahr zugerechnet, unterjähriger Eintritt kürzt den Anspruch nicht
automatisch.

---

## E2 Wartung, Prüfungen, Fristen (`maintenance`, 20 Operationen)

| Funktion | Reife | Live | Evidenz | Anmerkung |
|---|:--:|:--:|---|---|
| Wartungsverträge mit Statusautomat | U | ✔ | `/maintenance/contracts` (4 Op.), `services/wartung.py` | AKTIV↔INAKTIV→ARCHIVIERT, eigener Belegkreis „W" |
| **Vertrag ↔ Anlage (n:m)** | U | **✘** | `PUT …/contracts/{id}/assets`, `maintenance.contract_asset`, Migration 0135/0136 | Leere Zuordnung heißt „gilt fürs ganze Objekt"; Vertrag und Anlage müssen über **zwei zusammengesetzte FKs** zur selben Liegenschaft gehören |
| Aktion auslösen (Aufgabe / Projekt / Auftrag) | U | ✔ | `POST …/trigger` | Der erzeugte Auftrag erbt die Anlage, wenn es genau eine ist |
| Fälligkeiten zentral, mit Erledigen/Verwerfen | U | ✔ | `/maintenance/due-items` (3 Op.), `services/faelligkeit.py` | Idempotenz garantiert die **Datenbank** (drei partielle UNIQUE, statusunabhängig) |
| Prüfungen und Prüfarten | U | ✔ | `/maintenance/inspections`, `…/inspection-types` (7 Op.), `services/pruefung.py` | |
| Gewährleistungen + Vorgaben | U | ✔ | `/maintenance/warranties` (4 Op.), `services/gewaehrleistung.py` | `basis` (BGB/VOB) ist ein **Etikett**, maßgeblich ist `duration_months` — kein Rechtsrat im Produkt |
| Scheduler für Fälligkeiten | U | ✔ | `manage.py wartung_faellige_ausloesen`, `db_core/tests/test_wartung_scheduler.py` | **Muss täglich laufen** — ohne Cron erscheint nie eine Fälligkeit, und niemand versteht warum |
| Frontend | **T** | ✔ (ohne Anlagenbezug) | `features/wartung*`, `features/pruefungen`, `features/gewaehrleistung`, `features/faelligkeiten` | |

**Bewertung:** Wiederkehrendes Geschäft ist der wirtschaftlich attraktivste Teil
eines SHK-Betriebs, und MCN behandelt ihn als eigene Domäne mit
Datenbank-garantierter Idempotenz. Ein Fälligkeitsdatum wird **nie verschoben**
(eine Frist ist eine Frist), nur der abgeleitete Wunschtermin. Das ist ein
starkes Vertriebsargument — und die Anlagenzuordnung (0135/0136) schließt genau
den Fehlschluss, den der Praxistest am 28.07.2026 aufgedeckt hat: „irgendein
Vertrag gilt fürs Haus, also ist meine Therme versorgt".

---

## E3 Firma und Organisation (`company`, 18 Operationen)

| Funktion | Reife | Live | Evidenz | Anmerkung |
|---|:--:|:--:|---|---|
| Firmenprofil (Anschrift, Steuer, Bank, Register) | U | ✔ | `/company/profile` (2 Op.), `services/firma.py` | Tabelle trägt `is_singleton = true` — **eine Firma je Instanz** |
| Firmenlogo hochladen/löschen | U | ✔ | `/company/profile/logo` (3 Op.) | Wird ins Beleg-PDF eingebettet (PDF/A-tauglich) |
| Niederlassungen | U | ✔ | `/company/branches` (3 Op.) | |
| Gewerke | U | ✔ | `/company/trades` (3 Op.), Migration 0120 | |
| Akquisekanäle | U | ✔ | `/company/acquisition-sources` (3 Op.) | |
| Onboarding-Status | **T** | ✔ | `GET /company/onboarding` | Nur Statusabfrage, **kein geführter Einrichtungsassistent** |
| SMTP-Konto hinterlegen und testen | **T** | ✔ | `/company/mail-account` (3 Op.), `db_core/mail_crypto.py` | Zugangsdaten Fernet-verschlüsselt. **In der Live-Instanz durch `MCN_EMAIL_BACKEND=console` wirkungslos** |
| Frontend | U | ✔ | `features/firmenprofil`, `features/niederlassungen`, `features/gewerke`, `features/mail-einstellungen` | |

---

## E4 Rechte, Freigaben, Anmeldung (`security` + `auth`, 20 Operationen)

| Funktion | Reife | Live | Evidenz | Anmerkung |
|---|:--:|:--:|---|---|
| Rechtematrix: 15 Module × 8 Aktionen × 7 Rollen | U | ✔ | `/security/permissions` (2 Op.), `services/rechte.py`, `db_core/tests/test_rechte_pflege.py` | Rollen **addieren** Rechte; beim Zeilen-Scope gilt die **weiteste** Sicht |
| Rollenzuweisung, zeitabhängig | U | ✔ | `/security/user-roles` (3 Op.) | `valid_until` exklusiv, NULL = unbefristet |
| Zeilen-Scope `EIGENE` | **T** | ✔ | `api/tests/test_row_scope.py` | **Nur für Aufgaben und Einsätze umgesetzt — überall sonst fail-closed (403)**. Bewusst so; drei Reviews fanden hier je ein Loch |
| Vier-Augen-Freigaben | U | ✔ | `/security/approvals` (4 Op.), `services/vier_augen.py` | Genehmigung ist an den **`payload`** gebunden, nicht nur an Aktion + Ziel; `claim()` verbraucht sie in derselben Transaktion |
| Login (E-Mail + Passwort), Logout, Passwortwechsel | U | ✔ | `/auth/*` (9 Op.) | Ausdrücklich **kein SSO/Microsoft** (User-Entscheidung) |
| Passwort-Reset per Mail | **T** | ✔ | `/auth/password-reset/*` (2 Op.), `api/tests/test_password_reset_api.py` | Funktioniert nur mit scharfem Mailversand |
| **Brute-Force-Schutz** | U | ✔ | `services/login_schutz.py`, Migration 0116, `api/tests/test_login_throttle.py` | DB-gestützt, Konto **und** IP; vertraut bewusst **nicht** X-Forwarded-For |
| Geräte-Token für native Clients | U | ✔ | `/auth/device/*` (2 Op.), `api/device_auth.py`, `services/geraetetoken.py` | Vorbereitung für die Android-App |
| Endpunktschutz flächendeckend | U | ✔ | `api/tests/test_endpoint_schutz.py` | Gesamte API anmeldepflichtig; Ausnahmen nur `/health` und vier `/auth`-Endpunkte |
| Frontend | U | ✔ | `features/rechtematrix`, `features/freigaben`, `features/login` | |

### Die schärfste organisatorische Lücke des Systems

**Es gibt keinen Endpunkt und keine Oberfläche, um einen Benutzer anzulegen.**
Die Rechtematrix kann Rollen nur an **bestehende** Benutzer vergeben; neue
Benutzer entstehen ausschließlich über `/admin/` (per nginx gesperrt, Zugriff
über SSH-Tunnel) oder `createsuperuser`. Für ein Produkt, das an fremde Betriebe
verkauft werden soll, ist „Benutzer einladen" kein Komfort-Feature, sondern
Voraussetzung. Bewertung und Aufwand: `05h`.

---

## E5 Querschnittsfunktionen

| Funktion | Reife | Live | Evidenz | Anmerkung |
|---|:--:|:--:|---|---|
| Globale Suche / Kommandopalette | U | ✔ | `GET /suche`, `services/suche.py`, `frontend/…/kommandopalette` | Ein Endpunkt über alle Entitäten, **jede Kategorie an ihrem eigenen Modul getort** |
| Entitäts-Dossiers (Kontakt, Liegenschaft, Projekt, Auftrag) | U | ✔ | `/dossier/*` (4 Op.), `services/dossier.py` | Ein Aufruf je Entität, deterministisch; Kern hart getort, jeder weitere Baustein weich (`_sichtbar=false` statt fehlender Antwort) |
| Werkzeuge (Fachrechner) | U | ✔ | `features/werkzeuge`, `frontend/…/rechner.spec.ts` | Ergebnis geht **nur als Textposition** in einen Beleg, nie als Menge oder Preis |
| Dateikategorien, Betriebszeit, Feiertage | U | ✔ | `db_core/betriebszeit.py` | Fachdaten rechnen in `BETRIEBS_TZ`, nicht UTC |

---

## Zusammenfassung Block E

| Bereich | Operationen | Reife | Wesentliche Lücke |
|---|---:|---|---|
| Personal | 24 | hoch | kein Lohnexport; Steuer/Bank bewusst offen |
| Wartung/Fristen | 20 | hoch | Anlagenzuordnung noch nicht ausgerollt |
| Firma/Organisation | 18 | mittel-hoch | kein Einrichtungsassistent; Mailversand stillgelegt |
| Rechte/Freigaben/Login | 20 | hoch | **keine Benutzeranlage**; Zeilen-Scope nur an zwei Stellen |
| Querschnitt (Suche, Dossier) | 5 | hoch | — |

**Belastbare externe Formulierung:** „Rechte werden über eine Matrix aus 15
Modulen und 8 Aktionen je Rolle vergeben und serverseitig durchgesetzt; sensible
Vorgänge durchlaufen ein an den Antragsinhalt gebundenes Vier-Augen-Verfahren."
**Nicht behaupten:** mandantenfähig, benutzerverwaltend, DSGVO-vollständig
(Lösch- und Auskunftsprozesse sind organisatorisch noch nicht ausformuliert).
