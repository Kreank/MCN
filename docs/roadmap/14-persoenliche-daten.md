# 14 — Mein Profil (Hero: Persönliche Daten)

## Zweck & Hero-Entsprechung

„Mein Profil" ist der persönliche Bereich, in dem der eingeloggte Nutzer die
**eigenen** Benutzerdaten pflegt: Stammdaten (Anrede/Name/Kontakt), Anzeige-
sprache, E-Mail-Signatur, Passwort ändern sowie die persönliche
**Mailserver-/Absender-Konfiguration** inkl. OAuth-Anbindung an Microsoft/Google.
Er spiegelt Hero's Bereich **[Persönliche Daten]** — allerdings **bewusst
reduziert**: Hero hängt in denselben Bereich auch die **HR-Personalstammdaten**
(Vertrag, Steuerdaten, Bankdaten, Urlaub/Abwesenheiten, Zeiterfassung,
Stundenausgleich, Personaldokumente). Diese gehören fachlich zu **Mitarbeiter
(`12-mitarbeiter.md`)** und werden dort behandelt, nicht hier gedoppelt (siehe
Abgrenzung unten). „Passwort vergessen" ist ein **ausgeloggter** Login-Flow und
liegt außerhalb dieser Sektion (Auth/Login-Fundament, Phase 0).

- **Abgedeckte Hero-Quelldateien:**
  - `Persönliche Daten\Wo kann ich meine persönlichen Daten ändern\Wo kann ich meine persönlichen Daten ändern.txt` (Übersicht/Stammdaten/Navigation)
  - `Persönliche Daten\Das Passwort ändern\Das Passwort ändern.txt` (nur der **eingeloggte** „Passwort ändern"-Teil; „Passwort vergessen" → Login-Flow, siehe Abgrenzung)
  - `Persönliche Daten\Wie kann ich meine Signatur bearbeiten\Wie kann ich meine Signatur bearbeiten.txt`
  - `Persönliche Daten\Wie kann ich Links in meine E-Mail Signatur einfügen\Wie kann ich Links in meine E-Mail Signatur einfügen.txt`
  - `Persönliche Daten\Kann ich eine eigene E-Mail Adresse als Absender einrichten\Kann ich eine eigene E-Mail Adresse als Absender einrichten.txt`
  - `Persönliche Daten\Microsoft Email-Konto mit HERO verbinden (OAuth)\Microsoft Email-Konto mit HERO verbinden (OAuth).txt`
  - `Persönliche Daten\Microsoft Google Email-Konto mit HERO verbinden (OAuth)\Microsoft Google Email-Konto mit HERO verbinden (OAuth).txt`
- **An `12-mitarbeiter.md` abgegeben (hier nur verlinkt, nicht dupliziert):**
  Hero-Reiter [Vertrag], [Steuerdaten], [Bankdaten], [Urlaub und Abwesenheiten],
  [Zeiterfassung], [Stundenausgleich], [Dokumente] sowie die HR-Sicht des
  Reiters [Berechtigungen] (Lohngruppe/Niederlassung). Zur Rolle
  „Persönliche-Daten"-Reiter siehe Offene Punkte.

## Ziel-Navigation & Routen

