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

## Benachrichtigungen: ein Postfach für alles, kein Kanal je Bereich (2026-07-31)

**Entschieden:** `notify.notification` ist eine **einzige, bereichsübergreifende**
Tabelle in einem eigenen Schema. Das Ziel ist eine **weiche** Referenz
(`target_type` + `target_id`, kein FK) — dasselbe Zugeständnis wie in
`audit.domain_event`. Die Route zum Ziel baut das Frontend; in der DB steht
bewusst keine URL.

**Warum:** Ein harter FK ginge nur auf genau eine Tabelle. Bei der zweiten
Benachrichtigungsart (Termin, Freigabe, KI-Vorschlag) stünde man vor der Wahl,
eine zweite Tabelle anzulegen oder fünf nullbare FK-Spalten zu führen — beides
endet in einer Glocke, die je Bereich anders funktioniert. In `workflow` gelegt
hätte die Tabelle denselben Sog erzeugt.

**Niemand benachrichtigt sich selbst — und die DB verbietet es** (CHECK
`notification_kein_selbstruf`, nicht nur eine Service-Prüfung). Wer seine eigene
Aufgabe abhakt, bekommt dafür keinen roten Punkt. Das ist die Sorte Rauschen, an
der ein Postfach binnen einer Woche stirbt; danach liest es niemand mehr, und
die Meldung, auf die es ankam, geht mit unter.

**Änderbar ist ausschließlich `read_at`** (Trigger `notify.guard_notification`).
Eine Benachrichtigung, der man nicht ansieht, was sie ursprünglich meldete, ist
keine.

**`kind` ist ein geschlossenes Vokabular** — eine neue Art kostet eine Migration.
Absicht: Eine freie Textspalte hätte binnen weniger Slices vier Schreibweisen
derselben Art, und das UI könnte für keine eine verlässliche Beschriftung geben.

**Das Postfach hängt an KEINEM Modul-Recht** — dieselbe Begründung wie beim
eigenen Passwort (`/api/auth/password`): Der Endpunkt wirkt ausschließlich auf
`request.user`, es gibt keinen Parameter für ein fremdes Postfach. Hinge es an
`workflow/LESEN`, bekäme ein reines Buchhaltungskonto seine Freigabemeldungen nie
zu Gesicht — ein Recht aus dem falschen Bereich als Tor für alle anderen. Der
flächendeckende Wächtertest (`test_endpoint_schutz.py`) trägt die Ausnahme
namentlich; an ihre Stelle tritt ein inhaltlicher Nachweis (rollenloses Konto →
200, aber leer).

**Abfragetakt statt Push:** Die Glocke holt einmal pro Minute *nur den Zähler*
(`/api/benachrichtigungen/zaehler`, ein Zugriff auf den Teilindex) — und nur,
solange der Reiter sichtbar ist. Websockets wären für „ein Zahlwert pro Minute"
die deutlich teurere Antwort auf dieselbe Frage. Erst wenn Meldungen sekundengenau
ankommen müssen, wird das neu aufgemacht.

## Die Rückfrage gehört an den Datensatz, nicht ins Telefon (2026-07-31)

**Entschieden:** Aufgaben tragen einen **append-only Faden**
(`workflow.task_comment`, Muster `workflow.project_log`): kein UPDATE, kein
DELETE, Korrektur nur als neuer Eintrag. Statuswechsel schreiben **SYSTEM**-Zeilen
in denselben Faden.

