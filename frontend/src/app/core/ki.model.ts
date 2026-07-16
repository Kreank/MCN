/** Typen der KI-API (GET /api/ai/briefing). */

/** Bereich eines Briefing-Punkts — mappt im UI auf die Route der Kachel. */
export type BriefingBereich = 'aufgaben' | 'vorgaenge' | 'wartung' | 'angebote';

/** Dringlichkeit — nie nur über Farbe, immer mit Textlabel (WCAG 2.2). */
export type BriefingDringlichkeit = 'info' | 'bald' | 'ueberfaellig';

export interface BriefingPunkt {
  text: string;
  bereich: BriefingBereich;
  dringlichkeit: BriefingDringlichkeit;
}

export interface Briefing {
  schlagzeile: string;
  punkte: BriefingPunkt[];
  /** ISO-Zeitstempel der Generierung ("Stand"). */
  stand: string;
  /** false = deterministisches Fallback (kein Modell verfügbar/erreichbar). */
  ki_generiert: boolean;
  modell: string | null;
}
