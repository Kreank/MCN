// Vertrag zu /api/workflow/work_orders (workflow.work_order in der DB).
import { PropertyRef, ProjectMini, StatusChangeEntry } from './projekt.model';

export type WorkOrderStatus =
  | 'ENTWURF'
  | 'FREIGABE_AUSSTEHEND'
  | 'FREIGEGEBEN'
  | 'IN_PLANUNG'
  | 'IN_AUSFUEHRUNG'
  | 'TECHNISCH_ABGESCHLOSSEN'
  | 'KAUFMAENNISCH_GEPRUEFT'
  | 'ABGERECHNET'
  | 'STORNIERT';

export type OrderPriority = 'NORMAL' | 'DRINGEND' | 'NOTFALL';

export interface WorkOrder {
  id: string;
  order_number: string;
  title: string;
  status: WorkOrderStatus;
  priority: OrderPriority;
  responsibility_scope: string;
  is_emergency: boolean;
  desired_date: string | null;
  created_at: string;
  property: PropertyRef;
  project: ProjectMini | null;
  service_case_number: string | null;
}

export interface WorkOrderPage {
  items: WorkOrder[];
  total: number;
  page: number;
  page_size: number;
}

export interface WorkOrderQuery {
  page: number;
  page_size: number;
  q?: string;
  status?: WorkOrderStatus | null;
  project_id?: string | null;
  property_id?: string | null;
  service_case_id?: string | null;
}

export interface WorkOrderParty {
  party_id: string;
  display_name: string;
  role: string;
  is_primary: boolean;
  allocation_percent: string | null;
  source: string;
}

/**
 * Woraus die Rechnung dieses Auftrags entsteht (Migration 0084).
 *
 * - `PAUSCHAL` (Default): Die Rechnung ist die **Angebotskopie**. Zeiten und
 *   Berichtspositionen sind **Nachweis**, kein Rechnungsposten — das Angebot
 *   enthält die Leistung bereits; sie zusätzlich zu fakturieren hieße, doppelt
 *   zu kassieren.
 * - `REGIE`: Die Rechnung entsteht aus dem **Ist** (unterzeichnete Berichte +
 *   Zeitbuchungen).
 */
export type BillingMode = 'PAUSCHAL' | 'REGIE';

export interface WorkOrderDetail extends WorkOrder {
  description: string | null;
  customer_reference: string | null;
  order_evidence_reference: string | null;
  responsibility_confirmed_at: string | null;
  billing_mode: BillingMode;
  version: number;
  parties: WorkOrderParty[];
  history: StatusChangeEntry[];
}

// PATCH /api/workflow/work_orders/{id}
// Verlangt workflow/AENDERN UND invoicing/AENDERN. Nach erfolgter Abrechnung
// lehnt der Server den Wechsel mit 422 ab (Doppelabrechnungssperre).
export interface WorkOrderPatch {
  billing_mode: BillingMode;
}

// --- Offene Abrechnung ------------------------------------------------------
// GET /api/workflow/work_orders/{id}/offene-abrechnung
// Auftragssicht über die ganze Baustelle: Scope EIGENE bekommt 403 (fail-closed).

/** Ein **Vorschlag** für einen unbekannten Preis — nie ein gesetzter Wert. */
export interface PreisVorschlag {
  art: 'LETZTER_PREIS' | 'LISTENPREIS' | 'LOHNGRUPPE';
  /** Decimal als String (Geld wird nie als JS-number geführt). */
  betrag: string;
  quelle: string;
}

/** `UNBEKANNT` heißt: der Server hat KEINEN Preis — nicht 0,00 €. */
export type PreisStatus = 'BEKANNT' | 'UNBEKANNT';

export interface OffeneBerichtsposition {
  site_report_line_id: string;
  site_report_id: string;
  report_date: string;
  position_number: number;
  line_type: string;
  description: string;
  quantity: string | null;
  unit: string | null;
  preis_status: PreisStatus;
  /** null = unbekannt, NIE 0. */
  einzelpreis: string | null;
  grund: string | null;
  grund_text: string | null;
  vorschlaege: PreisVorschlag[];
}

/** Abgerechnet wird je **Lohngruppe** — ohne Lohngruppe je Mitarbeiter. */
export interface OffeneZeitgruppe {
  quelle_id: string;
  bezeichnung: string;
  wage_group_id: string | null;
  stunden: string;
  time_entry_ids: string[];
  preis_status: PreisStatus;
  einzelpreis: string | null;
  grund: string | null;
  grund_text: string | null;
  vorschlaege: PreisVorschlag[];
}

export interface UnsignierterBericht {
  id: string;
  report_date: string;
  status: string;
  activity_text: string;
}

export interface OffeneAbrechnung {
  work_order_id: string;
  billing_mode: BillingMode;
  /** false bei PAUSCHAL: die Positionen sind **Nachweis**, kein Rechnungsposten. */
  abrechenbar: boolean;
  hinweis: string;
  berichtspositionen: OffeneBerichtsposition[];
  zeitgruppen: OffeneZeitgruppe[];
  nicht_unterzeichnete_berichte: UnsignierterBericht[];
}

// --- Nachtrag: die Rechnung aus den Abweichungen ---------------------------

