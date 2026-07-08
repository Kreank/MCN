// Vertrag zu /api/invoicing/quotes (invoicing.quote in der DB).
// Beträge kommen als String (Decimal) — zur verlustfreien Anzeige.
export type QuoteStatus =
  | 'ENTWURF'
  | 'INTERN_GEPRUEFT'
  | 'FREIGEGEBEN'
  | 'VERSENDET'
  | 'ANGENOMMEN'
  | 'ABGELEHNT'
  | 'ABGELAUFEN'
  | 'ERSETZT';

export type LineType =
  | 'MATERIAL'
  | 'ARBEITSZEIT'
  | 'PAUSCHALE'
  | 'FREMDLEISTUNG'
  | 'FAHRT'
  | 'ZUSCHLAG'
  | 'TEXT'
  | 'ZWISCHENSUMME';

export interface QuotePropertyRef {
  id: string;
  property_number: string;
  name: string;
  city: string;
}

export interface Quote {
  id: string;
  quote_number: string | null;
  title: string;
  status: QuoteStatus;
  currency: string;
  quote_date: string | null;
  net_total: string | null;
  gross_total: string | null;
  property: QuotePropertyRef;
}

export interface QuotePage {
  items: Quote[];
  total: number;
  page: number;
  page_size: number;
}

export interface QuoteQuery {
  page: number;
  page_size: number;
  q?: string;
  status?: QuoteStatus | null;
  property_id?: string | null;
  project_id?: string | null;
}

export interface QuoteLine {
  position_number: number;
  line_type: LineType;
  description: string;
  quantity: string | null;
  unit: string | null;
  unit_price: string | null;
  discount_percent: string | null;
  tax_code: string | null;
  tax_rate_percent: string | null;
  net_amount: string | null;
}

export interface QuoteProjectRef {
  id: string;
  project_number: string;
  name: string;
}

export interface QuoteDetail extends Quote {
  valid_until_date: string | null;
  tax_total: string | null;
  version: number;
  project: QuoteProjectRef | null;
  lines: QuoteLine[];
}

// --- Rechnungen (invoicing.invoice) ----------------------------------------
export type InvoiceType =
  | 'RECHNUNG'
  | 'ABSCHLAGSRECHNUNG'
  | 'TEILRECHNUNG'
  | 'SCHLUSSRECHNUNG'
  | 'GUTSCHRIFT'
  | 'STORNO';
export type InvoiceStatus = 'ENTWURF' | 'VEROEFFENTLICHT';

export interface Invoice {
  id: string;
  invoice_number: string | null;
  invoice_type: InvoiceType;
  status: InvoiceStatus;
  currency: string;
  invoice_date: string | null;
  net_total: string | null;
  gross_total: string | null;
  property: QuotePropertyRef;
}

export interface InvoicePage {
  items: Invoice[];
  total: number;
  page: number;
  page_size: number;
}

export interface InvoiceQuery {
  page: number;
  page_size: number;
  q?: string;
  status?: InvoiceStatus | null;
  invoice_type?: InvoiceType | null;
  property_id?: string | null;
  project_id?: string | null;
}

export interface InvoiceDetail extends Invoice {
  due_date: string | null;
  tax_total: string | null;
  version: number;
  project: QuoteProjectRef | null;
  lines: QuoteLine[];
}
