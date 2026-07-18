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

// --- KI-Vorschläge (ai_proposal) — die Freigabe-Ansicht --------------------

export type ProposalStatus = 'PENDING' | 'APPROVED' | 'REJECTED' | 'EXPIRED';

/** Ein KI-Vorschlag in der Liste (ohne den vollen Entwurf). */
export interface KiVorschlag {
  id: string;
  proposal_type: string;
  target_type: string;
  target_id: string;
  status: ProposalStatus;
  created_at: string;
  expires_at: string;
  /** Server-abgeleitet: stammt der Entwurf aus einer untrusted Quelle (ASR/OCR/Vision)? */
  aus_untrusted_quelle: boolean;
  titel: string;
  anzahl_positionen: number;
  auftrag_titel: string | null;
  modell: string | null;
  workflow: string | null;
}

/** Eine Entwurfszeile (v1: Berichtsposition — nie Preise). */
export interface VorschlagZeile {
  line_type: string;
  description: string;
  quantity?: number | null;
  unit?: string | null;
}

export interface VorschlagPayload {
  activity_text?: string;
  lines?: VorschlagZeile[];
  [k: string]: unknown;
}

/** Ein KI-Vorschlag samt vollem Entwurf (Detailabruf). */
export interface KiVorschlagDetail extends KiVorschlag {
  proposed_payload: VorschlagPayload;
}

/** Ergebnis der Annahme: das materialisierte Fachobjekt. */
export interface VorschlagAnnahme {
  proposal_id: string;
  status: ProposalStatus;
  result_type: string;
  result_id: string;
  work_order_id: string | null;
}

export function proposalStatusLabel(s: ProposalStatus): string {
  switch (s) {
    case 'PENDING':
      return 'Offen';
    case 'APPROVED':
      return 'Angenommen';
    case 'REJECTED':
      return 'Abgelehnt';
    case 'EXPIRED':
      return 'Abgelaufen';
  }
}

export function proposalStatusClass(s: ProposalStatus): string {
  // Globale Stempelvarianten aus styles.scss (die Marke führt kein Rot):
  // Offen = amber (braucht Entscheidung), Angenommen = salbeigrün, Abgelehnt =
  // negativ (Amber-Ton), Abgelaufen = neutral.
  switch (s) {
    case 'PENDING':
      return 'stamp--warn';
    case 'APPROVED':
      return 'stamp--positive';
    case 'REJECTED':
      return 'stamp--negativ';
    case 'EXPIRED':
      return 'stamp--type';
  }
}

/** Vorschlagstyp lesbar (v1 kennt nur den Berichtsentwurf). */
export function proposalTypLabel(t: string): string {
  return t === 'SITE_REPORT_ENTWURF' ? 'Einsatzbericht-Entwurf' : t;
}

/** Positionsart einer Entwurfszeile lesbar. */
export function zeileTypLabel(t: string): string {
  const map: Record<string, string> = {
    MATERIAL: 'Material',
    ARBEITSZEIT: 'Arbeitszeit',
    PAUSCHALE: 'Pauschale',
    FREMDLEISTUNG: 'Fremdleistung',
    FAHRT: 'Fahrt',
    ZUSCHLAG: 'Zuschlag',
    TEXT: 'Text',
  };
  return map[t] ?? t;
}
