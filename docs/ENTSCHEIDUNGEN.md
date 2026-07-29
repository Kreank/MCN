# Fixierte Entscheidungen — nicht erneut aufmachen

> Bewusst getroffene Festlegungen samt Begründung. Wer eine davon ändern will,
> braucht ein neues Argument — nicht bloß eine andere Vorliebe.

---

## 7. Fixierte Entscheidungen (nicht erneut aufmachen)

- **Auth ist gebaut** (siehe Abschnitt 2b) — die frühere Notiz „Auth ganz zuletzt"
  ist erledigt und gilt nicht mehr. Die gesamte API ist anmeldepflichtig; die
  Dev-Phasen-Konvention „Lesen ohne Auth" ist damit **abgeschafft**.
- **Eigenes Login, kein SSO/Microsoft** (ausdrückliche User-Entscheidung). Die
  Microsoft/Google-OAuth-Anbindung in `docs/roadmap/14` betrifft nur den
  **Mailversand**, nicht die Anmeldung.
- **`row_scope='EIGENE'` ist fail-closed**: wo eine Ansicht die Zeilenbegrenzung
  nicht umsetzt, gibt es 403 statt aller Zeilen. Nicht „vorübergehend" auf
  `require_scoped` ohne Filter umstellen — das wäre ein stiller Datenleak.
- **Nav-Begriffe Hero-nah:** „Projekte"/„Dokumente" (nicht Vorgänge/Belege).
- **Liegenschaften** eigener Nav-Punkt (nicht Reiter in Kontakten).
- **Kein Löschen** (GoBD/Audit): Rechnungen nur Storno; Projekte nur verschieben/
  archivieren; überall „Löschen"→Archivieren/Storno/Status.
- **Lagerverwaltung vorerst weggelassen** (DB-Beschluss B-26 verbietet Bestände).
- **RAG/Firmenwissen: pgvector im BESTEHENDEN Postgres, eigenes Schema `knowledge`,
  KEINE zweite Datenbank und kein separater Vektor-Dienst** (User-Entscheidung
  2026-07-14). Zeitpunkt: **ganz zum Schluss** — nach dem KI-Ausbau, erst recht nach
  der Demo. Details siehe Abschnitt 11.


## 10. Deployment & Backup

> ### ⚠️ Stand 2026-07-22 — was von diesem Abschnitt noch gilt
>
> Der frühere **„SPERRBLOCK vor dem Echtbetrieb" ist abgeräumt**: Die Instanz läuft
> seit 2026-07-17 im **Echtbetrieb** (~2 Mio Artikel, echte Kundendaten), und das
> **Backup ist gebaut** und läuft nächtlich (Compose-Dienst `backup`, Runbook in
> `docs/deployment.md` 8a). Wo unten „noch nicht gebaut", „vor dem Echtbetrieb" oder
> „Demo-Instanz" steht, ist der Text **historisch**.
>
> **Zwei Korrekturen, die betrieblich zählen:**
> - **Der Mailversand ist NICHT mehr doppelt gesichert.** Unten steht „kein
>   `MCN_MAIL_KEY`" — der Schlüssel **ist seit dem Fresh-Reset gesetzt**. Es schützt
>   nur noch `MCN_EMAIL_BACKEND=…console.EmailBackend`. Wer den umstellt, macht
>   Rechnungs- und Mahnungsversand an echte Kundenadressen sofort scharf.
> - **`MCN_SEED_COMMAND=seed_demo` steht weiterhin in der `.env`**, entschärft
>   allein durch `MCN_SEED=0`. Diesen Schalter vor jedem Deploy prüfen.
>
> **Die INVARIANTEN unten gelten unverändert** — besonders die zwei Datentöpfe,
> die Reihenfolge DB→MinIO und der Schlüssel-Vorbehalt.

> **Der Demo-Datenbestand steht in `docs/demo-szenario.md`** (vom User
> freigegeben: WEG Badensche Straße 53 mit Verwaltung Stegos, EFH Peter Borm,
> sechs SHK-Szenarien). **`seed_demo` ist NICHT die Demo** — es ist
> Entwicklerfutter und wirkt vor dem Chef wie fremder Beispielkram. Dort steht
> auch die **Mieter-Lücke** (`tenure.occupancy` trägt keinen Beteiligten) und die
> Entscheidung **ein Artikelstamm, mehrere Anbindungen — kein „Gerätewissen"-Silo**.

