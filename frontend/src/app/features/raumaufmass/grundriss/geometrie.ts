import type { Orientation } from '../../../core/raum.model';

/**
 * Rechenkern des Grundrisses — **reine Funktionen, kein Angular, kein State**.
 *
 * DIE DREI REGELN AUS DEM MODULKOPF DER MIGRATION 0091:
 *
 * 1. **Koordinaten sind ganzzahlige Millimeter** im System des GESCHOSSES.
 *    Nie Gleitkomma: sonst liegen die Kanten zweier Nachbarräume „fast"
 *    aufeinander und die Fläche wandert je nach Rundung um Quadratzentimeter.
 *    Jede Funktion hier gibt wieder ganze Millimeter zurück — es wird **einmal**
 *    gerundet, bewusst, und nicht bei jeder Zwischenrechnung.
 *
 * 2. **Kante `i` = Strecke von Punkt `i` nach Punkt `i+1`**, zyklisch (die letzte
 *    Kante schließt zurück auf Punkt 0).
 *
 * 3. **Die Fläche ist der BETRAG der Trapezformel.** Der Umlaufsinn (im oder
 *    gegen den Uhrzeigersinn) darf keine negative Fläche erzeugen.
 *
 * Alles, was hier gerechnet wird, ist **Vorschau**. Verbindlich ist, was der
 * Server nach `PUT /rooms/{id}/grundriss` zurückgibt — er rechnet nach derselben
 * Formel, aber er ist die Quelle der Wahrheit.
 */

/** Ein Umrisspunkt. Ganze Millimeter, Koordinatensystem des Geschosses. */
export interface Punkt {
  readonly x_mm: number;
  readonly y_mm: number;
}

/** Kante `index`: von Punkt `index` nach Punkt `index+1` (zyklisch). */
export interface Kante {
  readonly index: number;
  readonly von: Punkt;
  readonly bis: Punkt;
  readonly laenge_mm: number;
}

/** Kaufmännisch runden — 3 Nachkommastellen ist die Skala der DB-Spalten. */
export function runde3(n: number): number {
  return Math.round((n + Number.EPSILON * Math.abs(n)) * 1000) / 1000;
}

/** Meter → ganze Millimeter. **Hier** wird gerundet, genau einmal. */
export function mmAusMeter(m: number): number {
  return Math.round(m * 1000);
}

/** Millimeter → Meter (3 Nachkommastellen = Millimetergenauigkeit). */
export function meterAusMm(mm: number): number {
  return runde3(mm / 1000);
}

/** Auf das Raster fangen. `raster <= 0` heißt: frei (nur auf ganze mm). */
export function snapMm(wert: number, raster: number): number {
  if (!(raster > 0)) return Math.round(wert);
  return Math.round(wert / raster) * raster;
}

/** Punkt aufs Raster fangen. */
export function snapPunkt(p: Punkt, raster: number): Punkt {
  return { x_mm: snapMm(p.x_mm, raster), y_mm: snapMm(p.y_mm, raster) };
}

// --- Kanten -----------------------------------------------------------------

/**
 * Die Kanten des geschlossenen Umrisses. Bei 2 Punkten gibt es genau EINE Kante
 * (die Rückkante wäre deckungsgleich) — der Umriss ist dann noch keiner.
 */
export function kanten(punkte: readonly Punkt[]): Kante[] {
  const n = punkte.length;
  if (n < 2) return [];
  const bis = n === 2 ? 1 : n;
  const out: Kante[] = [];
  for (let i = 0; i < bis; i++) {
    const a = punkte[i];
    const b = punkte[(i + 1) % n];
    out.push({ index: i, von: a, bis: b, laenge_mm: strecke(a, b) });
  }
  return out;
}

/** Länge einer Strecke in Millimetern (Gleitkomma — die PUNKTE bleiben ganzzahlig). */
export function strecke(a: Punkt, b: Punkt): number {
  return Math.hypot(b.x_mm - a.x_mm, b.y_mm - a.y_mm);
}

/**
 * Die **schließende** Kante (letzter Punkt → erster) ist NICHT frei setzbar: sie
 * ergibt sich daraus, dass der Umriss geschlossen ist. Wer sie ändern wollte,
 * müsste Punkt 0 verschieben — und damit auch Kante 0.
 */
export function istSchliessendeKante(index: number, anzahlPunkte: number): boolean {
  return anzahlPunkte >= 3 && index === anzahlPunkte - 1;
}