- Angular-Route: `/mein-profil` (unterster Nav-Bereich; im Leitstand als
  Nutzermenü oben rechts / Avatar erreichbar statt in der fachlichen Sidebar —
  spiegelt Hero's Trennung „persönlicher Bereich" vom Arbeitsbereich, siehe
  `00`, Abschnitte „Hero's Informationsarchitektur" und „Ziel-Navigation").
- **Tab-Struktur der Profilseite** (Reihenfolge spiegelt Hero, reduziert auf den
  hier verantworteten Umfang):
  1. `/mein-profil/persoenliches` — Stammdaten, Anzeigesprache, Signatur-Editor,
     Auslöser für Passwort-Dialog. (Hero: [Persönliches])
  2. `/mein-profil/berechtigungen` — **nur Anzeige** der eigenen Rollen/
     Niederlassung/Account-Typ. (Hero: [Berechtigungen])
  3. `/mein-profil/mailserver` — Absender-Einstellung + OAuth. (Hero: [Mailserver])
- **Nicht in dieser Sektion** (in Hero benachbarte Tabs, hier bewusst ausgelagert
  nach `12`): Vertrag, Steuerdaten, Bankdaten, Urlaub, Zeiterfassung,
  Stundenausgleich, Dokumente. Falls Produkt entscheidet, dem Nutzer eine
  **Selbst-Sicht** dieser HR-Daten unter „Mein Profil" zu geben, werden sie als
  read-only-Absprünge auf die `12`-Komponenten eingebunden (kein eigener Code
  hier) — siehe Offene Punkte.
- Globale Nav-/Sidebar-/Seiten-Konventionen: siehe `00-informationsarchitektur.md`.

## Screens & Komponenten

### Persönliches (Stammdaten + Sprache + Signatur)

- **UI-Typ & Aufbau:** Einstellungsseite mit Formular. Felder (Hero): [Anrede],
  [Vorname], [Nachname], [Geburtsdatum], [Emailadresse], [Telefonnummern],
  [Adresse], Auswahl **Anzeigesprache**, **Signatur-Editor-Bereich**, Button
  `[Passwort ändern]` (öffnet Dialog, unten), Button `[Speichern]`.
- **Signatur-Editor:** Rich-Text mit umschaltbarem **HTML-Quellcode-Modus**
  (Hero-Feature „Links in Signatur einfügen": Nutzer schaltet in HTML-Modus und
  fügt `<a href>`-Links ein). Umschalt-Symbol in Hero unklar dokumentiert (siehe
  Offene Punkte) — MCN nutzt einen expliziten `[</> HTML]`-Umschalter. Signatur
  wird beim späteren Mailversand mitgeschickt (Bezug Mailserver-Tab, `05`/`23`).
- **Zustände:** Laden (Skeleton-Formular); Fehler (Feld-/Speicherfehler inline);
  Erfolg (Toast). **Rollen-Sichtbarkeit:** immer nur eigenes Profil; Bearbeitung
  fremder Profile findet in `12` statt (Admin-Sicht), nicht hier.
- **Wiederverwendete shared components** (`00`): Anlege-/Bearbeiten-Formular-
  Muster. *Neu:* der Rich-Text-/HTML-Signatur-Editor (wiederverwendbar für
  firmenweite Signatur/Mailvorlagen in `13`).

### Passwort ändern (Dialog)

- **UI-Typ & Aufbau:** Modal, aus Tab „Persönliches" ausgelöst. Felder: „Altes
  Passwort", „Neues Passwort", „Neues Passwort wiederholen", Button `[Speichern]`.
- **Zustände:** Validierung (Übereinstimmung, Passwort-Policy — OFFEN, Policy noch
  festzulegen); Fehler (altes Passwort falsch); Erfolg (Toast, Dialog schließt).
- **Abgrenzung:** „Passwort **vergessen**" (Login-Seite → `[Passwort vergessen?]`
  → E-Mail mit befristetem Passwort) gehört **nicht** hierher, sondern in den
  Login-/Auth-Flow (Phase 0). Hero-Fakt zur Übernahme dort: **Einmal-Passwort
  gültig 12 Stunden**.

### Mailserver (Absender + OAuth)

- **UI-Typ & Aufbau:** Einstellungsseite/Formular mit Umschalter
  **„Standard-Absender"** vs. **„Eigene E-Mail-Adresse"**.
  - Bei „Eigene E-Mail-Adresse" (klassisch/SMTP): Auswahl „Ihr Anbieter", Felder
    „Ihre E-Mail-Adresse", „SMTP-Benutzername", „SMTP-Passwort", optional
    „BCC-Blindkopie". Bei Anbieter = „Anderer Anbieter" zusätzlich
    „SMTP-Ausgangsserver", „Verschlüsselung", „Port". Button `[Speichern]`.
  - Bei OAuth-Anbieter „Outlook / Hotmail oder Google (via OAuth2)": Felder
    E-Mail-Adresse + optional BCC, Button **`[Autorisieren]`** → externe
    Weiterleitung (Microsoft/Google-Login + Zustimmung `[Annehmen]`) → Rücksprung.
    Zustand „abgelaufen": Button **`[Erneut autorisieren]`** + Option
    **„Konfiguration zurücksetzen"** (Zugriff entziehen).
- **Zustände:** Verbunden / nicht verbunden / **abgelaufen**; Laden während
  Redirect-Rücksprung; Fehler (OAuth abgebrochen/abgelehnt). Status **nie nur über
  Farbe** (Text + Icon, `00`). **Wichtig (Hero-Fakt):** OAuth kann **nur der
  Nutzer selbst** autorisieren — ein Admin kann in `12` fremde SMTP-Daten
  hinterlegen, aber **keine** OAuth-Verbindung für andere herstellen.
