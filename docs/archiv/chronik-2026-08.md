# Chronik August 2026

Fertige Erzählung — **nicht** als aktueller Stand lesen; der steht in
`docs/HANDOFF.md`. Hierhin wandert, was erledigt ist, damit das Handoff
lesbar bleibt (Regel aus CLAUDE.md: HANDOFF trägt nur den geltenden Stand,
Ziel unter 150 Zeilen).

---

## Erledigt Anfang August 2026

- **Plantafel: Auslastung rechnete die Wanduhr.** Ein Einsatz über vier Tage
  stellte den Monteur mit **185 % ausgelastet** auf die Tafel — Nächte und Pausen
  zählten als Arbeitszeit. Aus Saschas Praxisblick heraus gefunden, in zwei
  Review-Runden fanden sich **vier weitere Fehler derselben Klasse**: Wochenenden
  zählten im Zähler, aber nicht im Nenner (Do–Di = 120 %); die Pause fiel je
  *Einsatz* statt je *Arbeitstag* an (07–12 plus 12–16 = 9 h statt 8 h); die
  Nachtlücke am Fensterrand blieb stehen (derselbe Einsatz = 31 h oder 24 h, je
  nach Lage der Woche); ein ausgelaufener Vertrag leerte den Zähler ganz. Die
  ganze Regel steht jetzt in `INVARIANTEN.md` Abschnitt 11 samt Schadensbild.
  **Arbeitsbeginn/Feierabend/Pause sind Firmenprofil-Felder** (Migration 0148,
  Vorgabe 07:00–16:00 / 60 min); die Pausen*schwelle* bleibt Gesetz (§ 4 ArbZG).
  **Offen:** Der **Notdienst** braucht eine eigene Behandlung (vertagt, Sascha).
- **Plantafel-Bedienung nach Saschas HERO-Vergleich.** Steuerleiste von drei
  Bändern auf **eine Zeile** (~92 → ~40 px); der Rückstand klappt zur **Seite**
  wie die Navigation statt nach oben (Board gewinnt gut 16 rem — daran hängt, ob
  eine Woche ohne Scrollen in den Schirm passt) und bleibt eingeklappt
  Ablageziel; die Kachel-Aktionen hängen nicht mehr an einem 31 px hohen
  Hover-Streifen, sondern an einem festen **⋯-Griff** (Klick oder Rechtsklick,
  bleibt offen, 24 px nach WCAG 2.5.8). Nebengewinn: Vorher lagen je Kachel drei
  unsichtbare Tabstopps im DOM — bei 200 Kacheln 600 Stück.
- **Protokoll-Maske: der Entwurf IST die Maske.** „Neues Protokoll" legt den
  Bericht sofort an und zeigt ihn als bearbeitbares Blatt — der vorgeschaltete
  Formular-Dialog ist ersatzlos weg, „Bearbeiten" ebenfalls. Direkt danach die
  Startwahl **„aus welchem Angebot — oder leer?"**, aber nur, wenn es etwas zu
  übernehmen gibt (am freien Termin also nie). Das Feld *Material (Notiz)* ist aus
  der Maske raus (Material gehört in die Positionen); alte Notizen bleiben
  sichtbar und gehen beim Speichern nicht verloren. Im Reiter *Zeiten & Material*
  ist der Erfassungsweg für Material geschlossen — **bestehende Buchungen bleiben
  sichtbar und abrechenbar**, und der Endpunkt lebt weiter (die App bucht darüber).
  **Warum das erst jetzt ging:** Ein Klick, der sofort anlegt, setzt voraus, dass
  der Fehlklick folgenlos ist — Berichtsentwürfe sind erst seit `0145` löschbar.
  **Kein Backend-Eingriff**: `gebuchte_zeiten` (je Lohngruppe, abgeleitet) und
  `vorbelegen_aus_angebot` gab es bereits. 8 neue Frontend-Tests (322 gesamt grün).
- **Sammelrechnung gebaut und live** — „drei Bäder, alle drei Wohnungen gehören
  Herrn Meier": mehrere Rechnungs**entwürfe** werden zu **einem** Beleg, je
  Quellentwurf eine Rubrik mit dem Wohnungsbezug als Titel. Dienst
  `abrechnung.sammelrechnung`, Endpunkt `POST /invoicing/invoices/sammelrechnung`,
  Auswahl im Belegregister (Mehrfachauswahl → Bestätigungsdialog).
  **Keine Migration, kein Freigabetor angefasst**: Bindungen lösen → Entwürfe
  verwerfen (0147) → neue Rechnung an EINEM Auftrag → Quellen neu binden, alles
  in einer Transaktion. Der Beleg hängt weiter an genau einem Auftrag (B-08).
  **Dabei ein Loch geschlossen, das erst dadurch entstand:** Die
  quellenübergreifende Doppelabrechnungssperre fragte über den **Beleg**
  (`invoice.work_order_id`). Da eine Sammelrechnung an einem Auftrag hängt, aber
  die Quellen mehrerer bindet, verlören alle anderen beteiligten Aufträge ihre
  Klammer. Sie fragt jetzt über die **Herkunft der Quelle**
  (`_bindungen_des_auftrags`). 22 neue Tests, volle Suite grün (4519).

