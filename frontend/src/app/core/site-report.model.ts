// Vertrag zu /api/workflow/site_reports (workflow.site_report in der DB).
// Ein Baustellenbericht hängt an einem ANKER: am Auftrag (work_order), am Einsatz
// (service_job) oder an beidem — nie im Leeren. Beim **freien Termin** (Einsatz
// ohne Auftrag) ist `work_order_id` null: das ist das Begehungsprotokoll. Fotos
// hängen über die Datei-Ablage (site_report_id) daran. Die Kundenunterschrift
// besiegelt den Bericht (ENTWURF → UNTERZEICHNET); danach ist er unveränderlich.

export type SiteReportStatus = 'ENTWURF' | 'UNTERZEICHNET';

/**
 * Briefkopf des Berichts (Befund B3/B8) — „das übliche Briefkopf-Gedöns".
 *
 * Nur im Detail befüllt, in Listen `null`: Er kostet je Bericht mehrere
 * Abfragen (Auftraggeber, Adresse, Belegung, Eigentümer) und wäre in einer
 * Liste ein N+1 für Angaben, die dort niemand liest.
 *
 * Alle Felder optional — ein Bericht am freien Termin hat keinen Auftrag und
 * damit keinen Auftraggeber; ein Auftrag am Gemeinschaftseigentum keine
 * Wohnung und damit keinen Mieter. Leer heißt „gibt es nicht".
 */
export interface SiteReportKopf {
  order_number: string | null;
  order_title: string | null;
  auftraggeber: string | null;
  auftraggeber_adresse: string | null;
  objekt_name: string | null;
  objekt_nummer: string | null;
  objekt_adresse: string | null;
  gebaeude: string | null;
  einheit: string | null;
  etage: string | null;
  /** Mehrere sind der Normalfall (Ehepaar = zwei Beteiligte). */
  mieter: string[];
  eigentuemer: string[];
  /** Fertige Anschriftblöcke fürs Dokumentblatt — dieselbe Form wie beim Beleg. */
  aussteller: string[];
  empfaenger: string[];
}

export interface SiteReport {
  id: string;
  kopf: SiteReportKopf | null;
  work_order_id: string | null;
  service_job_id: string | null;
  report_date: string;
  author_id: string | null;
  author_name: string | null;
  weather: string | null;
  activity_text: string;
  hours_worked: string | null;
  materials_note: string | null;
  remarks: string | null;
  status: SiteReportStatus;
  signed_by_name: string | null;
  signed_at: string | null;
  signature_file_id: string | null;
  version: number;
  created_at: string;
  /**
   * Gebuchte Arbeitszeit des Termins, je Lohngruppe zusammengefasst.
   *
   * **Nur in der Detailantwort** — die Liste liefert sie nicht (sie wäre dort
   * ein N+1). Deshalb optional.
   *
   * **Abgeleitet, nicht gespeichert:** Der Server rechnet sie bei jedem Abruf
   * frisch aus den Zeitbuchungen. Als Berichtsposition abgelegt stünden
   * dieselben Stunden zweimal in der Rechnung, weil die Abrechnung die
   * Buchungen ohnehin liest.
   */
  gebuchte_zeiten?: GebuchteZeit[];
}

export interface SiteReportListe {
  items: SiteReport[];
  total: number;
}

// POST /api/workflow/site_reports — mindestens eines von work_order_id und
// service_job_id ist Pflicht (Anker). Beim freien Termin nur service_job_id; der
// Server leitet den Auftrag aus dem Einsatz ab.
export interface SiteReportCreate {
  report_date: string;
  activity_text: string;
  work_order_id?: string | null;
  service_job_id?: string | null;
  weather?: string | null;
  hours_worked?: string | null;
  materials_note?: string | null;
  remarks?: string | null;
}

// PUT /api/workflow/site_reports/{id} — nur gesetzte Felder werden geändert.
export interface SiteReportUpdate {
  report_date?: string | null;
  service_job_id?: string | null;
  weather?: string | null;
  activity_text?: string | null;
  hours_worked?: string | null;
  materials_note?: string | null;
  remarks?: string | null;
}

// POST /api/workflow/site_reports/{id}/sign
export interface SiteReportSign {
  signed_by_name: string;
  signature_png_base64: string;
}

// --- Positionen (Migration 0080) --------------------------------------------
// Die Berichtsposition trägt Menge und Einheit — **niemals einen Preis**. Ein
// unterschriebener Bericht mit Preisen wäre eine Preisvereinbarung; der Preis
// entsteht erst in der Rechnung. Es gibt hier deshalb kein Preisfeld, auch nicht
// „nur zur Info".

/** Positionsart der BERICHTSposition — ohne ZWISCHENSUMME (der Bericht summiert nichts). */
export type SiteReportLineType =
  | 'MATERIAL'
  | 'ARBEITSZEIT'
  | 'PAUSCHALE'
  | 'FREMDLEISTUNG'
  | 'FAHRT'
  | 'ZUSCHLAG'
  | 'TEXT';

export interface SiteReportLine {
  id: string;
  position_number: number;
  line_type: SiteReportLineType;
  description: string;
  /** Ist-Menge (API-Dezimalstring). Bei TEXT null. */
  quantity: string | null;
  unit: string | null;
  source_article_id: string | null;
  source_assembly_id: string | null;
  /** Sollmenge aus dem Angebot (eingefroren). null = kein Soll → keine Abweichung. */
  planned_quantity: string | null;
  source_quote_line_id: string | null;
  note: string | null;
}