/**
 * Kantenlänge exakt setzen — **der Kern der Bedienung**: Der Handwerker misst
 * mit dem Laser 4,37 m und tippt 4,37, statt pixelgenau zu ziehen.
 *
 * Der Endpunkt der Kante wandert **entlang ihrer Richtung**; alle folgenden
 * Punkte wandern um denselben Vektor mit (die anschließenden Kanten behalten
 * damit Länge UND Richtung). Punkt 0 bleibt der Anker.
 *
 * **Was die Änderung aufnimmt, ist die schließende Kante.** Ein geschlossener
 * Umriss kann nicht alle Kanten gleichzeitig festhalten — irgendwo muss die
 * Differenz hin. Beim Rechteck heißt das: Kante 0 auf 4,37 m zu setzen macht den
 * Umriss zunächst schief; erst wenn auch die Gegenkante 2 auf 4,37 m gesetzt
 * wird, ist es wieder ein Rechteck. Genau so misst man auch: eine Kante nach der
 * anderen.
 *
 * `null` heißt: nicht möglich — schließende Kante, Länge ≤ 0 oder eine Kante der
 * Länge 0 (die hat keine Richtung, in die man sie verlängern könnte).
 */
export function kanteLaengeSetzen(
  punkte: readonly Punkt[],
  index: number,
  laenge_mm: number,
): Punkt[] | null {
  const n = punkte.length;
  if (index < 0 || index >= n - 1) return null; // schließende Kante: abgeleitet
  if (!(laenge_mm > 0)) return null;
  const a = punkte[index];
  const b = punkte[index + 1];
  const len = strecke(a, b);
  if (!(len > 0)) return null;
  const ex = (b.x_mm - a.x_mm) / len;
  const ey = (b.y_mm - a.y_mm) / len;
  const neu: Punkt = {
    x_mm: Math.round(a.x_mm + ex * laenge_mm),
    y_mm: Math.round(a.y_mm + ey * laenge_mm),
  };
  const dx = neu.x_mm - b.x_mm;
  const dy = neu.y_mm - b.y_mm;
  return punkte.map((p, k) =>
    k > index ? { x_mm: p.x_mm + dx, y_mm: p.y_mm + dy } : { x_mm: p.x_mm, y_mm: p.y_mm },
  );
}

// --- Punkte bearbeiten ------------------------------------------------------

export function punktSetzen(punkte: readonly Punkt[], index: number, p: Punkt): Punkt[] {
  return punkte.map((alt, k) =>
    k === index ? { x_mm: Math.round(p.x_mm), y_mm: Math.round(p.y_mm) } : alt,
  );
}

export function punktVerschieben(
  punkte: readonly Punkt[],
  index: number,
  dx_mm: number,
  dy_mm: number,
): Punkt[] {
  const p = punkte[index];
  if (!p) return [...punkte];
  return punktSetzen(punkte, index, { x_mm: p.x_mm + dx_mm, y_mm: p.y_mm + dy_mm });
}

/** Neuen Punkt in der MITTE der Kante `index` einfügen (er wird Punkt `index+1`). */
export function punktEinfuegen(punkte: readonly Punkt[], kanteIndex: number): Punkt[] {
  const n = punkte.length;
  if (n < 2) return [...punkte];
  const a = punkte[kanteIndex];
  const b = punkte[(kanteIndex + 1) % n];
  const mitte: Punkt = {
    x_mm: Math.round((a.x_mm + b.x_mm) / 2),
    y_mm: Math.round((a.y_mm + b.y_mm) / 2),
  };
  const out = [...punkte];
  out.splice(kanteIndex + 1, 0, mitte);
  return out;
}

export function punktLoeschen(punkte: readonly Punkt[], index: number): Punkt[] {
  return punkte.filter((_, k) => k !== index);
}

// --- Fläche und Umfang ------------------------------------------------------

/**
 * Fläche in m² nach der **Gauß'schen Trapezformel**, als BETRAG — beide
 * Umlaufsinne ergeben dieselbe positive Fläche.
 *
 * Weniger als 3 Punkte: 0 (es gibt keine Fläche — das ist keine Behauptung
 * über einen Raum, sondern über einen unfertigen Umriss; der Aufrufer zeigt in
 * dem Fall gar keine Zahl, sondern `pruefe()`s Befund „zu wenig Punkte").
 */
