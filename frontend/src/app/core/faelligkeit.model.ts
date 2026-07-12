// Vertrag zu /api/maintenance (Fälligkeiten-Engine, Migration 0071).
//
// Drei Fristenarten, ein Modell: Wartung (Vertrag), Prüfung (Prüffrist an
// Liegenschaft/Anlage) und Gewährleistung (Fristablauf je Auftrag).
//
// WICHTIG (steht auch im UI): Das Produkt gibt **keine Rechtsauskunft**.
// Prüfarten sind Stammdaten, die der Betrieb selbst pflegt; die mitgelieferten
// sind Vorschläge (`is_suggestion`). Gewährleistungsfristen sind je Auftrag
// einstellbar — aus `basis` (BGB/VOB) wird keine Frist abgeleitet.
import { PropertyRef } from './projekt.model';

export type FaelligkeitArt = 'WARTUNG' | 'PRUEFUNG' | 'GEWAEHRLEISTUNG';
export type FaelligkeitStatus = 'OFFEN' | 'ERLEDIGT' | 'VERWORFEN';
export type Folgeaktion = 'TERMIN' | 'AUFTRAG' | 'PROJEKT' | 'AUFGABE' | 'ANGEBOT' | 'KEINE';

export interface DueItem {
  id: string;
  kind: FaelligkeitArt;
  title: string;
  due_date: string;
  lead_time_days: number;
  status: FaelligkeitStatus;
  ueberfaellig: boolean;
  tage_bis_faellig: number;
  property: PropertyRef | null;
  quelle: string;
  quelle_id: string | null;
  /** Werktags-Vorschlag. Die Fälligkeit selbst wird nie verschoben. */
  termin_vorschlag: string;
  termin_hinweis: string | null;
  /** Nur bei GEWAEHRLEISTUNG: Hinweis, kein Rechtsrat. */
  vertriebshinweis: string | null;
  result_object_type: string | null;
  result_object_id: string | null;
  resolution_note: string | null;
  resolved_at: string | null;
  resolved_by: string | null;
}

export interface DueItemPage {
  items: DueItem[];
  total: number;
  page: number;
  page_size: number;
  offen_total: number;
  ueberfaellig_total: number;
}

export interface DueItemQuery {
  page: number;
  page_size: number;
  status?: FaelligkeitStatus | null;
  kind?: FaelligkeitArt | null;
  property_id?: string | null;
  von?: string | null;
  bis?: string | null;
}

export interface DueItemErledigen {
  folgeaktion: Folgeaktion;
  termin_datum?: string | null;
  notiz?: string | null;
}

export interface DueItemErledigt {
  item: DueItem;
  hinweis: string | null;
}

// --- Prüfarten & Prüfungen --------------------------------------------------

export type IntervallArt = 'JAEHRLICH' | 'MONATLICH' | 'WOECHENTLICH' | 'TAGE';
export type PruefStatus = 'AKTIV' | 'INAKTIV' | 'ARCHIVIERT';

export interface InspectionType {
  id: string;
  name: string;
  interval_kind: IntervallArt;
  interval_days: number | null;
  lead_time_days: number;
  responsibility: string | null;
  notes: string | null;
  /** true = mitgelieferter Vorschlag (kein Normkatalog, keine Rechtsauskunft). */
  is_suggestion: boolean;
  is_active: boolean;
}

export interface InspectionTypeCreate {
  name: string;
  interval_kind: IntervallArt;
  interval_days?: number | null;
  lead_time_days?: number;
  responsibility?: string | null;
  notes?: string | null;
}

export interface Inspection {
  id: string;
  name: string;
  inspection_type_id: string;
  inspection_type_name: string;
  status: PruefStatus;
  start_date: string;
  interval_kind: IntervallArt;
  interval_days: number | null;
  lead_time_days: number;
  next_due_date: string | null;
  responsibility: string | null;
  notes: string | null;
  property: PropertyRef;
  asset_id: string | null;
  is_due: boolean;
}

export interface InspectionPage {
  items: Inspection[];
  total: number;
  page: number;
  page_size: number;
}