**Warum append-only:** Eine Rückfrage, die man hinterher stillschweigend
umschreiben kann, ist als Nachweis wertlos — und genau als Nachweis wird sie
gebraucht („wir hatten doch besprochen, dass …"). Das UI bietet deshalb gar kein
Bearbeiten an; eine Schaltfläche, die der Server verweigert, wäre schlimmer als
keine.

**Warum SYSTEM-Zeilen im selben Faden:** Das Audit ist für die Revision, der
Faden für die zwei Menschen, die an der Aufgabe arbeiten. „Erledigt am Freitag"
muss zwischen den Rückfragen stehen, die dazu führten, sonst erzählt der Verlauf
die halbe Geschichte und der Rest bleibt im Telefonprotokoll.

**Kein `seq` wie bei `ai.conversation_turn`:** Dort nummeriert die Reihenfolge
den Modellkontext und muss lückenlos sein. Hier genügt `created_at, id` — mit
`seq` wären zwei gleichzeitige Beiträge ein UNIQUE-Konflikt für nichts.

---
Viel Erfolg. Halte dich an das Slice-Rezept, verifiziere end-to-end (nicht nur
Typecheck), und lass jeden substanziellen Slice von einem Opus-Reviewer prüfen.

## Entwürfe sind löschbar, Ausgestelltes nie (2026-08-02)

**Sascha:** *„Warum können wir Entwürfe nicht löschen? Finde ich blöd, das müllt
das System zu. Berichte, aus denen Rechnungen erstellt werden oder halt bestätigt
sind — das man die nicht mehr löschen kann, ok. Aber Entwürfe … bei Angebote und
Rechnungen dasselbe. Entwürfe alle löschbar. Sobald versendet oder bestätigt fest
und nicht mehr änderbar."*

**Entscheidung.** Die Löschsperre wird statusabhängig:

| | löschbar | fest |
|---|---|---|
| Baustellenbericht | `ENTWURF` | `ABGESCHLOSSEN`, `UNTERZEICHNET` |
| Angebot | `ENTWURF` | ab `VERSENDET` |
| Rechnung | `ENTWURF` | ab `VEROEFFENTLICHT` |

**Warum das kein Aufweichen ist.** Die GoBD verlangt Unveränderlichkeit ab dem
Zeitpunkt, an dem ein Beleg **entsteht** — nicht währenddessen. Ein Entwurf ist
kein Dokument: Er trägt keine Nummer, ist nie beim Kunden gewesen und begründet
keine Forderung. Ihn aufzubewahren dokumentiert nichts, es sammelt nur Müll an,
in dem später niemand den echten Beleg findet.

Bisher sperrte `util.forbid_mutation()` **pauschal** jedes DELETE, ohne den
Status anzusehen. Genau das ist der Fehler: Die Sperre ist richtig, sie greift
nur zu früh.

### Die Fallstricke — beim Bauen zwingend zu beachten

1. **Ein Rechnungsentwurf hält Abrechnungsbindungen** (`invoicing.billing_link`).
   Wird er gelöscht, ohne sie zu lösen, bleiben die Quellen (Stunden, Material,
   Berichts- und Angebotszeilen) **für immer** als abgerechnet markiert — die
   Arbeit wäre nie wieder fakturierbar, und der Grund wäre nirgends sichtbar.
   Der Storno löst die Bindung bereits; das Löschen muss denselben Weg gehen.
2. **Abhängige Zeilen zuerst**: Positionen, Rubriken, Beteiligte hängen per
   Fremdschlüssel am Kopf und tragen eigene `no_delete`-Trigger.
3. **Ein Berichtsentwurf kann in einem Rechnungsentwurf gebunden sein.** Dann ist
   er nicht frei löschbar — erst die Rechnung, dann der Bericht. Die Meldung muss
   sagen, welcher Beleg im Weg steht, sonst sucht der Bearbeiter im Dunkeln.
4. **Kein Kaskadenlöschen über Statusgrenzen hinweg.** Ein Auftrag mit einem
   unterzeichneten Bericht bleibt, wie er ist.

**Nicht verhandelbar bleibt:** Ausgestelltes wird nie gelöscht, sondern storniert
(B-21/B-30). Daran ändert diese Entscheidung nichts.

### Nachtrag 2026-08-02: Rechnungsentwürfe bleiben vorerst unlöschbar

Der erste Versuch ist **zurückgenommen** worden, und der Grund gehört
festgehalten, damit ihn niemand ein zweites Mal macht.

Der Ansatz war: Trigger auf `billing_link` lockern, sodass Bindungen einer
Entwurfsrechnung löschbar sind. Das öffnet ein Loch — **jede einzelne Bindung**
wäre damit löschbar, solange die Rechnung im Entwurf steht. Wer eine Bindung
entfernt, gibt die Quelle wieder frei, und die Doppelabrechnungssperre ist
spurlos ausgehebelt. Genau davor warnt `test_bindung_kann_nicht_geloescht_werden`
seit Migration 0084 („Ein gelöschter Link machte die Sperre spurlos rückgängig") —
der Test hat den Fehler gefangen.

**Was stattdessen zu prüfen ist**, wenn der Slice wieder aufgenommen wird:
* Fremdschlüssel `billing_link.invoice_id` auf `ON DELETE CASCADE` — dann
  verschwinden die Bindungen mit der Rechnung, ohne dass ein einzelnes DELETE
  je erlaubt wäre. Zu prüfen: ob der `BEFORE DELETE`-Trigger bei CASCADE feuert
  (er tut es) und wie er den Unterschied erkennt.
* Oder ein Verwerfen-Status statt echtem Löschen: Der Entwurf bleibt, ist aber
  aus allen Listen verschwunden — dann bleibt auch die Bindung heil.

**Angebote sind davon nicht betroffen** und seit `0146` löschbar: Sie tragen
keine Bindungen, weil erst beim Fakturieren gebunden wird.

### Entschieden 2026-08-02: Rechnungsentwürfe werden VERWORFEN, nicht gelöscht

**Sascha:** *„Ja wir nehmen das zweite. Aber es soll auch nur mit Entwürfen
gehen. Erstellte Rechnungen können nur wie gehabt über Storno berichtigt werden
(müssen dann neu erstellt werden)."*

**Der Weg.** Ein Rechnungsentwurf bekommt den Status `VERWORFEN`:

* Er verschwindet aus allen Listen und Auswahlen — das löst „das müllt das
  System zu", ohne dass eine Zeile Daten verlorengeht.
* Die **Abrechnungsbindungen werden gelöst**, nicht entfernt: `released_at` plus
  `released_reason = 'Entwurf verworfen'`. Damit sind Stunden, Material und
  Angebotszeilen wieder abrechenbar, und die gelöste Bindung bleibt als Nachweis
  stehen. Es ist derselbe Mechanismus wie beim Storno
  (`abrechnung.storniere_*`), nur mit anderem Grund.
* **Nur aus `ENTWURF`.** Ab `VEROEFFENTLICHT` bleibt es beim Storno mit
  Folgebeleg (B-21/B-30) — daran ändert sich nichts.

**Warum das besser ist als Löschen.** Der Löschweg hätte verlangt, den
Löschschutz auf `billing_link` zu lockern; damit wäre jede einzelne Bindung
angreifbar geworden und die Doppelabrechnungssperre spurlos aushebelbar (siehe
Nachtrag oben). Das Verwerfen fasst **keinen einzigen Schutztrigger an** — es ist
ein Statuswechsel plus die längst gebaute Bindungslösung.

**Zu bauen:**
1. Migration: `VERWORFEN` in den Status-CHECK von `invoicing.invoice`;
   Statusautomat erlaubt `ENTWURF → VERWORFEN` und sonst nichts von dort weg.
2. Dienst `verwirf_rechnung`: Status setzen + Bindungen lösen, in **einer**
   Transaktion.
3. Listen und Auswahlen filtern `VERWORFEN` heraus (Default), mit Schalter zum
   Einblenden — sonst wundert sich jemand, wo sein Entwurf geblieben ist.
4. Cockpit: Knopf „Entwurf verwerfen" mit Rückfrage, nur im Entwurf sichtbar.

**Für Angebote bleibt es beim echten Löschen** (0146) — sie tragen keine
Bindungen, dort gibt es nichts zu lösen.