export function flaecheM2(punkte: readonly Punkt[]): number {
  const n = punkte.length;
  if (n < 3) return 0;
  let zweifach = 0;
  for (let i = 0; i < n; i++) {
    const a = punkte[i];
    const b = punkte[(i + 1) % n];
    zweifach += a.x_mm * b.y_mm - b.x_mm * a.y_mm;
  }
  // mm² → m²: durch 1e6. Der Betrag macht den Umlaufsinn gleichgültig.
  return runde3(Math.abs(zweifach) / 2 / 1_000_000);
}

/**
 * Umfang in m — Summe der Kantenlängen des geschlossenen Umrisses.
 *
 * **Jede Kante wird EINZELN auf Millimeter gerundet, dann wird summiert** — genau
 * so rechnet der Server. Die naheliegende Variante (rohe Gleitkommalängen
 * aufsummieren und einmal am Schluss runden) ist mathematisch „genauer" und
 * praktisch falsch: Sie liefert eine Summe, die nicht der Summe der Kantenlängen
 * entspricht, die daneben in der Liste stehen — und sie weicht vom Server ab.
 *
 * An einer Raute (0,0) → (1000,1000) → (0,2000) → (−1000,1000) machte das 5,657 m
 * hier gegen 5,656 m dort: Die Zahl sprang beim Speichern. Der Umfang ist die
 * Mengengrundlage für Sockelleisten und geht so in ein Angebot.
 *
 * Es gilt dieselbe Regel wie bei der Heizlast: **Die ausgewiesene Summe muss die
 * Summe der ausgewiesenen Teile sein.**
 */
export function umfangM(punkte: readonly Punkt[]): number {
  const n = punkte.length;
  if (n < 3) return 0;
  let m = 0;
  for (let i = 0; i < n; i++) m += meterAusMm(strecke(punkte[i], punkte[(i + 1) % n]));
  return runde3(m);
}

/** Umlaufsinn: `+1` = gegen den Uhrzeigersinn (y zeigt nach oben), `-1` = im UZS. */
export function umlaufsinn(punkte: readonly Punkt[]): 1 | -1 | 0 {
  const n = punkte.length;
  if (n < 3) return 0;
  let zweifach = 0;
  for (let i = 0; i < n; i++) {
    const a = punkte[i];
    const b = punkte[(i + 1) % n];
    zweifach += a.x_mm * b.y_mm - b.x_mm * a.y_mm;
  }
  if (zweifach > 0) return 1;
  if (zweifach < 0) return -1;
  return 0;
}

// --- Prüfung: was der Server ablehnen würde ---------------------------------

export type BefundArt = 'ZU_WENIG' | 'DOPPELT' | 'ENTARTET' | 'SELBSTSCHNITT';

/**
 * Ein Fehler im Umriss — **immer mit Klartext**, nie nur mit einer roten Kante.
 * `kanten`/`punkte` sagen, WO er sitzt (die Zeichnung hebt sie hervor).
 */
export interface Befund {
  readonly art: BefundArt;
  readonly text: string;
  readonly kanten: readonly number[];
  readonly punkte: readonly number[];
}

/** Vorzeichen der Kreuzprodukt-Orientierung von a→b→c (ganzzahlig, exakt). */
function orientierung(a: Punkt, b: Punkt, c: Punkt): number {
  const v = (b.x_mm - a.x_mm) * (c.y_mm - a.y_mm) - (b.y_mm - a.y_mm) * (c.x_mm - a.x_mm);
  return v > 0 ? 1 : v < 0 ? -1 : 0;
}

/** Liegt der (kollineare) Punkt p in der Bounding-Box der Strecke a–b? */
function imKasten(a: Punkt, b: Punkt, p: Punkt): boolean {
  return (
    p.x_mm >= Math.min(a.x_mm, b.x_mm) &&
    p.x_mm <= Math.max(a.x_mm, b.x_mm) &&
    p.y_mm >= Math.min(a.y_mm, b.y_mm) &&
    p.y_mm <= Math.max(a.y_mm, b.y_mm)
  );
}

/**
 * Schneiden sich die Strecken a–b und c–d (Berührung eingeschlossen)?
 * Ganzzahlige Millimeter → exakt, ohne Epsilon-Gefummel.
 */