export interface InspectionCreate {
  inspection_type_id: string;
  property_id: string;
  start_date: string;
  name?: string | null;
  interval_kind?: IntervallArt | null;
  interval_days?: number | null;
  lead_time_days?: number | null;
  responsibility?: string | null;
  notes?: string | null;
}

// --- Gewährleistung ---------------------------------------------------------

export type GewaehrleistungBasis = 'BGB' | 'VOB' | 'INDIVIDUELL';

export interface Warranty {
  id: string;
  work_order_id: string;
  order_number: string;
  order_title: string;
  basis: GewaehrleistungBasis;
  start_date: string;
  duration_months: number;
  end_date: string;
  lead_time_days: number;
  is_machinery: boolean;
  status: 'AKTIV' | 'ARCHIVIERT';
  notes: string | null;
  property: PropertyRef;
  laeuft_ab_in_tagen: number;
  abgelaufen: boolean;
  vertriebshinweis: string | null;
}

export interface WarrantyPage {
  items: Warranty[];
  total: number;
  page: number;
  page_size: number;
  default_months: number;
  default_lead_days: number;
  vorschlaege: Record<string, number>;
}

export interface WarrantyPatch {
  start_date?: string;
  duration_months?: number;
  lead_time_days?: number;
  basis?: GewaehrleistungBasis;
  is_machinery?: boolean;
  notes?: string | null;
  status?: 'AKTIV' | 'ARCHIVIERT';
}

// --- Darstellung ------------------------------------------------------------

const ART_LABELS: Record<FaelligkeitArt, string> = {
  WARTUNG: 'Wartung',
  PRUEFUNG: 'Prüffrist',
  GEWAEHRLEISTUNG: 'Gewährleistung',
};

export function artLabel(a: FaelligkeitArt): string {
  return ART_LABELS[a] ?? a;
}

/** Farbklasse je Art. Status wird NIE nur über Farbe kommuniziert (WCAG) —
 *  neben dem Chip steht immer der Text. */
export function artClass(a: FaelligkeitArt): string {
  return `art--${a.toLowerCase()}`;
}

const STATUS_LABELS: Record<FaelligkeitStatus, string> = {
  OFFEN: 'Offen',
  ERLEDIGT: 'Erledigt',
  VERWORFEN: 'Verworfen',
};

export function statusLabel(s: FaelligkeitStatus): string {
  return STATUS_LABELS[s] ?? s;
}

const AKTION_LABELS: Record<Folgeaktion, string> = {
  TERMIN: 'Termin anlegen (Plantafel-Rückstand)',
  AUFTRAG: 'Auftrag anlegen',
  PROJEKT: 'Projekt anlegen',
  AUFGABE: 'Aufgabe anlegen',
  ANGEBOT: 'Angebot erzeugen',
  KEINE: 'Nur vermerken (kein Folgeobjekt)',
};

export function folgeaktionLabel(a: Folgeaktion): string {
  return AKTION_LABELS[a] ?? a;
}

export const FOLGEAKTIONEN: readonly Folgeaktion[] = [
  'TERMIN',
  'AUFTRAG',
  'ANGEBOT',
  'AUFGABE',
  'PROJEKT',
  'KEINE',
];

const INTERVALL_LABELS: Record<IntervallArt, string> = {
  JAEHRLICH: 'Jährlich',
  MONATLICH: 'Monatlich',
  WOECHENTLICH: 'Wöchentlich',
  TAGE: 'Alle N Tage',
};

export function intervallLabel(k: IntervallArt, tage: number | null): string {
  if (k === 'TAGE' && tage) return `Alle ${tage} Tage`;
  return INTERVALL_LABELS[k] ?? k;
}

/** „in 12 Tagen" / „seit 3 Tagen überfällig" / „heute". */
export function fristText(tage: number): string {
  if (tage === 0) return 'heute fällig';
  if (tage > 0) return `in ${tage} ${tage === 1 ? 'Tag' : 'Tagen'}`;
  const ueber = Math.abs(tage);
  return `seit ${ueber} ${ueber === 1 ? 'Tag' : 'Tagen'} überfällig`;
}
