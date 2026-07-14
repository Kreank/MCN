// Vertrag zu /api/buchhaltung (offene Posten, Zahlungen, Mahnwesen).
// Beträge kommen als String (Decimal) — verlustfrei behalten, nur zur Anzeige
// mit Number() formatieren.

/** AUSGEGLICHEN: nichts mehr zu fordern und nichts zu erstatten — die Rechnung ist
 *  durch Storno/Gutschrift verrechnet, es floss kein Geld. NICHT „bezahlt" (niemand
 *  hat gezahlt) und nicht „offen". Auf einem KREDITBELEG heißt es: vollständig mit
 *  der offenen Forderung verrechnet, es ist nichts zu erstatten. */
export type PaymentStatus =
  | 'OFFEN'
  | 'TEILZAHLUNG'
  | 'BEZAHLT'
  | 'UEBERZAHLT'
  | 'AUSGEGLICHEN';

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
  /** Summe der veröffentlichten Storno-/Gutschriftbelege zu dieser Rechnung (≤ 0). */
  credit_total: string;
  /**
   * Was zwischen diesem Beleg und seinem Gegenbeleg VERRECHNET ist (≥ 0).
   *
   * Auf der Rechnung: der Teil der Kreditbelege, der die noch offene Forderung
   * aufzehrt. Auf dem Kreditbeleg: sein Anteil daran — nur der REST davon ist dem
   * Kunden zu erstatten. Die Erstattungspflicht steht damit auf genau EINEM Beleg
   * (dem Kreditbeleg); die Rechnung wird durch einen Kreditbeleg nie negativ.
   */
  verrechnet: string;
  /** Brutto abzüglich des Verrechneten. Es gilt: open_amount = forderungsbetrag − paid_total. */
  forderungsbetrag: string;
  /** Forderungsbetrag minus Gezahltes. Negativ = Guthaben des Kunden. */
  open_amount: string;
  /** Noch an den Kunden zurückzuzahlen (0, wenn nichts offen ist). */
  zu_erstatten: string;
  /** Bereits an den Kunden zurückgezahlt. */
  erstattet: string;
  payment_status: PaymentStatus;
  is_overdue: boolean;
  /** Durch einen veröffentlichten STORNO aufgehoben — fordert nichts mehr. */
  is_storniert: boolean;
  /** Fordert dieser Beleg (noch) Geld? Kreditbelege und Stornierte: nein. */
  ist_forderung: boolean;
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
  id: string;
  payment_type: string;
  amount: string;
  currency: string;
  paid_at: string;
  import_source: string;
  /** Selbst eine Stornobuchung (payment_type STORNO_BUCHUNG). */
  is_reversal: boolean;
  /** Bereits durch eine Gegenbuchung storniert. */
  is_reversed: boolean;
  /** Eingehende Zahlung, noch nicht storniert → Storno möglich. */
  is_reversible: boolean;
}

