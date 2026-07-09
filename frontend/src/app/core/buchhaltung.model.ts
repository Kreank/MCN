// Vertrag zu /api/buchhaltung (offene Posten, Zahlungen, Mahnwesen).
// Beträge kommen als String (Decimal) — verlustfrei behalten, nur zur Anzeige
// mit Number() formatieren.

export type PaymentStatus = 'OFFEN' | 'TEILZAHLUNG' | 'BEZAHLT' | 'UEBERZAHLT';

export interface OpenItem {
  id: string;
  invoice_number: string | null;
  invoice_type: string;
  status: string;
  debtor: string | null;
  invoice_date: string | null;
  due_date: string | null;
  gross_total: string | null;
  paid_total: string;
  open_amount: string;
  payment_status: PaymentStatus;
  is_overdue: boolean;
  dunning_level: number | null;
}

export interface OpenItemPage {
  items: OpenItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface OpenItemQuery {
  page: number;
  page_size: number;
  q?: string;
  payment_status?: PaymentStatus | null;
  overdue?: boolean | null;
  invoice_type?: string | null;
}

export interface Payment {
  payment_type: string;
  amount: string;
  currency: string;
  paid_at: string;
  import_source: string;
}

export interface DunningNotice {
  level: number;
  label: string;
  issued_at: string;
  note: string | null;
  created_by: string | null;
}

export interface InvoiceRef {
  id: string;
  property_name: string;
  project_name: string | null;
  work_order_number: string | null;
}

export interface OpenItemDetail extends OpenItem {
  currency: string;
  net_total: string | null;
  tax_total: string | null;
  reference: InvoiceRef;
  payments: Payment[];
  dunning: DunningNotice[];
}

export interface DunningRow {
  id: string;
  invoice_number: string | null;
  debtor: string | null;
  due_date: string | null;
  gross_total: string | null;
  open_amount: string;
  dunning_level: number | null;
  last_issued_at: string | null;
  days_overdue: number | null;
}

export interface DunningLevelInfo {
  level: number;
  label: string;
  days_after_due: number;
}

export interface DunningList {
  items: DunningRow[];
  levels: DunningLevelInfo[];
}

// --- Darstellung -----------------------------------------------------------
const PAYMENT_STATUS_LABELS: Record<PaymentStatus, string> = {
  OFFEN: 'Offen',
  TEILZAHLUNG: 'Teilzahlung',
  BEZAHLT: 'Bezahlt',
  UEBERZAHLT: 'Überzahlt',
};

export function paymentStatusLabel(s: PaymentStatus): string {
  return PAYMENT_STATUS_LABELS[s] ?? s;
}

export function paymentStatusClass(s: PaymentStatus): string {
  if (s === 'BEZAHLT') return 'stamp--positive';
  if (s === 'TEILZAHLUNG' || s === 'UEBERZAHLT') return 'stamp--warn';
  return '';
}

const PAYMENT_TYPE_LABELS: Record<string, string> = {
  ZAHLUNG: 'Zahlung',
  TEILZAHLUNG: 'Teilzahlung',
  UEBERZAHLUNG: 'Überzahlung',
  RUECKERSTATTUNG: 'Rückerstattung',
  STORNO_BUCHUNG: 'Storno-Buchung',
};

export function paymentTypeLabel(t: string): string {
  return PAYMENT_TYPE_LABELS[t] ?? t;
}

const INVOICE_TYPE_LABELS: Record<string, string> = {
  RECHNUNG: 'Rechnung',
  ABSCHLAGSRECHNUNG: 'Abschlagsrechnung',
  TEILRECHNUNG: 'Teilrechnung',
  SCHLUSSRECHNUNG: 'Schlussrechnung',
  GUTSCHRIFT: 'Gutschrift',
  STORNO: 'Storno',
};

export function invoiceTypeLabel(t: string): string {
  return INVOICE_TYPE_LABELS[t] ?? t;
}

const EUR = new Intl.NumberFormat('de-DE', { style: 'currency', currency: 'EUR' });

/** Formatiert einen Decimal-String als EUR-Betrag (nur zur Anzeige). */
export function euro(value: string | null): string {
  if (value === null || value === '') return '—';
  return EUR.format(Number(value));
}