export function streckenSchneiden(a: Punkt, b: Punkt, c: Punkt, d: Punkt): boolean {
  const o1 = orientierung(a, b, c);
  const o2 = orientierung(a, b, d);
  const o3 = orientierung(c, d, a);
  const o4 = orientierung(c, d, b);
  if (o1 !== o2 && o3 !== o4) return true;
  if (o1 === 0 && imKasten(a, b, c)) return true;
  if (o2 === 0 && imKasten(a, b, d)) return true;
  if (o3 === 0 && imKasten(c, d, a)) return true;
  if (o4 === 0 && imKasten(c, d, b)) return true;
  return false;
}

const gleich = (a: Punkt, b: Punkt) => a.x_mm === b.x_mm && a.y_mm === b.y_mm;

/**
 * Alles, was der Server mit **422** ablehnen würde — hier VOR dem Speichern und
 * im Klartext: zu wenig Punkte, doppelter Punkt, entartetes Polygon (Fläche 0),
 * Selbstschnitt.
 *
 * Zwei benachbarte Kanten teilen sich immer einen Punkt — das ist kein Schnitt.
 * Ein Schnitt ist es erst, wenn sie **zurückklappen** (die Kante läuft auf sich
 * selbst zurück) oder wenn sich zwei NICHT benachbarte Kanten berühren.
 */
export function pruefe(punkte: readonly Punkt[]): Befund[] {
  const n = punkte.length;
  const befunde: Befund[] = [];

  if (n < 3) {
    return [
      {
        art: 'ZU_WENIG',
        text:
          n === 0
            ? 'Noch kein Punkt gesetzt. Ein Umriss braucht mindestens 3 Punkte.'
            : `Der Umriss hat erst ${n} ${n === 1 ? 'Punkt' : 'Punkte'} — es sind mindestens 3 nötig.`,
        kanten: [],
        punkte: [],
      },
    ];
  }

  // Doppelte Punkte: eine Kante der Länge 0 wäre eine Wand ohne Fläche.
  const doppelt: number[] = [];
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      if (gleich(punkte[i], punkte[j])) {
        if (!doppelt.includes(i)) doppelt.push(i);
        if (!doppelt.includes(j)) doppelt.push(j);
      }
    }
  }
  if (doppelt.length) {
    befunde.push({
      art: 'DOPPELT',
      text:
        `Zwei Punkte liegen aufeinander (${doppelt.map((i) => `Punkt ${i + 1}`).join(', ')}). ` +
        'Das ergäbe eine Kante ohne Länge — bitte einen davon verschieben oder löschen.',
      kanten: [],
      punkte: doppelt,
    });
  }

  if (flaecheM2(punkte) === 0) {
    befunde.push({
      art: 'ENTARTET',
      text:
        'Der Umriss hat keine Fläche — alle Punkte liegen auf einer Linie. ' +
        'Bitte mindestens einen Punkt herausziehen.',
      kanten: [],
      punkte: [],
    });
  }

  // Selbstschnitt.
  const geschnitten = new Set<number>();
  for (let i = 0; i < n; i++) {
    const a = punkte[i];
    const b = punkte[(i + 1) % n];
    for (let j = i + 1; j < n; j++) {
      const c = punkte[j];
      const d = punkte[(j + 1) % n];
      const benachbart = j === i + 1 || (i === 0 && j === n - 1);
      if (benachbart) {
        // Nur der Rückklapper zählt: die zweite Kante läuft in die Gegenrichtung
        // der ersten und überlagert sie (Stachel).
        if (orientierung(a, b, d) === 0 && orientierung(a, b, c) === 0) {
          const v1x = b.x_mm - a.x_mm;
          const v1y = b.y_mm - a.y_mm;
          const v2x = d.x_mm - c.x_mm;
          const v2y = d.y_mm - c.y_mm;
          if (v1x * v2x + v1y * v2y < 0) {
            geschnitten.add(i);
            geschnitten.add(j);
          }
        }
        continue;
      }
      if (streckenSchneiden(a, b, c, d)) {
        geschnitten.add(i);
        geschnitten.add(j);
      }
    }
  }
  if (geschnitten.size) {
    const liste = [...geschnitten].sort((x, y) => x - y);
    befunde.push({
      art: 'SELBSTSCHNITT',
      text:
        `Der Umriss überschlägt sich: ${liste.map((i) => `Kante ${i + 1}`).join(' und ')} ` +
        'kreuzen einander. Ein Raum kann sich nicht selbst durchdringen — bitte die ' +
        'Reihenfolge der Punkte oder ihre Lage korrigieren.',
      kanten: liste,
      punkte: [],
    });
  }

  return befunde;
}