**Erledigt am 2026-08-02**
- **Entwürfe löschbar** — Bericht (`0145`) und Angebot (`0146`), beide live. Die
  pauschale Sperre `util.forbid_mutation()` wich statusabhängigen Triggern; beim
  Angebot entscheidet die **Belegnummer** (entsteht erst beim Versand), nicht der
  Status. **Rechnung bewusst NICHT** — siehe Nachtrag in `ENTSCHEIDUNGEN.md`: Der
  Löschweg hätte den Schutz auf `billing_link` gelockert und damit die
  Doppelabrechnungssperre aushebelbar gemacht.
- **Protokoll-Maske verbreitert**: neue Dialogstufe `arbeitsflaeche` (92rem),
  zweispaltig — ausgeführte Arbeiten links mit 8 Zeilen, Beiwerk rechts.
  Ausgeführte Arbeiten werden vorbelegt („Protokoll vom …"), weil `activity_text`
  in der DB nicht leer sein darf.
- **Dritter Berichtszustand `ABGESCHLOSSEN`** (`e66b36e`, live, Migration 0144):
  fertig ohne Unterschrift = **voll abrechenbar**. Hintergrund: 80 % der Berichte
  werden nie unterschrieben, die alte Regel sperrte damit den Normalfall aus.
  `ENTWURF` bleibt draußen. Dabei zwei Löcher geschlossen — Positionen eines
  abgeschlossenen Berichts waren noch änderbar (0080 prüfte nur `UNTERZEICHNET`),
  und das Ersetzen von `protect_site_report()` hätte fast den Briefkopf-Schutz aus
  0132 mitentfernt.
- **Lohngruppen angelegt** (live, über den Dienst): Meister/Techniker 85 €/h,
  Monteur 65, Helfer 45, Azubi 25. **Keine dieser Zahlen steht im Code** — Pflege
  unter *Einstellungen → Lohngruppen*, Endpunkte `/pricing/wage-groups` waren
  bereits vorhanden. `cost_rate` (Kostensatz für die Deckungsbeitrags-Auswertung)
  ist bewusst leer gelassen; den kennt nur der Betrieb.
- **Der Belegbezug steht** (`d78caf0`, live): Angebot, Rechnung und Bildschirm nennen
  Wohneinheit, Eigentümer, Mieter und „Vertreten durch". Eigentümer-Kaskade
  Wohnung → Liegenschaft → Gemeinschaft, damit WEG, Mietshaus und Eigenheim ohne
  Konfiguration bedient sind. Eingefroren in `billing_snapshot`, Live-Fallback je
  Feld. Regel und Schaden in `INVARIANTEN.md` §2; Auflöser in
  `services/belegbezug.py`. **Kein Schemaeingriff.**
  *Nächster Schritt dort:* Ein Auftrag über **mehrere** Wohnungen (drei Bäder, eine
  Rechnung) zeigt heute nur die Einheit am Auftrag. Nach der Eigentumsgrenze wäre
  das dreimal Sondereigentum — also drei Eigentümer auf einem Beleg. Mit dem User
  am fertigen Blatt klären, bevor gebaut wird.

**Erledigt am 2026-08-01** *(hier nur als Beleg, dass es nicht vergessen wurde)*
- `main`/`develop` sind auf `origin` — der Rückstand von 27 Commits ist Geschichte.
- Die volle Backend-Suite läuft wieder. Sie war seit `0114_geraetetoken` im Teardown
  kaputt: Djangos `flush` leert `public.accounts_user`, aber `security.device_token`
  hält einen Fremdschlüssel darauf und ist als `managed = False` nie Teil des
  TRUNCATE — Postgres verweigert das zu Recht. 19 Tests starben daran, ausgerechnet
  die für Mahnungs-Schreibpfad, Abrechnung unter Nebenläufigkeit und Löschschutz.
  Behoben in `backend/conftest.py` (Details siehe Kommentar dort); **kein Eingriff
  ins Fachschema**. Gegenprobe gegen `main` bestätigt: vorbestehend, keine Regression.