**ERLEDIGT (2026-07-14): Der Demo-Stack ist gebaut und lokal end-to-end
verifiziert** — `deploy/` (compose, zwei Dockerfiles, Entrypoints,
`.env.example`) und **`docs/deployment.md`** (Schritt-für-Schritt für den Server).
**Backup weiterhin bewusst NICHT gebaut** (siehe unten). Was unten beschrieben
steht, ist damit umgesetzt; die Absätze bleiben als Begründung stehen.

Nachgewiesen (frische Volumes, Stack von null): migrate + Seed laufen, Frontend
über nginx, `/api/openapi.json`, **Login mit Demo-Passwort**, Beleg-PDF **und**
ZUGFeRD-PDF/A werden gerendert und in MinIO archiviert (DejaVu eingebettet),
`/admin/` gesperrt (403 ohne IP, 401 ohne Basic-Auth), Postgres/MinIO ohne
Host-Port, Mailversand tot. TLS wurde mit einem **selbstsignierten** Zertifikat
geprüft (Let's Encrypt braucht eine öffentliche Domain) — der HTTPS-Pfad,
Secure-Cookies und die Umschaltung HTTP→HTTPS sind damit belegt, die
ACME-Ausstellung selbst nicht.

**Drei Dinge, die man beim Anfassen wissen muss:**
- **Der Seed-Aufruf im Entrypoint bekommt `MCN_DEBUG=1` mit — nur er.**
  `seed_demo` **bricht ohne DEBUG ab** und vergibt Passwörter nur bei DEBUG.
  gunicorn startet unverändert mit `MCN_DEBUG=0`. Die Passwortvergabe hängt
  aber **nicht** an diesem Kniff, sondern am eigenen Command
  **`demo_passwoerter_setzen`** (verlangt `MCN_DEMO_INSTANZ=1`) — damit sie auch
  trägt, wenn `MCN_SEED_COMMAND` auf `seed_szenario` umgestellt wird. **Genau das
  ist der Tausch, der ansteht: nur der Name in der `.env`.**
- **Der Mailversand ist doppelt totgelegt:** `MCN_EMAIL_BACKEND=console` (der
  gesamte Versand läuft über `django.core.mail.get_connection()` — es gibt keinen
  zweiten Weg nach außen) **und** kein `MCN_MAIL_KEY` (ohne ihn lässt sich nicht
  einmal ein SMTP-Konto speichern). Nicht „reparieren".
- **`/admin/` ist auf der Demo der einzige Weg, Benutzer anzulegen** — der offene
  Slice „Benutzer einladen" im Leitstand bleibt offen.

### Was für die Demo-Instanz gilt (bewusst schlank)

Vier Container: **nginx** (liefert das gebaute Angular aus, reicht `/api` ans
Backend weiter, terminiert TLS), **backend** (Django + gunicorn), **postgres 16**,
**minio**. Das Frontend bekommt **keinen** Laufzeit-Container — der Angular-Build
ist statisch und wird per Multi-Stage-Build ins nginx-Image kopiert. Postgres und
MinIO bekommen **keine Ports nach außen** (im Dev sind 55432/9100 offen, weil man
von Windows draufkommen muss; auf einem Server wäre das eine offene Datenbank im
Internet).

- **`/admin/` darf NICHT öffentlich erreichbar sein.** Das Django-Admin kennt die
  Rechtematrix nicht — wer dort drin ist, legt Superuser an. nginx sperrt
  `location /admin/` (IP-Allowlist oder Basic-Auth); Zugriff per SSH-Tunnel.
- **Mailversand auf der Demo-Instanz TOTLEGEN** (kein `MCN_MAIL_KEY`,
  `EMAIL_BACKEND` auf console; Seed-Adressen auf `@example.com`). Der Versandpfad
  ist scharf: Ein neugieriger Klick auf „Mahnung versenden" schickt eine **echte
  Mahnung** an eine echte Adresse. Es ist die einzige Aktion im System, die nach
  außen wirkt.
- **Volumes für Postgres und MinIO** — sonst sind nach jedem Neustart alle Klicks
  der Demo-Nutzer weg.
- **Benutzeranlage fehlt im Leitstand.** Es gibt **keinen** Endpunkt und keine UI,
  um einen Benutzer zu erzeugen — die Rechtematrix kann Rollen nur an **bestehende**
  Benutzer vergeben. Neue Benutzer entstehen heute nur über `/admin/` oder
  `createsuperuser`. Für die Demo: Demo-Benutzer samt Rollen per
  Management-Command im Entrypoint anlegen, nicht von Hand im Admin klicken.
  **Offener Slice:** „Benutzer einladen" im Leitstand (spätestens wenn echte
  Mitarbeitende draufkommen).
