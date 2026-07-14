import { DEZIMAL_UNGUELTIG, deZuApiDezimal } from '../../shared/formular/dezimal';

/**
 * Rechenkern des Raumaufmaßes — reine Funktionen, kein Angular, kein State.
 *
 * **WAS HIER GERECHNET WERDEN DARF:** ausschließlich die *triviale Geometrie*
 * für das Sofort-Feedback auf der Baustelle —
 *   - Fläche      = Länge × Breite
 *   - Volumen     = Fläche × Höhe
 *   - Öffnung     = Anzahl × Breite × Höhe
 *   - Nettofläche = Bruttofläche − Öffnungen darin
 *
 * **WAS HIER NIEMALS GERECHNET WIRD:** die Heizlast. Sie kommt vom Server und
 * wird nur angezeigt. Fehlt eine Angabe (U-Wert, Temperaturfaktor …), ist sie
 * **unbekannt** — mit Grund. Nie 0, nie geschätzt.
 *
 * **Dezimaltrennzeichen:** Eingaben laufen durch `deZuApiDezimal` aus
 * `shared/formular/dezimal.ts`. Eine mehrdeutige Eingabe („1.500" = 1500 oder
 * 1,5?) wird ABGELEHNT, nicht geraten — hier hing schon einmal ein
 * Datenverlust-Bug (1200 → „1.200" → gespeichert als 1,5).
 */

/** Pflichthinweis auf jeder Heizlast-Ausgabe des Raumaufmaßes. */
export const RAUM_HEIZLAST_HAFTUNG =
  'Überschlägige Werte aus dem Aufmaß. KEIN Nachweis nach DIN EN 12831, nicht ' +
  'förderfähig (BEG/KfW) und nicht für den hydraulischen Abgleich „Verfahren B" ' +
  'geeignet. Dafür ist die raumweise Normheizlast zu berechnen.';

/** Auswertung einer deutschen Zahleingabe. */
export type Eingabe =
  { art: 'leer' } | { art: 'wert'; api: string; zahl: number } | { art: 'fehler' };

/**
 * Deutsche Eingabe auswerten. `fehler` heißt: unlesbar ODER mehrdeutig — beides
 * wird abgelehnt, damit keine falsche Zahl in die Datenbank wandert.
 */
export function eingabe(roh: string | null | undefined): Eingabe {
  const api = deZuApiDezimal(roh);
  if (api === '') return { art: 'leer' };
  if (api === DEZIMAL_UNGUELTIG) return { art: 'fehler' };
  const zahl = Number(api);
  return Number.isFinite(zahl) ? { art: 'wert', api, zahl } : { art: 'fehler' };
}

/** Zahl einer Eingabe, oder null (leer, unlesbar, mehrdeutig). */
export function zahlAus(roh: string | null | undefined): number | null {
  const e = eingabe(roh);
  return e.art === 'wert' ? e.zahl : null;
}

/** Ob eine ausgefüllte Eingabe unbrauchbar ist (leer gilt als brauchbar). */
export function istFehleingabe(roh: string | null | undefined): boolean {
  return eingabe(roh).art === 'fehler';
}

/** Ganzzahl (Stückzahlen). Leer/unlesbar/negativ → null. */
export function ganzzahlAus(roh: string | null | undefined): number | null {
  const s = (roh ?? '').trim();
  if (!/^\d+$/.test(s)) return null;
  const n = Number(s);
  return Number.isFinite(n) && n > 0 ? n : null;
}

/** Kaufmännisch runden — 3 Nachkommastellen ist die Skala der DB-Spalten. */
export function runde(n: number, stellen = 3): number {
  const f = 10 ** stellen;
  return Math.round((n + Number.EPSILON * Math.abs(n)) * f) / f;
}

/**
 * Zahl als API-Dezimalstring (Punkt, max. 3 Nachkommastellen, ohne
 * Tausendertrenner) — die Form, die die API erwartet. Aus einer `number` wird
 * hier ein String und er bleibt einer.
 */
export function apiZahl(n: number): string {
  const s = runde(n, 3).toFixed(3);
  return s.replace(/0+$/, '').replace(/\.$/, '');
}

/**
 * Fläche aus Länge × Breite — die **Herleitung**, nicht die Wahrheit: der
 * Anwender darf sie überschreiben (L-förmige Räume). Null, sobald ein Maß fehlt,
 * unlesbar oder ≤ 0 ist.
 */