/** Ist der Umriss speicherbar? (Kein Befund = der Server nimmt ihn an.) */
export function istGueltig(punkte: readonly Punkt[]): boolean {
  return pruefe(punkte).length === 0;
}

// --- Öffnungen in ihrer Kante -----------------------------------------------

export type Passung =
  | { readonly art: 'passt' }
  /** Nicht entscheidbar — die Lage ODER die Breite fehlt. Nie „passt", nie 0. */
  | { readonly art: 'unbekannt'; readonly grund: string }
  | { readonly art: 'passt_nicht'; readonly grund: string };

/**
 * Passt die Öffnung in ihre Kante? (`position + Breite ≤ Kantenlänge`.)
 *
 * **Unvollständig heißt UNBEKANNT, nicht 0** — die Hausregel dieses Slices, und
 * zwar für BEIDE Angaben:
 *  - Ohne `position_m` ist die Lage nicht ausgemessen. Die Öffnung zählt trotzdem
 *    in Fläche und Heizlast, sie wird nur nicht gezeichnet.
 *  - Ohne `breite_m` lässt sich die Frage nicht beantworten. Die Breite als 0
 *    anzunehmen hieße „passt immer" zu melden — eine Behauptung über eine
 *    Öffnung, deren Maß noch niemand getippt hat.
 */
export function oeffnungPasst(
  position_m: number | null,
  breite_m: number | null,
  kante_m: number | null,
): Passung {
  if (position_m == null) {
    return { art: 'unbekannt', grund: 'Die Lage in der Wand ist nicht ausgemessen.' };
  }
  if (breite_m == null) {
    return {
      art: 'unbekannt',
      grund: 'Ohne Breite lässt sich nicht sagen, ob die Öffnung in die Kante passt.',
    };
  }
  if (kante_m == null || !(kante_m > 0)) {
    return { art: 'passt_nicht', grund: 'Die Kante hat keine Länge.' };
  }
  if (position_m < 0) {
    return { art: 'passt_nicht', grund: 'Die Lage darf nicht negativ sein.' };
  }
  const b = breite_m;
  // 1 mm Toleranz: die Kantenlänge ist eine Wurzel, die Breite eine Eingabe.
  if (position_m + b > kante_m + 0.001) {
    return {
      art: 'passt_nicht',
      grund:
        `Die Öffnung reicht über die Kante hinaus: ${runde3(position_m)} m + ` +
        `${runde3(b)} m = ${runde3(position_m + b)} m, die Kante ist aber nur ` +
        `${runde3(kante_m)} m lang.`,
    };
  }
  return { art: 'passt' };
}

/** Punkt auf der Kante im Abstand `position_m` vom ANFANGSPUNKT. */
export function punktAufKante(k: Kante, position_m: number): Punkt {
  const len = k.laenge_mm;
  if (!(len > 0)) return k.von;
  const t = (position_m * 1000) / len;
  return {
    x_mm: k.von.x_mm + (k.bis.x_mm - k.von.x_mm) * t,
    y_mm: k.von.y_mm + (k.bis.y_mm - k.von.y_mm) * t,
  };
}

// --- Ansicht: Weltkoordinaten (mm) ⇄ SVG-Einheiten ---------------------------

export interface Kasten {
  readonly min_x: number;
  readonly min_y: number;
  readonly max_x: number;
  readonly max_y: number;
}

export function kasten(punkte: readonly Punkt[]): Kasten | null {
  if (!punkte.length) return null;
  let min_x = punkte[0].x_mm;
  let max_x = min_x;
  let min_y = punkte[0].y_mm;
  let max_y = min_y;
  for (const p of punkte) {
    if (p.x_mm < min_x) min_x = p.x_mm;
    if (p.x_mm > max_x) max_x = p.x_mm;
    if (p.y_mm < min_y) min_y = p.y_mm;
    if (p.y_mm > max_y) max_y = p.y_mm;
  }
  return { min_x, min_y, max_x, max_y };
}

/**
 * Abbildung Welt (mm, y zeigt nach OBEN) → SVG (Einheiten, y zeigt nach UNTEN).
 * Die y-Achse wird gespiegelt: sonst stünde der Grundriss auf dem Kopf.
 */