- **Hero-Fakten zur Übernahme:** OAuth-Verbindung gültig **180 Tage**, danach
  Re-Autorisierung nötig.
- **Wiederverwendung:** Diese Komponente/Service soll **identisch** für die
  firmenweite Variante `[Firmeneinstellungen] → [Mailserver]` (`13`) und für die
  Admin-Einrichtung fremder Nutzer (`12`) nutzbar sein — persönliche und
  firmenweite Absenderkonfiguration teilen sich Modell und Service (Hero führt
  dieselbe Maske an drei Stellen).

### Berechtigungen (nur Anzeige)

- **UI-Typ & Aufbau:** Read-only-Anzeige der eigenen [Niederlassung],
  [Benutzerrechte/Rollen], [Account-Typ]. Quelle: `security.user_role` /
  `security.role`. Bearbeitung ausschließlich durch Admin in `12`.
- **Zustände:** Laden; Anzeige. Keine Schreibaktion in dieser Sektion.

## API-Endpunkte (django-ninja)

Alle Writes ausschließlich über `business_transaction` (`00`). Schreibende
Endpunkte erfordern Session + `app_user`; sie wirken **immer nur auf das eigene
Konto** (Ownership-Check, kein Fremdzugriff — der liegt in `12`).

| Methode | Pfad (`/api/…`) | Zweck | Auth | Service-Funktion |
|---|---|---|---|---|
| GET | `/api/mein-profil` | Eigene Stammdaten, Sprache, Signatur | Session | `security.get_own_profile` |
| PATCH | `/api/mein-profil` | Stammdaten/Sprache/Signatur speichern (eigenes Konto) | Session | `security.update_own_profile` |
| POST | `/api/mein-profil/passwort` | Passwort ändern (altes + neues) | Session | `security.change_own_password` |
| GET | `/api/mein-profil/berechtigungen` | Eigene Rollen/Niederlassung/Account-Typ (read-only) | Session | `security.get_own_permissions` |
| GET | `/api/mein-profil/mailserver` | Aktuelle Absender-/OAuth-Konfiguration | Session | `mailserver.get_own_config` |
| PUT | `/api/mein-profil/mailserver` | SMTP-Absender speichern / auf Standard zurücksetzen | Session | `mailserver.set_own_config` |
| POST | `/api/mein-profil/mailserver/oauth/start` | OAuth-Autorisierung starten (liefert Redirect-URL) | Session | `mailserver.start_oauth` |
| GET | `/api/mein-profil/mailserver/oauth/callback` | OAuth-Rücksprung (Code→Token, Bindung an Nutzer) | Session | `mailserver.finish_oauth` |
| POST | `/api/mein-profil/mailserver/oauth/reset` | „Konfiguration zurücksetzen" (Token entziehen) | Session | `mailserver.reset_oauth` |

- **Lesend:** `GET mein-profil`, `…/berechtigungen`, `…/mailserver`.
- **Schreibend:** die übrigen — alle über `business_transaction`.
- Endpunkt-Namensraum `mailserver.*` ist provisorisch (Zielschema OFFEN, s. u.).

## DB-Bezug

- **`security.app_user`** (Migration `0002`) ist heute **minimal**: nur
  `display_name`, `principal_party_id`, `status`, `version`, Timestamps —
  **kein** Passwort-/Credential-Feld, **keine** Sprache, **keine** Signatur,
  **keine** Mailserver-/OAuth-Spalten. Für diese Sektion fehlen die Zieltabellen
  daher noch weitgehend → sie ist überwiegend **Greenfield** (neue Hand-SQL-
  Migration nötig, `managed=False`, Schutzstandard, siehe `00`/`backend/README`).
- **Stammdaten (Anrede/Name/Kontakt/Adresse):** hängen an der Person hinter dem
  Nutzer — `identity.party` / `identity.person` (via `app_user.principal_party_id`).
  Profil-Stammdaten also gegen `identity` schreiben, nicht neu in `security`.
- **Sprache & Signatur:** neue Nutzerprofil-Attribute — OFFEN, ob als Spalten auf
  `security.app_user` oder als separate `security.user_profile`-Tabelle
  (Empfehlung: eigene Tabelle, hält `app_user` schlank).