export function flaecheAusMassen(
  laenge: string | null | undefined,
  breite: string | null | undefined,
): number | null {
  const l = zahlAus(laenge);
  const b = zahlAus(breite);
  if (l == null || b == null || !(l > 0) || !(b > 0)) return null;
  return runde(l * b, 3);
}

/** Volumen = Fläche × Höhe (Vorschau; die DB führt eine GENERATED-Spalte). */
export function volumenAus(
  flaeche: string | null | undefined,
  hoehe: string | null | undefined,
): number | null {
  const f = zahlAus(flaeche);
  const h = zahlAus(hoehe);
  if (f == null || h == null || !(f > 0) || !(h > 0)) return null;
  return runde(f * h, 3);
}

/** Öffnungsfläche = Anzahl × Breite × Höhe. Null, sobald ein Maß fehlt. */
export function oeffnungFlaeche(
  anzahl: string | null | undefined,
  breite: string | null | undefined,
  hoehe: string | null | undefined,
): number | null {
  const n = ganzzahlAus(anzahl);
  const b = zahlAus(breite);
  const h = zahlAus(hoehe);
  if (n == null || b == null || h == null || !(b > 0) || !(h > 0)) return null;
  return runde(n * b * h, 3);
}

/** Eine Öffnung, so wie sie im Editor steht (deutsche Eingabeform). */
export interface OeffnungMasse {
  readonly surfaceRef: string | null;
  readonly anzahl: string;
  readonly breite: string;
  readonly hoehe: string;
}

/** Eine Hüllfläche, so wie sie im Editor steht. */
export interface HuelleMasse {
  readonly brutto: string;
}

/**
 * Eine Fläche in der Live-Vorschau: entweder ein Wert — oder **unbekannt mit
 * Grund**. Es gibt bewusst keinen dritten Fall „0, weil noch nichts da ist":
 * eine halb getippte Öffnung (Breite da, Höhe leer) als 0 m² zu zählen, würde
 * die Wand zu groß zeigen. Unvollständig heißt unbekannt.
 */
export type Flaechenwert =
  | { readonly art: 'wert'; readonly m2: number }
  | { readonly art: 'unbekannt'; readonly grund: string };

const wert = (m2: number): Flaechenwert => ({ art: 'wert', m2: runde(m2, 3) });
const unbekannt = (grund: string): Flaechenwert => ({ art: 'unbekannt', grund });

/** Ist eine Öffnung vollständig vermessen (Anzahl, Breite, Höhe lesbar und > 0)? */
function oeffnungUnvollstaendig(o: OeffnungMasse): boolean {
  return oeffnungFlaeche(o.anzahl, o.breite, o.hoehe) == null;
}

/**
 * Summe der Öffnungsflächen, die einer bestimmten Wand zugeordnet sind.
 *
 * Ist auch nur EINE dieser Öffnungen unvollständig (typisch: Breite getippt,
 * Höhe noch leer), ist die Summe **unbekannt** — nicht „so viel wie bisher".
 * Keine Öffnung an dieser Wand ist dagegen eine echte 0.
 */
export function oeffnungenSumme(ref: string, oeffnungen: readonly OeffnungMasse[]): Flaechenwert {
  const meine = oeffnungen.filter((o) => o.surfaceRef === ref);
  if (meine.some(oeffnungUnvollstaendig)) {
    return unbekannt('Maße einer Öffnung in dieser Fläche sind unvollständig.');
  }
  return wert(meine.reduce((s, o) => s + (oeffnungFlaeche(o.anzahl, o.breite, o.hoehe) ?? 0), 0));
}

/** Summe ALLER Öffnungen des Raumes — auch der keiner Wand zugeordneten. */
export function oeffnungenGesamt(oeffnungen: readonly OeffnungMasse[]): Flaechenwert {
  if (oeffnungen.some(oeffnungUnvollstaendig)) {
    return unbekannt('Maße mindestens einer Öffnung sind unvollständig.');
  }
  return wert(
    oeffnungen.reduce((s, o) => s + (oeffnungFlaeche(o.anzahl, o.breite, o.hoehe) ?? 0), 0),
  );
}

/**
 * Nettofläche einer Hüllfläche: brutto − Öffnungen darin.
 *
 * **Unbekannt**, wenn die Bruttofläche fehlt/unlesbar ist oder eine Öffnung in
 * dieser Fläche noch unvollständig vermessen ist — dann gibt es keine
 * Nettofläche, auch keine 0.
 *
 * Ist die Summe der Öffnungen größer als die Wand, ist das Ergebnis **negativ**
 * und wird auch so gezeigt: Das ist ein Erfassungsfehler, und ihn auf 0 zu
 * klemmen würde ihn verstecken. Die Datenbank lehnt ihn beim Speichern ab
 * (Migration 0086 je Fläche, Migration 0089 zusätzlich raumweit: Σ aller
 * Öffnungen ≤ Σ aller Bauteilflächen — auch für Öffnungen ohne Wandzuordnung).
 */
