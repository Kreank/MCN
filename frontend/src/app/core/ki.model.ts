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

// --- Konversationeller Assistent (frag das CRM) — Slice 5 ------------------

export type TurnRolle = 'USER' | 'ASSISTANT';
export type AssistentIntent = 'AUSKUNFT' | 'KENNZAHL' | 'VORSCHLAG';

/** Eine zitierte Entität (Grundlage der Antwort) — verlinkbar aufs Dossier. */
export interface Quelle {
  typ: string; // KONTAKT|LIEGENSCHAFT|PROJEKT|AUFTRAG|VORGANG|EINSATZ|ANGEBOT|RECHNUNG|ARTIKEL|LEISTUNG|MITARBEITER
  id: string;
  titel: string;
}

/** Eine Nachricht im Gespräch (Frage oder Antwort). */
export interface GespraechTurn {
  id: string;
  seq: number;
  role: TurnRolle;
  content: string;
  intent: AssistentIntent | null;
  sources: Quelle[];
  /** Gesetzt, wenn die Antwort einen Vorschlag angelegt hat (→ Freigabe). */
  proposal_id: string | null;
  ai_run_id: string | null;
  /** Nutzte die Antwort untrusted Inhalte? (dann Hinweis im UI). */
  aus_untrusted_quelle: boolean;
  created_at: string;
}

export interface Gespraech {
  id: string;
  title: string;
  status: 'ACTIVE' | 'ARCHIVED';
  created_at: string;
  updated_at: string;
}

export interface GespraechDetail extends Gespraech {
  turns: GespraechTurn[];
}

/** Antwort auf eine gestellte Frage (POST /conversations/frage). */
export interface FrageAntwort {
  conversation_id: string;
  frage: GespraechTurn;
  antwort: GespraechTurn;
}

/** Pfad ins Dossier für eine zitierte Quelle — oder null, wenn es keins gibt. */
export function quelleDossierPfad(q: Quelle): string | null {
  const map: Record<string, string> = {
    KONTAKT: 'kontakt',
    LIEGENSCHAFT: 'liegenschaft',
    PROJEKT: 'projekt',
    AUFTRAG: 'auftrag',
  };
  const seg = map[q.typ];
  return seg ? `/dossier/${seg}/${q.id}` : null;
}

const QUELLE_TYP_LABELS: Record<string, string> = {
  KONTAKT: 'Kontakt',
  LIEGENSCHAFT: 'Liegenschaft',
  PROJEKT: 'Projekt',
  VORGANG: 'Vorgang',
  AUFTRAG: 'Auftrag',
  EINSATZ: 'Einsatz',
  ANGEBOT: 'Angebot',
  RECHNUNG: 'Rechnung',
  ARTIKEL: 'Artikel',
  LEISTUNG: 'Leistung',
  MITARBEITER: 'Mitarbeiter',
};

export function quelleTypLabel(typ: string): string {
  return QUELLE_TYP_LABELS[typ] ?? typ;
}

export function intentLabel(i: AssistentIntent | null): string | null {
  switch (i) {
    case 'KENNZAHL':
      return 'Kennzahl';
    case 'VORSCHLAG':
      return 'Vorschlag';
    default:
      return null; // AUSKUNFT trägt kein Extra-Label
  }
}
