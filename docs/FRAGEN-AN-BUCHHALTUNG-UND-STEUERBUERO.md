# Fragen an die Buchhaltung und das Steuerbüro

**Betrifft:** Einführung der neuen Betriebssoftware (MCN)
**Stand:** 13.07.2026
**Ansprechpartner im Haus:** _____________________

---

## Worum es geht

Wir stellen unsere Auftrags- und Rechnungsabwicklung auf eine neue Software um.
Die Software erzeugt Angebote und Rechnungen, führt die offenen Posten und kann
die Buchungsdaten exportieren. Bevor wir damit produktiv gehen, brauchen wir zu
**vier Punkten** eine Aussage von Ihnen. Ohne diese Aussagen können wir die
Anbindung an Ihre Systeme nicht abschließen.

Die Fragen sind so gestellt, dass ein kurzes Kreuz oder ein Satz genügt.
Rückfragen jederzeit gern.

---

## 1. Umsatzsteuer-Voranmeldung — wer macht sie?

Davon hängt ab, ob unsere Rechnungen **automatisch** in Lexware landen müssen
oder ob der Export an Sie ausreicht.

> **Hintergrund:** Lexware Office berechnet die Umsatzsteuer-Voranmeldung aus
> allen dort erfassten Belegen. Fehlen unsere Ausgangsrechnungen in Lexware, wäre
> die Voranmeldung schlicht falsch. Macht dagegen die Kanzlei die Voranmeldung aus
> den DATEV-Daten, ist eine Lexware-Anbindung nicht zwingend.

**Frage:** Wer erstellt die Umsatzsteuer-Voranmeldung?

- [ ] Wir selbst, in **Lexware Office**
- [ ] Die **Steuerkanzlei**, aus den übergebenen Buchungsdaten
- [ ] Anders: _______________________________________________

---

## 2. Lexware — welches Produkt, und gibt es einen API-Zugang?

Es gibt zwei verschiedene Lexware-Welten, die technisch **nichts** miteinander zu
tun haben. Wir müssen wissen, welche bei uns läuft.

**Frage 2a:** Womit arbeiten Sie?

- [ ] **Lexware Office** — Anmeldung im **Browser** unter `app.lexware.de`
- [ ] Ein **installiertes Programm** auf einem Windows-Rechner
      (z. B. „Lexware buchhaltung", „Lexware financial office")
- [ ] Kein Lexware

**Frage 2b — nur falls Lexware Office (Browser):**
Bitte einmal im Browser einloggen und diese Adresse öffnen:

    app.lexware.de/addons/public-api

- [ ] Es erscheint eine Seite mit einem Knopf **„API-Schlüssel erstellen"**
- [ ] Die Seite gibt es nicht / der Knopf fehlt / es kommt ein Hinweis auf einen
      höheren Tarif

> **Warum das wichtig ist:** Die Schnittstelle gibt es nur im Tarif **„XL"**.
> Ohne diesen Tarif ist eine automatische Anbindung technisch nicht möglich —
> dann bliebe nur der Datei-Export.

---

## 3. Buchungsdaten-Export — kommt er bei Ihnen sauber an?

Wir erzeugen einen **DATEV-Buchungsstapel** (Format EXTF, wie es DATEV vorgibt).

**Frage:** Können Sie damit arbeiten?

- [ ] Ja, wir arbeiten mit **DATEV** — schicken Sie uns eine Testdatei
- [ ] Wir arbeiten mit Lexware und importieren dort über die
      **DATEV-Schnittstelle** — schicken Sie uns eine Testdatei
- [ ] Wir brauchen ein **anderes Format**, nämlich: _____________________

**Bitte in jedem Fall:** Wir stellen Ihnen eine **Testdatei** mit ein paar
Beispielrechnungen bereit. Bitte einmal probeweise einlesen und uns sagen, ob
sie fehlerfrei durchläuft. Das ist die einzige Art, das verlässlich zu klären.