export interface DunningNotice {
  /** Id der Mahnung — adressiert den Versand-Endpunkt (send-email). */
  id: string;
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

export interface CreditRef {
  id: string;
  invoice_number: string | null;
  invoice_type: string;
  gross_total: string | null;
}

export interface OpenItemDetail extends OpenItem {
  currency: string;
  net_total: string | null;
  tax_total: string | null;
  reference: InvoiceRef;
  origin: CreditRef | null;
  credit_notes: CreditRef[];
  payments: Payment[];
  dunning: DunningNotice[];
  /** Best-effort vorbelegte Schuldner-E-Mail für den Mahnungsversand-Dialog. */
  recipient_email: string | null;
  /** Zahlungsbedingungen der Rechnung (read-only). Sie ändern weder den
   *  Zahlungsstatus noch den offenen Betrag — maßgeblich bleibt, was tatsächlich
   *  gezahlt wurde. Decimals sind Strings. */
  discount_percent: string | null;
  discount_days: number | null;
  payment_term_days: number | null;
  skonto_bis: string | null;
  skonto_betrag: string | null;
  skonto_zahlbetrag: string | null;
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
  /** Verzugstage aus dem Zahlungsspiegel — null, sobald nichts mehr offen ist. */
  days_overdue: number | null;
  /** Storniert: die Mahnhistorie bleibt sichtbar, aber es geht keine Stufe weiter. */
  is_storniert: boolean;
  /** Lässt sich (weiter) mahnen? Nur eine offene Forderung. */
  mahnbar: boolean;
  /** OFFEN | TEILZAHLUNG | BEZAHLT | UEBERZAHLT | AUSGEGLICHEN — der wahre Zustand. */
  payment_status: string;
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

// --- Schreib-Verträge (POST-Payloads) --------------------------------------
// amount/Beträge sind Punkt-Strings (Decimal), niemals number.

export interface PaymentRecord {
  amount: string;
  paid_at: string;
  payment_type?: string;
  external_reference?: string | null;
  currency?: string;
}

/** Antwort auf Zahlungserfassung/-storno (PaymentDetailOut). */
export interface PaymentDetail extends Payment {
  id: string;
  invoice_id: string;
  external_reference: string;
}

export interface DunningIssue {
  level: number;
  issued_at: string;
  note?: string | null;
}

/** Antwort des Mahnungsversands per E-Mail. */
export interface DunningEmailResult {
  sent: boolean;
  to_address: string;
}

/** Rechnungskorrektur über ausgewählte Positionsnummern. */
export interface CorrectionInput {
  positions: number[];
}

/**
 * Antwort (HTTP 202), wenn Storno/Rechnungskorrektur erst einen genehmigten
 * Vier-Augen-Antrag braucht (action_code RECHNUNGSKORREKTUR). Der Server hat
 * einen Freigabeantrag angelegt (oder einen bestehenden wiederverwendet); es
 * wurde NOCH NICHTS storniert/gutgeschrieben. Vertrag: PendingApprovalOut.
 */
export interface PendingApproval {
  /** Id des angelegten/wiederverwendeten Freigabeantrags. */
  pending_approval: string;
  action_code: string;
  detail: string;
}

/**
 * Ergebnis von Storno bzw. Rechnungskorrektur. Der Endpunkt antwortet ENTWEDER
 * mit 201 (Folgebeleg erzeugt — eine passende Genehmigung lag vor und wurde in
 * derselben Transaktion verbraucht) ODER mit 202 (Vier-Augen-Antrag angelegt,
 * wartet auf eine zweite Person). Das UI MUSS beide Fälle unterscheiden: bei
 * `wartet` darf es NICHT so tun, als sei bereits storniert/gutgeschrieben.
 */
export type CreditOutcome =
  | { kind: 'erzeugt'; credit: CreditRef }
  | { kind: 'wartet'; pending: PendingApproval };

/** Erlaubte Zahlungsarten der manuellen Erfassung (positiv wirkend).
 *  STORNO_BUCHUNG entsteht nur systemseitig über den Storno-Endpunkt. */
export const PAYMENT_TYPES: { wert: string; label: string }[] = [
  { wert: 'ZAHLUNG', label: 'Zahlung' },
  { wert: 'TEILZAHLUNG', label: 'Teilzahlung' },
  { wert: 'UEBERZAHLUNG', label: 'Überzahlung' },
  { wert: 'RUECKERSTATTUNG', label: 'Rückerstattung' },
];

// --- Darstellung -----------------------------------------------------------
const PAYMENT_STATUS_LABELS: Record<PaymentStatus, string> = {
  OFFEN: 'Offen',
  TEILZAHLUNG: 'Teilzahlung',
  BEZAHLT: 'Bezahlt',
  UEBERZAHLT: 'Überzahlt',
  AUSGEGLICHEN: 'Ausgeglichen',
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

// --- Mahnlauf (semi-automatischer Stapel) ----------------------------------

/** Ein Rechnungs­kandidat für die nächste Mahnstufe (aus der Vorschau). */
export interface MahnlaufCandidate {
  invoice_id: string;
  invoice_number: string | null;
  debtor: string | null;
  due_date: string | null;
  open_amount: string;
  current_level: number;
  next_level: number;
  next_level_label: string;
  days_overdue: number;
  recipient_email: string | null;
}

export interface MahnlaufPreview {
  stichtag: string;
  candidates: MahnlaufCandidate[];
}

export interface MahnlaufItem {
  invoice_id: string;
  level: number;
}

export interface MahnlaufInput {
  items: MahnlaufItem[];
  send_email: boolean;
  stichtag?: string | null;
}

export interface MahnlaufResultRow {
  invoice_id: string;
  status: 'issued' | 'sent' | 'skipped' | 'failed';
  level: number | null;
  notice_id: string | null;
  detail: string | null;
}

export interface MahnlaufResult {
  issued: number;
  sent: number;
  skipped: number;
  failed: number;
  results: MahnlaufResultRow[];
}
