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
  /**
   * Auftragsbezug: die Aussage „dieses Angebot ist das **Soll** dieser Baustelle".
   * Der Soll-Ist-Abgleich am Baustellenbericht stützt sich ausschließlich darauf.
   * null = keinem Auftrag zugeordnet.
   *
   * **In jedem Status änderbar** — auch nach dem Versand (Migration 0082). Der reale
   * Ablauf ist „Angebot versenden → Kunde nimmt an → *dann* Auftrag anlegen"; wäre die
   * Zuordnung ab Versand gesperrt (B-30), wäre sie genau dann unmöglich, wenn man sie
   * braucht. Sie ist ein interner Verweis, kein Beleginhalt.
   *
   * Sperre in der Gegenrichtung: Stützt sich bereits eine Berichtsposition auf eine
   * Position dieses Angebots, lässt sich die Zuordnung **nicht mehr lösen oder
   * umhängen** (422) — sonst fiele das Soll unter dem Nachweis weg.
   */
  work_order_id: string | null;
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
  /**
   * Nach § 35a EStG begünstigter Arbeitskostenanteil (netto) dieser Position.
   *
   * **null = unbestimmt, NICHT 0,00.** Der Server leitet ihn ab, wo die
   * Positionsart eindeutig ist (ARBEITSZEIT/FAHRT voll, MATERIAL 0,00); bei
   * PAUSCHALE/FREMDLEISTUNG/ZUSCHLAG bleibt er offen, bis ihn jemand setzt.
   * Solange auch nur eine Position offen ist, weist die Rechnung nichts aus.
   */
  labour_net_amount: string | null;
  /** Interner Kalkulations-Snapshot (steht nicht auf dem Kundenbeleg). */
  unit_cost: string | null;
  markup_percent: string | null;
  source_article_id: string | null;
  source_assembly_id: string | null;
  /**
   * Anrechnungsposition einer Schlussrechnung (negativer Betrag): sie rechnet die
   * genannte Abschlags-/Teilrechnung an. **Read-only** — der Editor darf sie nicht
   * ändern und schickt sie nicht zurück; der Server erzeugt sie aus der
   * Verkettung. Bei allen anderen Positionen null.
   */
  advance_invoice_id?: string | null;
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

export interface QuoteWorkOrderRef {
  id: string;
  order_number: string;
  title: string;
}

export interface QuoteDetail extends Quote {
  valid_until_date: string | null;
  tax_total: string | null;
  version: number;
  project: QuoteProjectRef | null;
  /** Aufgelöster Auftragsbezug (null = keinem Auftrag zugeordnet). */
  work_order: QuoteWorkOrderRef | null;
  sent_at: string | null;
  has_snapshot: boolean;
  content_hash: string | null;
  /** Vorbelegung für den E-Mail-Versand: primäre EMAIL der best-effort über den
   *  Auftrag abgeleiteten Empfängerpartei (nur bei versendeten Angeboten, sonst
   *  null — dann trägt der Nutzer die Adresse im Dialog manuell ein). */
  recipient_email: string | null;
  rubriken: Rubrik[];
  lines: QuoteLine[];
}