export interface Sicht {
  readonly skala: number; // SVG-Einheiten je Millimeter
  readonly breite: number;
  readonly hoehe: number;
  readonly mitte_x: number; // Weltmitte
  readonly mitte_y: number;
}

/**
 * Kleinste Weltausdehnung, auf die eingepasst wird: **2 Meter**.
 *
 * Ohne diese Schranke lief das Einpassen in entarteten Fällen in eine sinnlose
 * Zoomstufe: Bei EINEM Punkt ist die Bounding-Box 0 × 0 groß; mit dem alten
 * `Math.max(…, 1)` blieben 1 mm × 1 mm übrig und die Skala sprang auf **560
 * SVG-Einheiten je Millimeter**. Die Zeichenfläche zeigte danach knapp 2 mm × 1 mm
 * Welt — das Raster war weg, und **jeder** weitere Klick snappte auf denselben
 * ersten Punkt zurück („Dort liegt bereits ein Punkt."). Das Zeichnen mit der Maus
 * war ab dem ersten Punkt tot.
 *
 * Derselbe Fehler traf jede kollineare Zwischenstufe (zwei Punkte auf einer
 * Achse): Dort ist EINE der beiden Ausdehnungen 0, und die Skala entgleist in
 * dieser Achse.
 */
const MIN_AUSDEHNUNG_MM = 2000;

/**
 * Sicht so wählen, dass der Umriss (bzw. das Vorgabefeld) mit Rand hineinpasst.
 *
 * **Bei weniger als 2 Punkten gibt es nichts einzupassen** — dann gilt das
 * Standardfeld (`vorgabe_mm` breit), bei einem Punkt um ihn herum zentriert.
 * Ansonsten wird die Bounding-Box benutzt, aber **nie unter `MIN_AUSDEHNUNG_MM`**:
 * Eine Ausdehnung von 0 (ein Punkt, oder alle Punkte auf einer Linie) darf keine
 * Zoomstufe erzeugen, in der nichts mehr zu treffen ist.
 */
export function sichtEinpassen(
  punkte: readonly Punkt[],
  breite = 1000,
  hoehe = 700,
  rand = 70,
  vorgabe_mm = 12000,
): Sicht {
  const k = kasten(punkte);
  // Nichts einzupassen: 0 oder 1 Punkt. Standardfeld, ggf. um den Punkt zentriert.
  const nutzeVorgabe = punkte.length < 2;
  const w = nutzeVorgabe || !k ? vorgabe_mm : Math.max(k.max_x - k.min_x, MIN_AUSDEHNUNG_MM);
  const h =
    nutzeVorgabe || !k
      ? (vorgabe_mm * hoehe) / breite
      : Math.max(k.max_y - k.min_y, MIN_AUSDEHNUNG_MM);
  const skala = Math.min((breite - 2 * rand) / w, (hoehe - 2 * rand) / h);
  return {
    skala,
    breite,
    hoehe,
    mitte_x: k ? (k.min_x + k.max_x) / 2 : 0,
    mitte_y: k ? (k.min_y + k.max_y) / 2 : 0,
  };
}

/** Weltpunkt → SVG-Koordinate. */
export function zuSicht(p: Punkt, s: Sicht): { x: number; y: number } {
  return {
    x: s.breite / 2 + (p.x_mm - s.mitte_x) * s.skala,
    // Spiegelung: Welt-y nach oben, SVG-y nach unten.
    y: s.hoehe / 2 - (p.y_mm - s.mitte_y) * s.skala,
  };
}

/** SVG-Koordinate → Weltpunkt (ganze Millimeter). */
export function zuWelt(x: number, y: number, s: Sicht): Punkt {
  return {
    x_mm: Math.round(s.mitte_x + (x - s.breite / 2) / s.skala),
    y_mm: Math.round(s.mitte_y - (y - s.hoehe / 2) / s.skala),
  };
}

/** Liegt der Punkt in der sichtbaren Fläche? (Sonst: „Ansicht einpassen".) */
export function inSicht(p: Punkt, s: Sicht): boolean {
  const v = zuSicht(p, s);
  return v.x >= 0 && v.x <= s.breite && v.y >= 0 && v.y <= s.hoehe;
}

// --- Himmelsrichtung ---------------------------------------------------------

/** Kompasswinkel im Uhrzeigersinn ab Nord. */
const KOMPASS: Record<Orientation, number> = {
  N: 0,
  NO: 45,
  O: 90,
  SO: 135,
  S: 180,
  SW: 225,
  W: 270,
  NW: 315,
};