export function nettoFlaeche(
  brutto: string | null | undefined,
  ref: string,
  oeffnungen: readonly OeffnungMasse[],
): Flaechenwert {
  const e = eingabe(brutto);
  if (e.art === 'leer') return unbekannt('Die Bruttofläche fehlt.');
  if (e.art === 'fehler') return unbekannt('Die Bruttofläche ist nicht lesbar.');
  const oeffn = oeffnungenSumme(ref, oeffnungen);
  if (oeffn.art === 'unbekannt') return oeffn;
  return wert(e.zahl - oeffn.m2);
}

/**
 * Bruttowandfläche des ganzen Raumes (Summe der Hüllflächen).
 *
 * **Ohne eine einzige Hüllfläche ist sie unbekannt, nicht 0** — genau wie beim
 * Server: „kein Bauteil aufgenommen" heißt nicht „dieser Raum hat keine Hülle".
 */
export function bruttoGesamt(huellen: readonly HuelleMasse[]): Flaechenwert {
  if (!huellen.length) return unbekannt('Für diesen Raum ist keine Hüllfläche aufgenommen.');
  let summe = 0;
  for (const h of huellen) {
    const e = eingabe(h.brutto);
    if (e.art !== 'wert') {
      return unbekannt('Mindestens eine Hüllfläche hat keine lesbare Bruttofläche.');
    }
    summe += e.zahl;
  }
  return wert(summe);
}

/** Nettowandfläche des ganzen Raumes: Σ brutto − Σ aller Öffnungen. */
export function nettoGesamt(
  huellen: readonly HuelleMasse[],
  oeffnungen: readonly OeffnungMasse[],
): Flaechenwert {
  const brutto = bruttoGesamt(huellen);
  if (brutto.art === 'unbekannt') return brutto;
  const oeffn = oeffnungenGesamt(oeffnungen);
  if (oeffn.art === 'unbekannt') return oeffn;
  return wert(brutto.m2 - oeffn.m2);
}

// --- Anzeige ---------------------------------------------------------------

/** Deutsche Anzeige einer Zahl mit fester Nachkommastellenzahl (nie im Eingabefeld). */
export function zeige(n: number, nachkomma = 2): string {
  return new Intl.NumberFormat('de-DE', {
    minimumFractionDigits: nachkomma,
    maximumFractionDigits: nachkomma,
    useGrouping: true,
  }).format(n);
}

/**
 * API-Wert (String vom Server; ein float wäre auch verkraftbar) als deutsche
 * Anzeige. `null` bleibt **„unbekannt"** — es wird NIE zu „0".
 */
export function zeigeApi(
  wert: string | number | null | undefined,
  nachkomma = 2,
  unbekannt = 'unbekannt',
): string {
  if (wert == null || wert === '') return unbekannt;
  const n = Number(wert);
  return Number.isFinite(n) ? zeige(n, nachkomma) : String(wert);
}

/**
 * Wert **mit Einheit** — oder das nackte „unbekannt". Wichtig: ein unbekannter
 * Wert bekommt KEINE Einheit angehängt („unbekannt m²" wäre Unsinn und liest
 * sich wie eine Zahl, die nur nicht angezeigt wird).
 */
export function mitEinheit(
  wert: string | number | null | undefined,
  einheit: string,
  nachkomma = 2,
  unbekannt = 'unbekannt',
): string {
  const s = zeigeApi(wert, nachkomma, '');
  return s === '' ? unbekannt : `${s} ${einheit}`;
}

/**
 * Summe einer API-Wertspalte über mehrere Räume (Fläche, Volumen — **nie eine
 * Heizlast**, die summiert der Server). Fehlende Werte werden übersprungen, sie
 * zählen NICHT als 0; ist gar nichts bekannt, ist die Summe `null` und nicht 0.
 */
export function summeApi(werte: readonly (string | number | null | undefined)[]): number | null {
  const zahlen = werte
    .filter((w) => w != null && w !== '')
    .map((w) => Number(w))
    .filter((n) => Number.isFinite(n));
  if (!zahlen.length) return null;
  return runde(
    zahlen.reduce((s, n) => s + n, 0),
    3,
  );
}
