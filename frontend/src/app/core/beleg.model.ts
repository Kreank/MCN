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

/**
 * Positionsart. `line_type` sagt, WAS die Position ist; `LineKind`, OB sie in die
 * Summe zählt. Alternativ- und Bedarfspositionen tragen einen Betrag, gehen aber
 * nicht in die Gesamtsumme ein (Anzeige in Klammern).
 */
export type LineKind = 'NORMAL' | 'ALTERNATIV' | 'BEDARF';

export interface QuoteLine {
  position_number: number;
  line_type: LineType;
  line_kind: LineKind;
  /** 1-basierte Abschnittsnummer, null = keinem Abschnitt zugeordnet. */
  rubrik: number | null;
  description: string;
  quantity: string | null;
  unit: string | null;
  unit_price: string | null;
  discount_percent: string | null;
  tax_code: string | null;
  tax_rate_percent: string | null;
  net_amount: string | null;
  /** Interner Kalkulations-Snapshot (steht nicht auf dem Kundenbeleg). */
  unit_cost: string | null;
  markup_percent: string | null;
  source_article_id: string | null;
  source_assembly_id: string | null;
}

/** Abschnitt (Rubrik) eines Belegs — gliedert die Positionen. */
export interface Rubrik {
  position_number: number;
  title: string;
  description: string | null;
}

/**
 * Kalkulation eines Abschnitts. `rubrik: null` ist die Sammelgruppe
 * „Ohne Abschnitt".
 *
 * `ek_vollstaendig: false` heißt: mindestens einer Position fehlt der
 * Einkaufspreis. Dann sind `deckungsbeitrag` und `marge_prozent` null — die Zahl
 * ist nicht bekannt, nicht null. Nie als 0 darstellen.
 */
export interface KalkAbschnitt {
  rubrik: number | null;
  title: string;
  description: string | null;
  netto: string;
  ek: string;
  deckungsbeitrag: string | null;
  marge_prozent: string | null;
  ek_vollstaendig: boolean;
  positionen: number;
  positionen_ohne_ek: number;
  alternativ_netto: string;
  bedarf_netto: string;
  arbeitszeit: string;
}

export interface Kalkulation {
  abschnitte: KalkAbschnitt[];
  gesamt: KalkAbschnitt;
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
  sent_at: string | null;
  has_snapshot: boolean;
  content_hash: string | null;
  rubriken: Rubrik[];
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

export interface InvoiceParty {
  party_id: string;
  display_name: string;
  role: string;
  is_primary: boolean;
  allocation_percent: string | null;
}

export interface InvoiceDetail extends Invoice {
  due_date: string | null;
  tax_total: string | null;
  version: number;
  project: QuoteProjectRef | null;
  work_order_number: string | null;
  published_at: string | null;
  has_snapshot: boolean;
  content_hash: string | null;
  /** Vorbelegung für den E-Mail-Versand: primäre EMAIL der Empfängerpartei
   *  (nur bei veröffentlichten Rechnungen aufgelöst, sonst null). */
  recipient_email: string | null;
  parties: InvoiceParty[];
  rubriken: Rubrik[];
  lines: QuoteLine[];
}

/** Antwort des Rechnungsversands per E-Mail. */
export interface InvoiceEmailResult {
  sent: boolean;
  to_address: string;
}

// --- Schreib-Verträge (POST-Payloads) --------------------------------------
// Alle Decimal-Felder sind Punkt-Strings (deZuApiDezimal), niemals number.

export interface QuoteLineInput {
  line_type: LineType;
  description: string;
  line_kind?: LineKind;
  /** 1-basierte Abschnittsnummer, passend zu `rubriken` im selben Payload. */
  rubrik?: number | null;
  quantity?: string | null;
  unit?: string | null;
  unit_price?: string | null;
  discount_percent?: string | null;
  tax_code?: string | null;
  unit_cost?: string | null;
  markup_percent?: string | null;
  sale_price_group_id?: string | null;
  source_article_id?: string | null;
  source_assembly_id?: string | null;
}

export interface RubrikInput {
  title: string;
  description?: string | null;
}

export interface QuoteCreate {
  property_id: string;
  title: string;
  project_id?: string | null;
  quote_date?: string | null;
  valid_until_date?: string | null;
  rubriken?: RubrikInput[];
  lines: QuoteLineInput[];
}

/**
 * Änderungs-Payload des Angebotseditors. Positionen und Abschnitte werden vom
 * Server VOLLSTÄNDIG ersetzt — immer den ganzen Beleg schicken (`rubriken` +
 * `lines`). Kopffelder sind optional; weggelassen = unverändert. 422, wenn das
 * Angebot bereits versendet (eingefroren) ist.
 */
export interface QuoteUpdate {
  title?: string | null;
  quote_date?: string | null;
  valid_until_date?: string | null;
  rubriken?: RubrikInput[];
  lines?: QuoteLineInput[];
}

export interface InvoiceCreate {
  property_id: string;
  invoice_type?: InvoiceType;
  project_id?: string | null;
  work_order_id?: string | null;
  reference_invoice_id?: string | null;
  invoice_date?: string | null;
  due_date?: string | null;
  rubriken?: RubrikInput[];
  lines: QuoteLineInput[];
}

export interface InvoicePartyCreate {
  party_id: string;
  role: string;
  is_primary?: boolean;
  allocation_percent?: string | null;
  liability_group?: string | null;
  liability_basis?: string | null;
}

/** Kurzreferenz auf einen Folgebeleg (Storno/Gutschrift), CreditRefOut. */
export interface CreditRef {
  id: string;
  invoice_number: string | null;
  invoice_type: InvoiceType;
  gross_total: string | null;
}