- **Passwort/Credentials:** gehört zum noch nicht existierenden **Auth-Fundament**
  (`security`); Login/Session ist Phase-0-Voraussetzung. Diese Sektion setzt
  darauf auf.
- **Berechtigungen (Anzeige):** `security.role`, `security.user_role`,
  `security.role_permission` (Migration `0026`; Rollen u. a. ADMINISTRATION,
  GESCHAEFTSFUEHRUNG, DISPOSITION, BUCHHALTUNG, MONTEUR, NUR_LESEN). Niederlassung
  ist in `security`/Stammdaten zu verorten (mit `12`/`13` abzustimmen).
- **Mailserver/OAuth-Tokens:** **keine passende Tabelle vorhanden** — neu
  anzulegen (Integrations-/Credential-Tabelle, verschlüsselte SMTP-Passwörter &
  OAuth-Refresh-Tokens, gebunden an `app_user`; firmenweite Variante an Firma).
  Zielschema OFFEN (Vorschlag: eigenes `mailserver`/`integration`-Schema oder
  unter `security`). Secrets nie im Klartext; Ablauf 180 Tage abbilden.
- **Statusautomaten/Constraints:** neue Tabellen erben den Schutzstandard
  (No-Delete/Audit/No-Truncate, `00`). Kein Statusautomat-Kern hier; relevanter
  „Zustand" ist der OAuth-Verbindungsstatus (aktiv/abgelaufen/entzogen).

## KI-Andockpunkte (`ai.ai_proposal`)

- Bewusst **eng** gehalten: „Mein Profil" ist ein persönlicher, sicherheits-
  naher Bereich — die KI verwaltet keine fremden Konten und autorisiert **kein**
  OAuth (Hero-Regel: nur der Nutzer selbst).
- Sinnvolle, unkritische Andockpunkte über `ai.ai_proposal` (gehen durch dieselben
  Service-Tore wie ein Mensch, `00`):
  - **Signatur-Entwurf vorschlagen** (KI generiert/aktualisiert Signaturtext/-HTML;
    Nutzer nimmt an → `update_own_profile`).
  - **Sprachumstellung/Profilvervollständigung vorschlagen** (z. B. fehlende
    Telefonnummer), rein als Vorschlag.
- **Nicht** KI-fähig: Passwort ändern, OAuth autorisieren, Konfiguration
  zurücksetzen (immer nutzergebundene, explizite Handlung).

## No-Delete/Audit/GoBD-Übersetzung

- **„Speichern" von Stammdaten/Sprache/Signatur:** Update mit Versionierung +
  Audit (Append-only-Trail), kein Hard-Delete.
- **„Konfiguration zurücksetzen" (Mailserver/OAuth):** **kein Löschen** des
  Datensatzes, sondern Statuswechsel auf „entzogen"/„inaktiv" + Token-Invalidierung
  (die Konfigurationshistorie bleibt für Audit erhalten). Token-Secret darf beim
  Entziehen unbrauchbar gemacht/rotiert werden.
- **Passwortänderung:** kein „Löschen"; Credential wird ersetzt, Ereignis wird
  auditiert (nicht das Klartext-Passwort).
- Kein GoBD-Beleg-Bezug in dieser Sektion.

## Offene Punkte / Entscheidungen

- **HR-Abgrenzung (Grundsatz):** Hero packt Vertrag/Steuer/Bank/Urlaub/Zeit in
  „Persönliche Daten". MCN verortet diese in **Mitarbeiter (`12`)**. Zu
  entscheiden: bekommt der Nutzer unter „Mein Profil" eine **read-only
  Selbst-Sicht** dieser HR-Daten (dann als Absprung auf `12`-Komponenten), oder
  sind sie ausschließlich über `12` (Admin/HR) erreichbar? (Produktentscheidung.)
- **HR-/Personalmodul überhaupt im Scope?** (aus Spec übernommen): Kein
  Domänen-Schema deckt Personalstammdaten/Lohn ab. Grundsatzfrage: eigenes
  `hr`/`personnel`-Schema in `12`, oder bewusst außerhalb des MCN-Scopes (Fokus
  Liegenschaften/Mandate, keine Lohnbuchhaltung)? → in `12` zu klären, wirkt auf
  diese Sektion nur über die Selbst-Sicht-Frage.
