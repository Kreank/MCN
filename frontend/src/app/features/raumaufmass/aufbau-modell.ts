import { Adjacent, OpeningType, SurfaceType } from '../../core/raum.model';

/**
 * Der **Arbeitsstand** des Aufbaus im Editor — Zahlen in deutscher Eingabeform
 * (Strings mit Komma), noch nicht gespeichert.
 *
 * Er liegt bewusst hier und nicht in `raum-editor.ts`: Die Zeichnung
 * (`grundriss/grundriss-editor`) und die Liste (`raum-editor`) bearbeiten
 * **denselben** Arbeitsstand. Zwei Kopien würden auseinanderlaufen — und
 * spätestens beim `PUT …/aufbau` (das immer das GANZE schreibt) verlöre eine der
 * beiden ihre Änderungen.
 */

/** Eine Hüllfläche im Editor. `uid` ist zugleich der `ref` fürs Speichern. */
export interface Huelle {
  uid: string;
  surface_type: SurfaceType;
  adjacent: Adjacent;
  orientation: string;
  label: string;
  /**
   * Die Bruttofläche **als Eingabe**.
   *
   * **LEER auf einer Kantenwand heißt: abgeleitet** — der Client schickt sie dann
   * gar nicht mit, und der Server rechnet sie (Kantenlänge × Raumhöhe) und hält
   * sie bei jeder Änderung von Umriss oder Raumhöhe aktuell (Migration 0093).
   *
   * **Gefüllt heißt: Handeingabe** — sie wird gesendet und nie wieder vom Server
   * angefasst (Giebel, Erker). Das ist genau der Grund, warum sie hier leer
   * *bleiben* muss, solange niemand sie ausdrücklich überschreibt: Ein vorbelegter
   * Wert würde jede Wand still zur Handeingabe machen, und eine spätere
   * Höhenkorrektur ginge lautlos an ihr vorbei.
   *
   * Ohne Kante (Decke, Boden, Dachschräge) ist sie Pflicht.
   */
  brutto: string;
  u_value: string;
  temp_factor: string;
  /** Gewählte Katalog-Vorlage — nur die Herkunft; `u_value` ist eine Kopie. */
  template_id: string | null;
  /**
   * Die Polygonkante, auf der diese Wand steht. **null = keine Kante** (Decke,
   * Boden, Dachschräge — oder eine Wand ohne Zeichnung).
   */
  edge_index: number | null;
}

/** Eine Öffnung im Editor — sie hängt über `surfaceRef` an ihrer Wand. */
export interface Oeffnung {
  uid: string;
  surfaceRef: string | null;
  opening_type: OpeningType;
  label: string;
  anzahl: string;
  breite: string;
  hoehe: string;
  u_value: string;
  template_id: string | null;
  /**
   * Abstand vom Anfangspunkt der Kante, in Metern (deutsche Eingabeform).
   *
   * **Leer heißt UNBEKANNT, nicht 0.** Die Öffnung zählt trotzdem in Fläche und
   * Heizlast — sie wird nur nicht gezeichnet und steht in der Liste „ohne Lage in
   * der Wand". Sie stillschweigend bei 0 zu platzieren, wäre eine Erfindung.
   */
  position: string;
}

let seq = 0;
export const neueUid = (p: string): string => `${p}${++seq}`;

/** Bruttofläche einer Kantenwand = Kantenlänge × Raumhöhe (dieselbe Formel wie am Server). */
export function abgeleiteteWandflaeche(kanteM: number, hoeheM: number): number | null {
  if (!(kanteM > 0) || !(hoeheM > 0)) return null;
  return Math.round(kanteM * hoeheM * 1000) / 1000;
}