export type Nord =
  | { readonly art: 'unbekannt' }
  | { readonly art: 'widerspruch' }
  | { readonly art: 'richtung'; readonly x: number; readonly y: number };

/**
 * Wo ist Norden? Die Zeichnung selbst weiß es nicht — aber die **Ausrichtung der
 * Wände** verrät es: Die Außennormale einer Kante zeigt in ihre Himmelsrichtung.
 * Aus jeder ausgerichteten Kante folgt damit ein Nordvektor; gemittelt ergibt das
 * den Nordpfeil.
 *
 * Widersprechen sich die Angaben (Resultierende zu kurz), wird **kein Pfeil
 * gezeigt**, sondern `widerspruch` gemeldet — ein erfundener Nordpfeil wäre
 * schlimmer als keiner.
 */
export function nordRichtung(
  punkte: readonly Punkt[],
  orientierungJeKante: readonly (Orientation | null)[],
): Nord {
  const n = punkte.length;
  if (n < 3) return { art: 'unbekannt' };
  const sinn = umlaufsinn(punkte);
  if (sinn === 0) return { art: 'unbekannt' };

  let sx = 0;
  let sy = 0;
  let anzahl = 0;
  for (let i = 0; i < n; i++) {
    const o = orientierungJeKante[i];
    if (!o) continue;
    const a = punkte[i];
    const b = punkte[(i + 1) % n];
    const dx = b.x_mm - a.x_mm;
    const dy = b.y_mm - a.y_mm;
    const len = Math.hypot(dx, dy);
    if (!(len > 0)) continue;
    // Innen liegt bei positivem Umlaufsinn links der Kante → außen ist rechts.
    const nx = (sinn === 1 ? dy : -dy) / len;
    const ny = (sinn === 1 ? -dx : dx) / len;
    // Norden = Außennormale um den Kompasswinkel GEGEN den Uhrzeigersinn zurückgedreht.
    const t = (KOMPASS[o] * Math.PI) / 180;
    sx += nx * Math.cos(t) - ny * Math.sin(t);
    sy += nx * Math.sin(t) + ny * Math.cos(t);
    anzahl++;
  }
  if (!anzahl) return { art: 'unbekannt' };
  const len = Math.hypot(sx, sy);
  // Mittlere Vektorlänge < 0,5 → die Angaben widersprechen sich zu stark.
  if (len / anzahl < 0.5) return { art: 'widerspruch' };
  return { art: 'richtung', x: sx / len, y: sy / len };
}

// --- Textbeschreibung (das barrierefreie Äquivalent zur Zeichnung) -----------

function m(n: number, stellen = 2): string {
  return new Intl.NumberFormat('de-DE', {
    minimumFractionDigits: stellen,
    maximumFractionDigits: stellen,
  }).format(n);
}

/**
 * Der Umriss **als Satz** — für `aria-label`/`desc` des SVG. Wer die Zeichnung
 * nicht sieht, bekommt hier dasselbe gesagt: Kantenzahl, Ausdehnung, Fläche,
 * Umfang und was an den Kanten hängt.
 */
export function beschreibung(
  punkte: readonly Punkt[],
  zusatz: {
    readonly waende?: number;
    readonly oeffnungen?: number;
    readonly ohneLage?: number;
  } = {},
): string {
  const n = punkte.length;
  if (n === 0) return 'Noch kein Umriss gezeichnet.';
  if (n < 3) {
    return `Angefangener Umriss mit ${n} ${n === 1 ? 'Punkt' : 'Punkten'} — es sind mindestens 3 nötig.`;
  }
  const k = kasten(punkte)!;
  const teile = [
    `Umriss mit ${n} Kanten`,
    `${m(meterAusMm(k.max_x - k.min_x))} m × ${m(meterAusMm(k.max_y - k.min_y))} m`,
    `${m(flaecheM2(punkte))} m² Fläche`,
    `${m(umfangM(punkte))} m Umfang`,
  ];
  if (zusatz.waende != null) {
    teile.push(`${zusatz.waende} von ${n} Kanten mit einer Wand belegt`);
  }
  if (zusatz.oeffnungen) teile.push(`${zusatz.oeffnungen} Öffnungen eingezeichnet`);
  if (zusatz.ohneLage) teile.push(`${zusatz.ohneLage} Öffnungen ohne Lage in der Wand`);
  return `${teile.join(', ')}.`;
}