- **Sprache & Signatur — Ablage:** Spalten auf `app_user` vs. eigene
  `user_profile`-Tabelle (Empfehlung: eigene Tabelle).
- **Mailserver-Zielschema & Secret-Handling:** eigenes Schema für SMTP-/OAuth-
  Credentials, Verschlüsselung/Vault-Anbindung, gemeinsames Modell für persönlich
  (14) / firmenweit (`13`) / fremdverwaltet (`12`). Entscheidbar mit Security.
- **Passwort-Policy** (Mindestlänge/Komplexität) für „Passwort ändern" festlegen.
- **HTML-Signatur-Umschalter:** Hero-Optik unklar (Docx→Text-Artefakt, Spec) —
  MCN legt expliziten `[</> HTML]`-Umschalter fest; Screenshot image1 zur
  Verifikation heranziehen.
- **OAuth-Provider-Freischaltung:** In Hero muss die OAuth-Option ggf. per Support
  freigeschaltet werden — für MCN entscheiden, ob standardmäßig aktiv.
- **Fakten zur bewussten Übernahme/Neufestlegung:** Einmal-Passwort 12 h (Login-
  Flow), OAuth-Verbindung 180 Tage.

## Abhängigkeiten

- **Auth-/Login-Fundament (Phase 0):** Session + `app_user`, Passwort-Credentials,
  „Passwort vergessen"-Flow — Voraussetzung für Passwort ändern und alle Writes.
- **`identity`** (party/person) für Stammdaten; **`security.role`/`user_role`**
  (`0026`) für die Berechtigungs-Anzeige.
- **Neue DB-Migration** (Hand-SQL) für Sprache/Signatur + Mailserver/OAuth-
  Tabellen (existieren noch nicht).
- **`12-mitarbeiter.md`:** definiert das HR-Modell und die Admin-Sicht; klärt die
  Selbst-Sicht-Absprünge und teilt die Mailserver-Komponente.
- **`13-einstellungen.md`:** firmenweite Mailserver-Variante (gemeinsame
  Komponente/Service).
- **Mail-Versand-Infrastruktur** (`content`/`23-kommunikation`) als Konsument der
  Absender-/Signatur-Konfiguration.
- **Shared components** (`00`): Formular-/Dialog-Muster.

## Aufwand & Priorität

- **Empfohlene Phase:** **Phase 4 — Administration** (`00`), zusammen mit `12`/`13`.
  Setzt das Auth-Fundament (Phase 0) voraus.
- **Aufwand je Screen:**
  - Persönliches (Stammdaten + Sprache): **S** (Formular auf `identity`/Profil).
  - Signatur-Editor (Rich-Text + HTML-Modus): **M** (wiederverwendbare Komponente).
  - Passwort-Dialog: **S** (setzt Auth voraus).
  - Berechtigungen (read-only): **S**.
  - Mailserver SMTP + OAuth-Flow: **L** (externer Redirect, Secret-Handling,
    Ablauf/Re-Autorisierung, gemeinsames Modell mit `12`/`13`).
- **Reihenfolge:** zuerst Persönliches + Berechtigungen (leicht, nach Auth), dann
  Signatur, zuletzt Mailserver/OAuth (schwer, koordiniert mit `12`/`13`).

## Screenshots zur Vorlage (Wiedererkennung)

- `Wo kann ich meine persönlichen Daten ändern` **image1** — Gesamtübersicht mit
  Sidebar-Einstieg und Tab-/Menü-Struktur; prägt das mentale Modell (HOCH).
- `Das Passwort ändern` — Eingabemaske Altes/Neues Passwort (Button-Ort im Tab
  „Persönliches") als Vorlage für den Dialog (HOCH).
- `Kann ich eine eigene E-Mail Adresse als Absender einrichten` **image1–image4**
  — Aufbau der Mailserver-Maske (Umschalter Standard/Eigene, Anbieter, SMTP-Felder)
  (HOCH für den Mailserver-Screen).
- `Microsoft Google Email-Konto mit HERO verbinden (OAuth)` **image1–image4** —
  OAuth-Flow (Anbieter-Auswahl, `[Autorisieren]`, externer Consent, Zustände
  verbunden/abgelaufen) (HOCH für den OAuth-Teil).
- Signatur-Screenshots (je image1) nur MITTEL/NIEDRIG — als Detailreferenz für
  den Editor, nicht layoutprägend.
