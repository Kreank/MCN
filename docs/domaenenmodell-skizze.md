# Domänenmodell — Saschas Skizze (2026-07-19)

Handskizze aus dem Praxisbetrieb, abgelegt als `domaenenmodell-skizze.png`. Der
Inhalt steht hier zusätzlich als Text, weil ein Bild weder durchsuchbar noch
zitierbar ist.

> Diese Datei ist **Dokumentation, kein Arbeitsauftrag.** Was daraus umgesetzt
> werden soll, steht in den Befundlisten (`DISPONENT_BEFUNDE*.md`).

## Die vier Ebenen

| Ebene | Was daran hängt |
|---|---|
| **Liegenschaft** | Verwaltung · Eigentümer · Mieter |
| **Kontakte** | Verwaltungen · Eigentümer · Firma · Öffentlich · Sonstiges |
| **Auftrag** | Termine · Dokumente (Angebote, Baustellenberichte, Rechnungen) · Fotos · Objektadresse · Mieter · Beschreibung · Zeiterfassung |
| **Projekte** | „Alles aus Aufträge und die verschiedenen Aufträge selbst" |

## Die Regeln, wörtlich

> - Ein Auftrag kann erzeugt werden aus: Kontakte oder Liegenschaften
> - Eine Liegenschaft ist ein Zusammenschluss von Verwaltung, Eigentümer und
>   evtl. Mieter
> - Eine Liegenschaft kann dadurch, dass sie mehrere Eigentümer beherbergen
>   kann, auch mehrere Rechnungsadressen besitzen

## Das Beispiel, das die Rollen trennt

> Eigentümer Müller bekommt von seinem Mieter einen Anruf (Klo kaputt).
> Eigentümer ruft Verwaltung an und sagt: „Ruf mal Firma an, bei meinem Mieter
> ist Klo kaputt." Verwaltung ruft also uns an und sagt: „Von Eigentümer Müller
> hat der Mieter Promo angerufen und gesagt, Klo kaputt. Kümmert euch mal drum."
>
> **Dann ist der Auftraggeber die Verwaltung, der Rechnungsempfänger aber der
> Herr Müller.**

Daraus folgt unmittelbar:

> Es kann dadurch vorkommen, dass mehrere Eigentümer in einer Liegenschaft
> existieren. Aber **immer nur eine Verwaltung**. Es kann also sein, dass ich 20
> Rechnungsadressen habe, die ich immer angeben muss, wenn ich Dokumente dazu
> erzeuge.

## Wie sich das zum gebauten Stand verhält

**Getrennte Rollen am Beleg: vorhanden.** `PRINCIPAL` (Auftraggeber) am Auftrag
und `INVOICE_DEBTOR`/`INVOICE_RECIPIENT` am Beleg sind eigene Rollen; die
Beteiligtenverwaltung der Rechnung lässt beide unabhängig setzen. Das Beispiel
oben — Auftraggeber Verwaltung, Rechnungsempfänger Eigentümer — ist damit
abbildbar.

**Eine Verwaltung je Liegenschaft: vorhanden** (`management_mandate`).

**Mehrere Eigentümer je Liegenschaft: im Modell vorhanden, in der Oberfläche
nicht.** `tenure.ownership_period`/`ownership_interest` sind seit Migration 0005
vollständig gebaut (Bruchanteile, Anteilsprüfung, Quellennachweis), haben aber
**keinen Endpunkt und keine Oberfläche**. Das ist Arbeitspaket **AP5** aus
Runde 1. Für „Gebäude 52 gehört Herrn X" ohne Anteile gibt es die Rolle
`property_party_role = PROPERTY_OWNER` (Entscheidung 2026-07-21: kein Eigentum
mit Anteilen oberhalb der Einheit).

**„20 Rechnungsadressen, die ich angeben muss": der offene Punkt.** Die
Beteiligten lassen sich je Beleg von Hand setzen, aber es gibt keinen Weg, die
Rechnungsempfänger einer Liegenschaft **vorgeschlagen** zu bekommen. Solange
AP5 nicht angebunden ist, weiß das System gar nicht, wer die Eigentümer sind —
also kann es sie auch nicht anbieten. Das ist der praktische Grund, warum AP5
nicht bloß eine Lesefunktion ist.

**Kopfzeile Liegenschaft (AP2)** — „das sind Daten, die der Dispo schnell wissen
will" — hängt an derselben Stelle: Verwaltung und Mieter ließen sich heute schon
zeigen, der Eigentümer erst mit AP5.