- Settings sind bereits deployfähig: `MCN_DEBUG` ist **fail-safe aus** (Dev muss es
  bewusst einschalten), Session-/CSRF-Cookies laufen in Produktion automatisch nur
  über HTTPS, `MCN_ALLOWED_HOSTS`/`MCN_CSRF_TRUSTED_ORIGINS` kommen aus Env-Vars.
  Das Frontend ruft die API über **relative Pfade** (`/api/...`) — same-origin
  hinter nginx, **kein CORS nötig**.
- **Secrets gehören nicht ins Image** (`MCN_SECRET_KEY` hat noch einen
  „django-insecure"-Default). Auf dem Server erzeugen, per Env einspeisen.
- **Scheduler nicht vergessen:** `wartung_faellige_ausloesen` muss täglich laufen
  (Wartungen, Prüffristen, Gewährleistung). Ohne Cron tauchen nie Fälligkeiten auf,
  und niemand versteht warum.

### Vor dem Echtbetrieb: Backup ist Pflicht (User-Entscheidung 2026-07-14)

Bewusst **auf später verschoben** — für Seed-Daten wäre eine Backup-Strategie
Overengineering. **Aber: Bevor die erste echte Rechnung im System steht, muss das
hier stehen.** Das System ist GoBD-relevant (Rechnungen unveränderlich, kein
Löschen, zehn Jahre Aufbewahrung).

**INVARIANTE: Es gibt ZWEI Datentöpfe, und sie sind nicht gleich viel wert.**
- Die **Beleg-PDFs** in MinIO sind ersetzbar — sie werden aus dem eingefrorenen
  `billing_snapshot` neu gerendert. Cache, kein Original.
- **Unwiederbringlich sind: Kundenunterschriften unter Baustellenberichten,
  Baustellenfotos, Atteste.** Die existieren **nur** als Datei in MinIO. Ist MinIO
  weg, bleibt in der DB ein unterzeichneter, versiegelter Bericht — **ohne die
  Unterschrift, wegen der er überhaupt existiert.** Der Beweiswert ist dann null.

**INVARIANTE: Reihenfolge ist erst DB, dann MinIO — nie umgekehrt.** Eine Datei in
MinIO ohne DB-Eintrag ist ein harmloser Waise. Ein DB-Eintrag, der auf eine im
Backup fehlende Datei zeigt, ist ein kaputter Beleg.

**INVARIANTE: Ohne `MCN_MAIL_KEY` ist das DB-Backup teilweise wertlos.** Der
Fernet-Schlüssel entschlüsselt SMTP-Zugangsdaten und Händler-Credentials. Er
gehört in den Passwortmanager, **nicht nur** in die `.env` auf dem Server —
zusammen mit `MCN_SECRET_KEY` und den MinIO-Keys.

Bausteine, wenn es so weit ist: nächtlicher `pg_dump` **plus WAL-Archivierung**
(Point-in-Time statt „gestern Nacht"), MinIO gespiegelt (`mc mirror`) in ein
zweites, **versioniertes** Ziel, beides verschlüsselt und **offsite**, Aufbewahrung
lang genug, dass ein drei Wochen unbemerkter Fehler noch reparabel ist.

**Und das Wichtigste: ein Restore-Skript, das eine Wegwerf-Umgebung aus dem Backup
hochzieht — quartalsweise scharf gefahren.** Ein Backup, das nie zurückgespielt
wurde, ist kein Backup, sondern eine Hoffnung. Erst der gelungene Restore beweist,
dass Schlüssel, Reihenfolge und Volumes stimmen.

## 11. Firmenwissen / RAG — entschieden, aber GANZ ZUM SCHLUSS

Ziel des Users: Firmenwissen (SOPs, CRM-Anleitung, Herstellerunterlagen) als
durchsuchbares Werkzeug **im Leitstand-Frontend**. **Zeitpunkt: ganz am Ende** —
nach dem KI-Ausbau (Dossiers → Vorschläge → Chat), erst recht nach der Demo.
Nicht vorziehen. Die Entscheidung ist trotzdem hier festgehalten, damit sie beim
KI-Ausbau **nicht neu aufgemacht** wird.

**ENTSCHIEDEN: pgvector im bestehenden Postgres, eigenes Schema `knowledge`.**
Keine zweite Datenbank, kein Qdrant/Weaviate.

**Warum dieselbe Datenbank — das Argument hängt an den RECHTEN, nicht an der
Bequemlichkeit.** SOPs sind harmlos, aber die Wissensbasis bleibt nicht dabei:
Sobald Angebote, Baustellenberichte oder Kundenkorrespondenz indiziert werden,
gilt die Rechtematrix. Ein Monteur mit `row_scope='EIGENE'` darf über die Suche
**nichts finden, was er in der Oberfläche nie sähe**. Liegen die Vektoren in einer
fremden DB, müsste man die Rechte dorthin **duplizieren** (zwei Wahrheiten, die
auseinanderlaufen) oder **nachträglich filtern** — und nachträglich filtern heißt:
Der Treffer ist bereits gefunden, man hofft nur, ihn rechtzeitig wegzuwerfen.
**Genau so lecken RAG-Systeme.** Im selben Postgres ist die Rechteprüfung ein JOIN
in derselben Abfrage, mit **demselben Filter wie die Entitäts-Dossiers**.

**Warum trotzdem ein eigenes Schema — WICHTIG:**
**`knowledge.*` ist AUSDRÜCKLICH VOM SCHUTZSTANDARD AUSGENOMMEN**
(No-Delete/Audit/No-Truncate gilt dort **nicht**). Ein RAG-Index wird bei jedem
Re-Index weggeworfen und neu gebaut; bei einem Modellwechsel muss **alles** neu
eingebettet werden. Wer dort pflichtschuldig den No-Delete-Trigger anhängt, macht
Re-Indexieren **physisch unmöglich**. Denkrahmen wie beim Beleg-PDF: **der Index
ist Cache, nicht Original** — Quelle sind die Dateien in MinIO/`content.file`. Ein
verlorener Index kostet Rechenzeit, keine Daten; er gehört deshalb auch **nicht**
ins GoBD-Backup (hält die wertvolle Sicherung schlank).

**Zwei Konstruktionsregeln, wenn es so weit ist:**
- **Embedding-Modell und Dimension an JEDEN Chunk schreiben.** Sonst mischen sich
  beim Modellwechsel alte und neue Vektoren, die Ähnlichkeiten werden stillschweigend
  Unsinn — die Suche liefert ja weiterhin *irgendetwas*, nur das Falsche.
- **Herkunft von Anfang an trennen** (`source_kind` + optionale Entitäts-FKs):
  SOP-Chunks tragen keinen Entitätsbezug und sind für alle sichtbar;
  ein Chunk aus einem Baustellenbericht trägt die Referenz auf seinen Auftrag und
  wird **exakt wie der Auftrag** gefiltert. Dann ist der Rechtefilter eine
  WHERE-Bedingung und keine Architekturfrage.

Eine eigene Datenbank wäre erst bei Millionen Chunks oder eigenem Betreiberteam
richtig. Für einen Handwerksbetrieb mit ein paar tausend Dokumenten ist pgvector
im vorhandenen Postgres nicht der Kompromiss, sondern die richtige Wahl.

---

## Wartungsvertrag ↔ Anlage: n:m statt einer Spalte (2026-07-28)

**Entschieden:** Zuordnungstabelle `maintenance.contract_asset` (n:m), **nicht**
ein `asset_id` am Vertrag.

**Warum:** Ein Vertrag über *alle* Thermen eines Hauses ist der Normalfall, nicht
die Ausnahme; und dieselbe Anlage kann in mehreren Verträgen stehen (Wartung
jährlich, Abgasmessung separat). Eine Spalte hätte den ersten Vertrag mit zwei
Thermen sofort erpresst: entweder zwei Verträge anlegen (falsch — es ist einer)
oder das Feld leer lassen (dann ist es Zierrat). Form nach dem Vorbild
`hr.employee_trade` (0120): voller Schutzstandard, `active`-Flag statt DELETE.

**Nicht neu aufmachen:** Die leere Zuordnung bleibt „gilt fürs ganze Objekt".
Bestandsverträge nachträglich auf „deckt nichts ab" umzudeuten wäre eine
stillschweigende Entwertung echter Daten.

## Gebäudeansicht statt zwölftem Reiter (2026-07-28)

**Entschieden:** Die Gebäudeansicht (Liegenschaft als Haus: Etagen, Einheiten,
Belegung, Technik) sitzt als **Sicht im Reiter Struktur** — Umschalter
„Gebäudeansicht / Liste & Bearbeiten" —, nicht als eigener Reiter.

**Warum:** Der Auslöser war die Beschwerde über *zu viele* Reiter. Eine neue
Ansicht als zwölften Reiter zu hängen hätte das Problem vergrößert, das sie löst.
Haus und Baum sind nicht zwei Themen, sondern zwei Blickwinkel auf dieselben
Daten; der Umschalter sagt das auch.

**Ebenfalls entschieden:** Die Etagen-Reihenfolge wird **abgeleitet, nie
gespeichert** — `unit.storey` bleibt Freitext. Eine Codeliste für Etagen wäre die
naheliegende „Verbesserung" und verlöre das Haus mit dem Zwischengeschoss.
Ungedeutetes wird sichtbar unten gesammelt, nicht einsortiert.

Die zugehörige Verschlankungs-Recherche (11 → 6 Reiter, mit Aufwand und
Reihenfolge) liegt in `docs/roadmap/liegenschaft-reiter-verschlankung.md`.

## Etagenbänder gruppieren über die Höhe, nicht über den Text (2026-07-29)

**Entschieden:** Einheiten werden zu Bändern über die **abgeleitete Höhe**
gebündelt; eine bekannte Lageangabe (links/Mitte/rechts, li/mi/re) wird vorher
vom Etagentext abgespalten und ordnet die Wohnungen *innerhalb* des Bandes von
links nach rechts. Die Nummer sortiert danach **natürlich** („WE 2" vor „WE 10").

**Warum:** Die ursprüngliche Regel „gruppiere über den Originaltext" (28.07.)
hatte einen Nebennutzen — uneinheitliche Erfassung wurde sichtbar — und einen
Hauptschaden, der im Praxistest sofort auftrat: Erfasst wird nicht „EG", sondern
**„EG links"**, weil das Etagenfeld das einzige ist, in das die Lage passt. Damit
war jede Wohnung ihr eigenes „Stockwerk", nichts mehr deutbar und die
Reihenfolge alphabetisch — das EG stand über dem 3. OG. Ein Haus, das man nicht
lesen kann, ist schlimmer als eine unsauber erfasste Schreibweise.

**Der Nebennutzen bleibt, ohne das Haus zu zerreißen:** Das Band führt seine
`schreibweisen` („2. OG" *und* „2.OG") und weist aus, wenn eine Etage nur aus der
Einheitennummer abgeleitet ist (`abgeleitet`).

**Nicht neu aufmachen:** Abgespalten wird **nur**, wenn der Rest danach eine
deutbare Etage ist — „links hinten" bleibt unangetastet im Ungedeutet-Band. Und
eine nackte Zahl in der *Nummer* wird nie zum Stockwerk („3" = Wohnung 3).

---
Viel Erfolg. Halte dich an das Slice-Rezept, verifiziere end-to-end (nicht nur
Typecheck), und lass jeden substanziellen Slice von einem Opus-Reviewer prüfen.
