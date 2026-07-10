// Vertrag zu /api/accounting (Belegerfassung: Eingangsbelege, Buchungskonten,
// Kostenstellen — Fachschema `accounting`). Beträge UND Mengen kommen als String
// (Decimal) — verlustfrei behalten, nur zur Anzeige mit Number() formatieren.
// Die Belegnummer (EB-00001) vergibt die DB; die Summen rechnet der Server
// verbindlich (der Editor nimmt keine Summe vorweg).

export type ReceiptStatus =
  | 'ERFASST'
  | 'GEPRUEFT'
  | 'FREIGEGEBEN'
  | 'GEBUCHT'
  | 'ABGELEHNT';

export type AccountType = 'AKTIV' | 'PASSIV' | 'AUFWAND' | 'ERTRAG';
export type ChartOfAccounts = 'SKR03' | 'SKR04';

// --- Stammdaten: Buchungskonten --------------------------------------------

export interface LedgerAccount {
  id: string;
  account_number: string;
  label: string;
  account_type: AccountType;
  chart_of_accounts: ChartOfAccounts | null;
  active: boolean;
  notes: string | null;
}

export interface LedgerAccountInput {
  account_number: string;
  label: string;
  account_type: AccountType;
  chart_of_accounts?: string | null;
  notes?: string | null;
}

export interface LedgerAccountPatch {
  account_number?: string;
  label?: string;
  account_type?: AccountType;
  chart_of_accounts?: string | null;
  active?: boolean;
  notes?: string | null;
}

// --- Stammdaten: Kostenstellen ---------------------------------------------

export interface CostCenter {
  id: string;
  code: string;
  label: string;
  active: boolean;
  notes: string | null;
}

export interface CostCenterInput {
  code: string;
  label: string;
  notes?: string | null;
}

export interface CostCenterPatch {
  code?: string;
  label?: string;
  active?: boolean;
  notes?: string | null;
}

// --- Eingangsbeleg ----------------------------------------------------------

export interface ReceiptLineInput {
  description: string;
  quantity: string;
  unit_price: string;
  tax_code: string;
  unit?: string | null;
  ledger_account_id?: string | null;
  cost_center_id?: string | null;
}

export interface ReceiptLine {
  id: string;
  position_number: number;
  description: string;
  quantity: string;
  unit: string | null;
  unit_price: string;
  tax_code: string;
  tax_rate_percent: string;
  net_amount: string;
  ledger_account_id: string | null;
  ledger_account_label: string | null;
  cost_center_id: string | null;
  cost_center_label: string | null;
}

export interface ReceiptRow {
  id: string;
  receipt_number: string;
  supplier: string | null;
  supplier_invoice_number: string | null;
  receipt_date: string;
  due_date: string | null;
  currency: string;
  net_total: string;
  tax_total: string;
  gross_total: string;
  status: ReceiptStatus;
}

export interface ReceiptPage {
  items: ReceiptRow[];
  total: number;
  page: number;
  page_size: number;
}

export interface StatusEvent {
  from_status: string | null;
  to_status: string;
  reason: string | null;
  changed_by: string | null;
  occurred_at: string;
}

export interface ReceiptDetail extends ReceiptRow {
  supplier_party_id: string;
  received_date: string;
  rejection_reason: string | null;
  notes: string | null;
  lines: ReceiptLine[];
  history: StatusEvent[];
}

export interface ReceiptCreate {
  supplier_party_id: string;
  receipt_date: string;
  lines: ReceiptLineInput[];
  received_date?: string | null;
  due_date?: string | null;
  supplier_invoice_number?: string | null;
  currency?: string;
  notes?: string | null;
}

export interface ReceiptUpdate {
  supplier_party_id?: string | null;
  receipt_date?: string | null;
  received_date?: string | null;
  currency?: string | null;
  lines?: ReceiptLineInput[];
  due_date?: string | null;
  supplier_invoice_number?: string | null;
  notes?: string | null;
}

export interface StatusInput {
  to_status: ReceiptStatus;
  reason?: string | null;
}

export interface ReceiptQuery {
  page: number;
  page_size: number;
  q?: string;
  status?: ReceiptStatus | null;
}

// --- Darstellung ------------------------------------------------------------

const EUR = new Intl.NumberFormat('de-DE', { style: 'currency', currency: 'EUR' });

/** Formatiert einen Decimal-String als EUR-Betrag (nur zur Anzeige). */
export function euro(value: string | null): string {
  if (value === null || value === '') return '—';
  return EUR.format(Number(value));
}

const MENGE = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 3 });

/** Menge mit optionaler Einheit (nur zur Anzeige). */
export function menge(qty: string | null, unit: string | null): string {
  if (qty === null || qty === '') return '—';
  const n = MENGE.format(Number(qty));
  return unit ? `${n} ${unit}` : n;
}

const STATUS_LABELS: Record<ReceiptStatus, string> = {
  ERFASST: 'Erfasst',
  GEPRUEFT: 'Geprüft',
  FREIGEGEBEN: 'Freigegeben',
  GEBUCHT: 'Gebucht',
  ABGELEHNT: 'Abgelehnt',
};

/** Deutsches Statuslabel (auch für Verlaufseinträge als String tolerant). */
export function receiptStatusLabel(s: string | null): string {
  if (s === null) return 'Anlage';
  return STATUS_LABELS[s as ReceiptStatus] ?? s;
}

// Stempel-Klassen laut Vorgabe: ERFASST neutral, GEPRUEFT type, FREIGEGEBEN
// warn (eingefroren, aber noch nicht gebucht), GEBUCHT positive, ABGELEHNT
// negativ. Status wird immer zusätzlich als Text gezeigt (WCAG: nicht nur Farbe).
const STATUS_CLASSES: Record<ReceiptStatus, string> = {
  ERFASST: '',
  GEPRUEFT: 'stamp--type',
  FREIGEGEBEN: 'stamp--warn',
  GEBUCHT: 'stamp--positive',
  ABGELEHNT: 'stamp--negativ',
};

export function receiptStatusClass(s: string): string {
  return STATUS_CLASSES[s as ReceiptStatus] ?? '';
}

const ACCOUNT_TYPE_LABELS: Record<AccountType, string> = {
  AKTIV: 'Aktivkonto',
  PASSIV: 'Passivkonto',
  AUFWAND: 'Aufwandskonto',
  ERTRAG: 'Ertragskonto',
};

export function accountTypeLabel(t: string): string {
  return ACCOUNT_TYPE_LABELS[t as AccountType] ?? t;
}