/** GET /api/workflow/site_reports/{id} — der Bericht mit seinen Positionen. */
/** Eine Lohngruppe mit ihren Stunden auf dem Termin (abgeleitet, s. u.). */
export interface GebuchteZeit {
  bezeichnung: string;
  stunden: number;
}

export interface SiteReportDetail extends SiteReport {
  lines: SiteReportLine[];
}

/**
 * PUT /api/workflow/site_reports/{id}/positionen — ersetzt ALLE Positionen.
 *
 * **Kein `planned_quantity`.** Das Soll wird serverseitig aus
 * `source_quote_line_id` abgeleitet; ohne Herkunft ist es verboten (422). Ein
 * frei gesetztes Soll landete sonst auf einem unterschriebenen Kundendokument.
 */
export interface SiteReportLineIn {
  line_type: SiteReportLineType;
  description?: string | null;
  quantity?: string | null;
  unit?: string | null;
  source_article_id?: string | null;
  source_assembly_id?: string | null;
  source_quote_line_id?: string | null;
  note?: string | null;
}

export interface SiteReportLines {
  items: SiteReportLine[];
  total: number;
}

/** Auswahlkandidat für „Aus Angebot vorbelegen" — bewusst ohne Beträge. */
export interface VorbelegbaresAngebot {
  id: string;
  quote_number: string | null;
  title: string;
  status: string;
}

// --- Soll-Ist-Abgleich am Auftrag -------------------------------------------
export type SollIstArt =
  | 'MEHRVERBRAUCH'
  | 'MINDERVERBRAUCH'
  | 'ZUSATZ'
  | 'ENTFALLEN'
  | 'UNVERAENDERT';

export interface SollIstPosition {
  schluessel: string;
  source_article_id: string | null;
  source_assembly_id: string | null;
  bezeichnung: string;
  einheit: string | null;
  soll: string;
  ist: string;
  differenz: string;
  art: SollIstArt;
}

/** Ein Angebot, auf das sich das Soll stützt (ohne Beträge). */
export interface SollIstAngebot {
  id: string;
  quote_number: string | null;
  title: string;
  status: string;
}

export interface SollIst {
  work_order_id: string;
  positionen: SollIstPosition[];
  /** Worauf sich das Soll stützt. Leer = dem Auftrag ist kein gültiges Angebot
   *  zugeordnet — dann ist alles ZUSATZ, und man sieht auch, warum. */
  angebote: SollIstAngebot[];
  /** Sind unsignierte (= noch änderbare) Berichte eingeflossen? Dann ist der
   *  Abgleich vorläufig. Wird ausgewiesen, nicht verschwiegen. */
  enthaelt_entwuerfe: boolean;
}

const LINE_TYPE_LABELS: Record<SiteReportLineType, string> = {
  MATERIAL: 'Material',
  ARBEITSZEIT: 'Arbeitszeit',
  PAUSCHALE: 'Pauschale',
  FREMDLEISTUNG: 'Fremdleistung',
  FAHRT: 'Fahrt',
  ZUSCHLAG: 'Zuschlag',
  TEXT: 'Textzeile',
};

export function siteReportLineTypeLabel(t: SiteReportLineType): string {
  return LINE_TYPE_LABELS[t] ?? t;
}

/** Alle wählbaren Positionsarten (TEXT wird über „Freie Textzeile" erzeugt). */
export const SITE_REPORT_MENGENARTEN: SiteReportLineType[] = [
  'MATERIAL',
  'ARBEITSZEIT',
  'PAUSCHALE',
  'FREMDLEISTUNG',
  'FAHRT',
  'ZUSCHLAG',
];

// Die Art wird IMMER als Text ausgewiesen; Symbol und Farbe kommen nur als
// zusätzlicher Kanal dazu (WCAG 2.2 AA, 1.4.1 — Status nie nur über Farbe).
const ART_LABELS: Record<SollIstArt, string> = {
  MEHRVERBRAUCH: 'Mehrverbrauch',
  MINDERVERBRAUCH: 'Minderverbrauch',
  ZUSATZ: 'Zusatz',
  ENTFALLEN: 'Entfallen',
  UNVERAENDERT: 'Unverändert',
};
const ART_SYMBOLE: Record<SollIstArt, string> = {
  MEHRVERBRAUCH: '▲',
  MINDERVERBRAUCH: '▼',
  ZUSATZ: '＋',
  ENTFALLEN: '✕',
  UNVERAENDERT: '＝',
};

export function sollIstArtLabel(a: SollIstArt): string {
  return ART_LABELS[a] ?? a;
}
export function sollIstArtSymbol(a: SollIstArt): string {
  return ART_SYMBOLE[a] ?? '·';
}
export function sollIstArtClass(a: SollIstArt): string {
  if (a === 'MEHRVERBRAUCH' || a === 'ZUSATZ') return 'stamp--warn';
  if (a === 'MINDERVERBRAUCH' || a === 'ENTFALLEN') return 'stamp--type';
  return 'stamp--positive';
}

const STATUS_LABELS: Record<SiteReportStatus, string> = {
  ENTWURF: 'Entwurf',
  UNTERZEICHNET: 'Unterzeichnet',
};

export function siteReportStatusLabel(s: SiteReportStatus): string {
  return STATUS_LABELS[s] ?? s;
}

export function siteReportStatusClass(s: SiteReportStatus): string {
  return s === 'UNTERZEICHNET' ? 'stamp--positive' : '';
}