/**
 * Eine abrechenbare Abweichung — **nicht die ganze Position**.
 *
 * Bei MEHRVERBRAUCH ist `menge` die **Differenz** (Ist − Soll): Die Sollmenge ist
 * mit der Pauschale bezahlt. Bei ZUSATZ ist sie die volle Menge (es gibt kein
 * Soll). `einzelpreis`/`betrag` sind **null = unbekannt, nie 0**.
 */
export interface NachtragPosition {
  schluessel: string;
  art: 'MEHRVERBRAUCH' | 'ZUSATZ';
  bezeichnung: string;
  einheit: string | null;
  soll: string;
  ist: string;
  /** Das, was JETZT abgerechnet wird. */
  menge: string;
  bereits_berechnet: string;
  preis_status: PreisStatus;
  einzelpreis: string | null;
  betrag: string | null;
  grund: string | null;
  grund_text: string | null;
  vorschlaege: PreisVorschlag[];
}

/** Eine Abweichung, deren Mehrmenge bereits in einer Rechnung steht. */
export interface NachtragAbgerechnet {
  schluessel: string;
  art: 'MEHRVERBRAUCH' | 'ZUSATZ';
  bezeichnung: string;
  einheit: string | null;
  menge: string;
  rechnungen: string[];
}

/**
 * Ein Posten, dessen Mengen in VERSCHIEDENEN Einheiten vorliegen (z. B.
 * „Stk"/„Stück" desselben Artikels).
 *
 * Fail-closed: nicht summierbar, nicht abrechenbar, bis ein Mensch die Einheiten
 * vereinheitlicht oder den echten Mehr-Einheiten-Fall bewusst trennt. Sonst
 * stünde derselbe Posten unter zwei Einheiten doppelt auf zwei Rechnungen.
 */
export interface EinheitKonflikt {
  schluessel: string;
  bezeichnung: string;
  einheiten: string[];
}

export interface NachtragVorschau {
  work_order_id: string;
  billing_mode: BillingMode;
  /** false bei REGIE: dort wird ohnehin das gesamte Ist fakturiert. */
  abrechenbar: boolean;
  hinweis: string;
  positionen: NachtragPosition[];
  bereits_abgerechnet: NachtragAbgerechnet[];
  /** Fail-closed: derselbe Artikel in verschiedenen Einheiten — nicht abrechenbar. */
  einheit_konflikte: EinheitKonflikt[];
  /** Summe **der bepreisbaren** Positionen — `preise_unbekannt` sagt, ob sie
   *  unvollständig ist. Eine Summe, die vollständig tut, wäre eine Lüge. */
  summe: string;
  preise_unbekannt: boolean;
  nicht_unterzeichnete_berichte: UnsignierterBericht[];
}

// --- Schreib-Payloads ------------------------------------------------------
// POST /api/workflow/work_orders
export interface WorkOrderCreate {
  property_id: string;
  title: string;
  project_id?: string | null;
  service_case_id?: string | null;
  description?: string | null;
  priority: OrderPriority;
  desired_date?: string | null;
  customer_reference?: string | null;
  is_emergency: boolean;
}

// POST /api/workflow/work_orders/{id}/parties
export interface WorkOrderPartyCreate {
  party_id: string;
  role: string;
  is_primary: boolean;
  allocation_percent?: string | null;
  source?: string;
}

// POST /api/workflow/work_orders/{id}/responsibility
export interface ResponsibilityInput {
  scope: string;
}

// POST /api/workflow/work_orders/{id}/evidence
export interface EvidenceInput {
  reference: string;
}

// POST /api/workflow/work_orders/{id}/status
export interface WorkOrderStatusInput {
  to_status: WorkOrderStatus;
  reason?: string | null;
}

// --- Darstellung (eine Quelle für Auftrag-Detail und Projektmappe) ---------
const WORK_ORDER_STATUS_LABELS: Record<WorkOrderStatus, string> = {
  ENTWURF: 'Entwurf',
  FREIGABE_AUSSTEHEND: 'Freigabe ausstehend',
  FREIGEGEBEN: 'Freigegeben',
  IN_PLANUNG: 'In Planung',
  IN_AUSFUEHRUNG: 'In Ausführung',
  TECHNISCH_ABGESCHLOSSEN: 'Technisch abgeschlossen',
  KAUFMAENNISCH_GEPRUEFT: 'Kaufmännisch geprüft',
  ABGERECHNET: 'Abgerechnet',
  STORNIERT: 'Storniert',
};

export function workOrderStatusLabel(s: WorkOrderStatus): string {
  return WORK_ORDER_STATUS_LABELS[s] ?? s;
}

export function workOrderStatusClass(s: WorkOrderStatus): string {
  if (s === 'ABGERECHNET' || s === 'KAUFMAENNISCH_GEPRUEFT') return 'stamp--positive';
  if (s === 'STORNIERT') return 'stamp--warn';
  return '';
}

/** Auftraggeber eines Auftrags + wie viele Aufträge/Termine dieser Kunde hat. */
export interface Kundenhistorie {
  customer_party_id: string | null;
  customer_name: string | null;
  auftraege_gesamt: number;
  termine_gesamt: number;
}