Ergebnis des Testimports:

- [ ] Läuft fehlerfrei durch
- [ ] Es gibt Fehler / Warnungen, und zwar: __________________________________

Außerdem für die Zuordnung:

- Kontenrahmen: [ ] SKR03  [ ] SKR04  [ ] anderer: ____________
- Beraternummer: ____________   Mandantennummer: ____________
- Sachkontenlänge: ____________ Stellen
- Erster Monat des Wirtschaftsjahres: ____________

---

## 4. Abschlagsrechnungen und Sonderfälle — welche Konten?

Hier steckt die einzige Stelle, an der wir eine **fachliche Annahme** getroffen
haben. Die möchten wir von Ihnen bestätigt oder korrigiert bekommen.

### 4a) Abschlagsrechnungen

Wir stellen im Projektgeschäft Abschlagsrechnungen und am Ende eine
Schlussrechnung, die die Abschläge anrechnet. Die Software kann beides:

- **Variante „Erlös"**: Die Abschlagsrechnung wird wie eine normale Rechnung auf
  ein Erlöskonto gebucht.
- **Variante „Anzahlung"**: Die Abschlagsrechnung wird auf ein Konto für
  *erhaltene, versteuerte Anzahlungen* gebucht; die Schlussrechnung löst diese
  Anzahlungen wieder auf.

**Frage:** Welche Variante sollen wir verwenden?

- [ ] **Erlös** (bei echten Teilleistungen üblich)
- [ ] **Erhaltene Anzahlungen** mit Auflösung durch die Schlussrechnung
- [ ] Kommt darauf an — bitte erläutern: ____________________________________

**Falls „Erhaltene Anzahlungen":** Wir haben folgende Standardkonten hinterlegt.
Bitte prüfen und ggf. korrigieren:

| Fall | SKR03 | SKR04 | Ihre Korrektur |
|---|---|---|---|
| Anzahlungen, 19 % USt | 1718 | 3272 | ____________ |
| Anzahlungen, 7 % USt | 1711 | 3260 | ____________ |

### 4b) Die offene Frage: § 13b und steuerfreie Umsätze

**Das ist der Punkt, bei dem wir Ihre Aussage wirklich brauchen.**

Für Bauleistungen nach **§ 13b UStG** (Steuerschuldnerschaft des
Leistungsempfängers) und für **steuerfreie Umsätze** gibt es unseres Wissens
**kein Standard-Automatikkonto** für Anzahlungen.

Wir haben deshalb behelfsweise das **neutrale Konto für erhaltene Anzahlungen**
verwendet (SKR03 **1710** / SKR04 **3250**). **Das ist eine begründete Annahme
von uns — kein DATEV-Standard.**

**Frage:** Auf welche Konten sollen Anzahlungen gebucht werden bei …

- … **Bauleistungen nach § 13b UStG**: ______________________________________
- … **steuerfreien Umsätzen**: ______________________________________________

- [ ] Unsere Annahme (1710 / 3250) ist in Ordnung
- [ ] Bitte stattdessen die oben genannten Konten verwenden

---

## 5. Was wir Ihnen liefern können

Damit Sie das prüfen können, stellen wir bereit:

- eine **DATEV-Testdatei** (EXTF-Buchungsstapel) mit Beispielrechnungen,
  Abschlags- und Schlussrechnung, Gutschrift und Storno
- eine **Beispielrechnung als PDF**
- eine **E-Rechnung** im Format **ZUGFeRD/Factur-X** (PDF mit eingebetteten
  Daten, Profil EN 16931) — falls Sie prüfen möchten, ob Ihr System sie einliest

Bitte kurz Bescheid geben, was Sie davon brauchen.

---

## Antwort bitte an

Name: _____________________  Datum: ____________

Unterschrift / Kürzel: _____________________

Rückfragen an: _____________________________