/** Antwort des Angebotsversands per E-Mail. */
export interface QuoteEmailResult {
  sent: boolean;
  to_address: string;
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

/**
 * Der § 35a-Ausweis einer Rechnung — **vom Server gerechnet**, nie im Client.
 *
 * `bestimmbar: false` heißt: mindestens eine Position hat ihren Arbeitskosten-
 * anteil nicht bestimmt (`offen` nennt die Positionsnummern). Die Beträge sind
 * dann null = unbekannt (nicht 0), und der Beleg weist nichts aus.
 */
export interface Arbeitskosten {
  bestimmbar: boolean;
  /**
   * Warum kein Ausweis zustande kommt:
   * - `OFFENE_POSITIONEN` — `offen` nennt die Positionsnummern ohne Anteil.
   * - `UNSTIMMIG` — das Ergebnis ist kein Teil des Rechnungsbetrags (negativ oder
   *   größer). Entsteht nur bei einer Schlussrechnung mit fehlerhaft erfasstem
   *   Abschlag; im Beleg selbst ist dann nichts zu reparieren.
   */
  grund: 'OFFENE_POSITIONEN' | 'UNSTIMMIG' | null;
  offen: number[];
  net_amount: string | null;
  tax_amount: string | null;
  gross_amount: string | null;
}

export interface InvoiceDetail extends Invoice {
  due_date: string | null;
  tax_total: string | null;
  /** Arbeitskosten nach § 35a EStG auf dem Beleg ausweisen (Default: ja). */
  show_labour_costs: boolean;
  /** Der berechnete Ausweis (immer gesetzt; siehe `bestimmbar`). */
  arbeitskosten: Arbeitskosten | null;
  /** Zahlungsziel in Tagen ab Belegdatum (leitet due_date beim Veröffentlichen ab). */
  payment_term_days: number | null;
  /** Skontosatz in Prozent — Decimal als String. Nur zusammen mit discount_days. */
  discount_percent: string | null;
  /** Skontofrist in Tagen ab Belegdatum. Nur zusammen mit discount_percent. */
  discount_days: number | null;
  /** Abgeleitet (read-only, Server rechnet): Ende der Skontofrist. */
  skonto_bis: string | null;
  /** Abgeleitet: Skontobetrag (Decimal als String). */
  skonto_betrag: string | null;
  /** Abgeleitet: Bruttobetrag abzüglich Skonto (Decimal als String). */
  skonto_zahlbetrag: string | null;
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
  /** Schlussrechnung → die angerechneten Abschlags-/Teilrechnungen. */
  advances: InvoiceAdvance[];
  /** Abschlags-/Teilrechnung → die Schlussrechnung, die sie anrechnet. */
  angerechnet_in: FinalInvoiceRef | null;
  /** Nur bei Anrechnung: Leistung VOR Abzug. Der Zahlbetrag ist `gross_total`. */
  leistung_netto: string | null;
  leistung_steuer: string | null;
  leistung_brutto: string | null;
}

// --- Abschlags-/Teil-/Schlussrechnung --------------------------------------
// Die Schlussrechnung rechnet die bereits gestellten Abschläge desselben Auftrags
// an: je Abschlag und Steuersatz eine negative Position. `gross_total` der SR ist
// damit der ZAHLBETRAG (Differenz), nicht die Gesamtleistung.

/** Eine angerechnete Abschlags-/Teilrechnung (Beträge positiv = was abgezogen wird). */
export interface InvoiceAdvance {
  advance_invoice_id: string;
  invoice_number: string | null;
  invoice_type: InvoiceType;
  invoice_date: string | null;
  net_amount: string;
  tax_amount: string;
  gross_amount: string;
  steuergruppen: AdvanceSteuergruppe[];
}

export interface AdvanceSteuergruppe {
  tax_code: string;
  tax_rate_percent: string;
  net_amount: string;
  tax_amount: string;
  gross_amount: string;
}

/** Gegenrichtung der Kette: die Schlussrechnung, die diesen Abschlag anrechnet. */
export interface FinalInvoiceRef {
  id: string;
  invoice_number: string | null;
  invoice_date: string | null;
  status: InvoiceStatus;
}

/** Ein anrechenbarer Abschlag zur Auswahl in der Schlussrechnung. */
export interface AnrechenbarerAbschlag {
  id: string;
  invoice_number: string | null;
  invoice_type: InvoiceType;
  invoice_date: string | null;
  net_total: string | null;
  tax_total: string | null;
  gross_total: string | null;
  /** In einem ANDEREN Schlussrechnungs-Entwurf vorgemerkt (bindet nicht). */
  vorgemerkt: boolean;
  /** Von DIESER Schlussrechnung bereits angerechnet. */
  angerechnet: boolean;
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
  /**
   * § 35a-Arbeitskostenanteil (Decimal als Punkt-String). **Weglassen = vom
   * Server ableiten lassen**; ein gesetzter Wert gewinnt immer und muss ein Teil
   * des Positionsbetrags sein (sonst 422).
   */
  labour_net_amount?: string | null;
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
  /** Auftragsbezug (= Soll dieser Baustelle). Der Auftrag muss zur selben
   *  Liegenschaft/zum selben Projekt gehören, sonst 422. */
  work_order_id?: string | null;
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
  /** Auftragsbezug setzen (oder mit `null` lösen). Weggelassen = unverändert.
   *  In jedem Status möglich (Migration 0082) — der eingefrorene Beleginhalt (B-30)
   *  umfasst die Zuordnung nicht. Ausnahme: Sie ist gesperrt, sobald ein
   *  Baustellenbericht eine Position dieses Angebots als Soll führt (422). */
  work_order_id?: string | null;
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
  payment_term_days?: number | null;
  /** Decimal als Punkt-String (deZuApiDezimal), nie number. */
  discount_percent?: string | null;
  discount_days?: number | null;
  /** § 35a-Ausweis (Default: true — der Privatkunde ist der Regelfall). */
  show_labour_costs?: boolean;
  rubriken?: RubrikInput[];
  lines: QuoteLineInput[];
  /**
   * Nur SCHLUSSRECHNUNG: die anzurechnenden Abschlags-/Teilrechnungen. Die
   * negativen Anrechnungspositionen je Steuersatz erzeugt der Server — sie
   * gehören NICHT in `lines`.
   */
  advance_invoice_ids?: string[];
}

/** Rechnungsentwurf ändern (Positionen/Abschnitte vollständig ersetzt; kein Titel). */
export interface InvoiceUpdate {
  invoice_date?: string | null;
  due_date?: string | null;
  payment_term_days?: number | null;
  /** Decimal als Punkt-String (deZuApiDezimal), nie number. */
  discount_percent?: string | null;
  discount_days?: number | null;
  /** § 35a-Ausweis ein/aus. Weggelassen = unverändert. */
  show_labour_costs?: boolean;
  rubriken?: RubrikInput[];
  lines?: QuoteLineInput[];
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
