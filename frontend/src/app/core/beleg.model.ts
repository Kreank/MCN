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
   * Vorgangsbezug: der Vorgang, an dem dieser Beleg hängt — direkt oder über einen
   * Auftrag des Vorgangs. So lassen sich Belege an der Vorgangsmappe bündeln.
   * null = keinem Vorgang zugeordnet.
   */
  service_case_id: string | null;
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
  /** Belege des Vorgangs (direkt am Vorgang oder an einem seiner Aufträge). */
  service_case_id?: string | null;
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
  /**
   * Herkunft der **Abrechnungsbindung** (Migration 0084) — nur bei Rechnungen:
   * `BERICHTSPOSITION` | `ZEITBUCHUNG` | `ANGEBOTSPOSITION`, sonst null.
   *
   * Eine gebundene Zeile stammt aus dem Abrechnungslauf und ist der Nachweis,
   * dass genau diese Leistung fakturiert wurde. Der DB-Trigger
   * `invoicing.protect_billed_invoice_lines` sperrt seit Migration 0088 genau
   * **diese Zeile** (UPDATE und DELETE) — nicht den ganzen Beleg. Das INSERT einer
   * neuen, ungebundenen Zeile bleibt erlaubt (siehe `InvoiceDetail.gebunden`).
   */
  billing_source?: BillingSource | null;
}

/** Quellart einer Abrechnungsbindung. */
export type BillingSource = 'BERICHTSPOSITION' | 'ZEITBUCHUNG' | 'ANGEBOTSPOSITION';

// ---------------------------------------------------------------------------
// Die Mengensicht: das Angebot OHNE Geld (GET /invoicing/quotes/mengen, 0102)
// ---------------------------------------------------------------------------
//
// **Kein Typ hier erbt von `Quote`/`QuoteLine`.** Das ist Absicht: Erbte die
// Mengenzeile die Angebotszeile, trüge sie `unit_price`, `unit_cost` (Einkauf!) und
// `markup_percent` (Aufschlag) im Typ — und irgendein Template zeigte sie
// irgendwann an. Der Server schickt diese Felder für die Mengensicht nicht; der Typ
// kennt sie deshalb auch nicht.

/** Angebotsposition der Mengensicht: **was** und **wie viel** — kein Betrag. */
export interface QuoteMengenLine {
  position_number: number;
  line_type: LineType;
  /**
   * ALTERNATIV/BEDARF heißt: **nicht beauftragt**. Deshalb steht die Angabe auch in
   * der preisfreien Sicht — sie wegzulassen wäre gefährlicher, als sie zu zeigen.
   */
  line_kind: LineKind;
  rubrik: number | null;
  description: string;
  quantity: string | null;
  unit: string | null;
  source_article_id: string | null;
  source_assembly_id: string | null;
}

/** Angebotskopf der Mengensicht — ohne `net_total`/`tax_total`/`gross_total`. */
export interface QuoteMengen {
  id: string;
  quote_number: string | null;
  title: string;
  status: QuoteStatus;
  quote_date: string | null;
  valid_until_date: string | null;
  property: QuotePropertyRef;
  work_order_id: string | null;
  /**
   * True = dem Abrufer werden Preise **vorenthalten** (row_scope EIGENE). Das UI
   * sagt es ihm ins Gesicht, statt Spalten stillschweigend wegzulassen — dieselbe
   * Ehrlichkeitsregel wie bei der gekürzten Trefferliste der Suche.
   */
  preise_ausgeblendet: boolean;
}

export interface QuoteMengenDetail extends QuoteMengen {
  project: { id: string; project_number: string; name: string } | null;
  work_order: { id: string; order_number: string; title: string } | null;
  sent_at: string | null;
  rubriken: Rubrik[];
  lines: QuoteMengenLine[];
}

export interface QuoteMengenPage {
  items: QuoteMengen[];
  total: number;
  page: number;
  page_size: number;
}

// ---------------------------------------------------------------------------
// Beschriftungen (eine Quelle für alle Belegansichten)
// ---------------------------------------------------------------------------

export const QUOTE_STATUS_LABEL: Readonly<Record<QuoteStatus, string>> = {
  ENTWURF: 'Entwurf',
  INTERN_GEPRUEFT: 'Intern geprüft',
  FREIGEGEBEN: 'Freigegeben',
  VERSENDET: 'Versendet',
  ANGENOMMEN: 'Angenommen',
  ABGELEHNT: 'Abgelehnt',
  ABGELAUFEN: 'Abgelaufen',
  ERSETZT: 'Ersetzt',
};

export const LINE_TYPE_LABEL: Readonly<Record<LineType, string>> = {
  MATERIAL: 'Material',
  ARBEITSZEIT: 'Arbeitszeit',
  PAUSCHALE: 'Pauschale',
  FREMDLEISTUNG: 'Fremdleistung',
  FAHRT: 'Fahrt',
  ZUSCHLAG: 'Zuschlag',
  TEXT: 'Text',
  ZWISCHENSUMME: 'Zwischensumme',
};

export const LINE_KIND_LABEL: Readonly<Record<LineKind, string>> = {
  NORMAL: '',
  ALTERNATIV: 'Alternative — nicht beauftragt',
  BEDARF: 'Bedarfsposition — nur auf Abruf',
};

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
  /** Anschreiben-Freitext im Belegkopf (Dokumente-9). null = kein Anschreiben.
   *  Beleginhalt: ab VERSENDET eingefroren (B-30). */
  cover_letter: string | null;
  rubriken: Rubrik[];
  /** Briefkopf für die Dokumentansicht (G1). */
  dokumentkopf: Dokumentkopf | null;
  lines: QuoteLine[];
}

/**
 * Briefkopf eines Belegs für die Bildschirmdarstellung (Befund G1).
 *
 * Die Zeilen kommen **fertig zusammengesetzt** vom Server — dieselbe Funktion,
 * aus der das PDF sein Anschriftfeld baut. Wie eine deutsche Anschrift
 * aufgebaut ist (Zusatz vor der Straße, PLZ und Ort in einer Zeile, Land nur
 * bei Auslandsbelegen), gehört an eine Stelle und nicht zusätzlich hierher.
 */
export interface Dokumentkopf {
  aussteller: string[];
  empfaenger: string[];
  /** Stammt der Kopf aus dem eingefrorenen Beleg (veröffentlichte Rechnung)? */
  aus_snapshot: boolean;
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
  /**
   * Vorgangsbezug: der Vorgang, an dem diese Rechnung hängt — direkt oder über
   * einen Auftrag des Vorgangs. null = keinem Vorgang zugeordnet.
   */
  service_case_id: string | null;
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
  /** Rechnungen des Vorgangs (direkt am Vorgang oder an einem seiner Aufträge). */
  service_case_id?: string | null;
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
  work_order_id: string | null;
  work_order_number: string | null;
  /**
   * Trägt die Rechnung **aktive** Abrechnungsbindungen? Dann sind die **gebundenen
   * Zeilen** unveränderlich: Der DB-Trigger `invoicing.protect_billed_invoice_lines`
   * weist seit Migration 0088 UPDATE und DELETE **einer gebundenen Zeile** ab — das
   * INSERT einer neuen, ungebundenen Zeile dagegen nicht.
   *
   * Der **Editor** bleibt trotzdem zu: Er ersetzt den ganzen Positionssatz per
   * Delete+Insert und trifft dabei zwangsläufig die gebundene Zeile (422). Ergänzen
   * lässt sich der Beleg über „Position anhängen" (`POST /invoices/{id}/lines`);
   * der Ausweg aus einem verunglückten Entwurf bleibt „Bindungen lösen"
   * (Recht invoicing/STORNIEREN).
   */
  gebunden: boolean;
  published_at: string | null;
  has_snapshot: boolean;
  content_hash: string | null;
  /** Vorbelegung für den E-Mail-Versand: primäre EMAIL der Empfängerpartei
   *  (nur bei veröffentlichten Rechnungen aufgelöst, sonst null). */
  recipient_email: string | null;
  parties: InvoiceParty[];
  rubriken: Rubrik[];
  /** Briefkopf für die Dokumentansicht (G1) — bei veröffentlichten Rechnungen
   *  aus dem eingefrorenen Snapshot, nicht aus den Live-Stammdaten. */
  dokumentkopf: Dokumentkopf | null;
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
  /**
   * EK→VK-Aufschlag in Prozent (Decimal als Punkt-String, vorzeichenbehaftet —
   * negativ = bewusster Abschlag/Verlust). **Weglassen = vom Server ableiten
   * lassen** (aus `unit_cost`/`unit_price`). Nur mitschicken, wenn der Bediener
   * den Aufschlag AUSDRÜCKLICH gesetzt hat — dann kalkuliert der Server den VK
   * daraus (und der Aufschlag hält, nicht der Preis). Sonst folgt er dem VK.
   */
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
  /** Vorgangsbezug: hängt das Angebot direkt an einen Vorgang. Hat der Vorgang ein
   *  Projekt, erbt das Angebot es serverseitig automatisch. */
  service_case_id?: string | null;
  /** Auftragsbezug (= Soll dieser Baustelle). Der Auftrag muss zur selben
   *  Liegenschaft/zum selben Projekt gehören, sonst 422. */
  work_order_id?: string | null;
  quote_date?: string | null;
  valid_until_date?: string | null;
  /** Anschreiben-Freitext im Belegkopf (Dokumente-9), optional. */
  cover_letter?: string | null;
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
  /** Projektzuordnung setzen (Verschieben) oder mit `null` lösen. Weggelassen =
   *  unverändert. Nur im Entwurf möglich (GoBD); ab VERSENDET friert die DB alle
   *  Spalten außer dem Status ein. Passt der hängende Auftrag nicht zum neuen
   *  Projekt → 422. */
  project_id?: string | null;
  /** Anschreiben-Freitext (Dokumente-9). Beleginhalt: nur im editierbaren Status
   *  änderbar, ab VERSENDET eingefroren (422). Weggelassen = unverändert,
   *  `null`/leer = löschen. */
  cover_letter?: string | null;
  rubriken?: RubrikInput[];
  lines?: QuoteLineInput[];
}

/**
 * Ziel einer Angebotskopie. Weggelassene Felder erben Liegenschaft/Projekt der
 * Quelle; `project_id: null` erzeugt eine projektlose Kopie.
 */
export interface QuoteCopy {
  property_id?: string | null;
  project_id?: string | null;
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

/**
 * Eine Zeile der Live-Vorschau (POST …/vorschau). **In Payload-Reihenfolge** —
 * also passend zur geflatteten Zeilenliste von `payloadBauen()`. Alle Beträge
 * als Punkt-String oder null (unbestimmt); Textzeilen tragen keinen Betrag.
 */
export interface BelegVorschauLine {
  net_amount: string | null;
  markup_percent: string | null;
  tax_rate_percent: string | null;
  labour_net_amount: string | null;
}

/**
 * Antwort der Live-Vorschau: derselbe Rechenweg wie beim Speichern (PUT), aber
 * **ohne** zu schreiben. Der Server bleibt die einzige verbindliche Rechenstelle;
 * die Vorschau ist reiner Komfort für den ungespeicherten Stand. `kalkulation`
 * ist null, wenn der Abrufer kein `pricing/LESEN` hat (dann bleibt die Leiste
 * verborgen, genau wie bei der geladenen Kalkulation).
 */
export interface BelegVorschau {
  lines: BelegVorschauLine[];
  net_total: string | null;
  tax_total: string | null;
  gross_total: string | null;
  kalkulation: Kalkulation | null;
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

// ---------------------------------------------------------------------------
// Abrechnung: aus Angebot bzw. aus Auftrag (Migration 0084)
// ---------------------------------------------------------------------------

/** POST /api/invoicing/invoices/aus-angebot — die Angebotskopie (PAUSCHAL). */
export interface RechnungAusAngebot {
  quote_id: string;
  invoice_date?: string | null;
  due_date?: string | null;
  payment_term_days?: number | null;
  discount_percent?: string | null;
  discount_days?: number | null;
  show_labour_costs?: boolean;
}

/**
 * POST /api/invoicing/invoices/aus-auftrag — Regieabrechnung aus Bericht + Zeiten.
 *
 * `tax_code` ist **Pflicht und hat keinen Default**: welcher Steuersatz gilt, ist
 * eine steuerliche Entscheidung des Belegs, kein Ratespiel des Servers.
 *
 * `preise` ({quelle_id → Einzelpreis als Punkt-String}) ist die **Klärung des
 * Menschen** — nur für Positionen, deren Preis der Server NICHT kennt. Für alle
 * anderen lehnt er ihn ab (422); die eine Rechenstelle wird nicht unterlaufen.
 */
export interface RechnungAusAuftrag {
  work_order_id: string;
  tax_code: string;
  preise?: Record<string, string>;
  mit_berichten?: boolean;
  mit_zeiten?: boolean;
  invoice_date?: string | null;
  due_date?: string | null;
  payment_term_days?: number | null;
  discount_percent?: string | null;
  discount_days?: number | null;
  show_labour_costs?: boolean;
}

/**
 * Rechnung über die **Abweichungen** eines PAUSCHAL-Auftrags (Nachtrag).
 *
 * MEHRVERBRAUCH mit der **Differenzmenge**, ZUSATZ mit der vollen Menge — was
 * pauschal vereinbart war, steht schon auf der Angebotsrechnung. `preise` ist der
 * bestehende Klärungsweg (Schlüssel der Abweichung → Einzelpreis).
 */
export interface RechnungAusNachtrag {
  work_order_id: string;
  tax_code: string;
  preise?: Record<string, string>;
  invoice_date?: string | null;
  due_date?: string | null;
  payment_term_days?: number | null;
  discount_percent?: string | null;
  discount_days?: number | null;
  show_labour_costs?: boolean;
}

/**
 * Der Ausgang eines versendeten Angebots.
 *
 * **ERSETZT steht bewusst nicht hier**: Der Status verlangt ein Nachfolgeangebot
 * (DB-Regel) und ist damit der Vorgang „Ersatzangebot anlegen", kein
 * Statuswechsel.
 */
export type QuoteAusgang = 'ANGENOMMEN' | 'ABGELEHNT' | 'ABGELAUFEN';

/** Ein **Vorschlag** für einen unbekannten Preis — nie vorausgefüllt. */
export interface PreisVorschlag {
  art: 'LETZTER_PREIS' | 'LISTENPREIS' | 'LOHNGRUPPE';
  betrag: string;
  quelle: string;
}

/**
 * Eine Position, für die der Server **keinen** Preis hat (aus dem 422-Körper).
 *
 * Das ist kein Fehlerbalken, sondern eine Aufgabe: Der Mensch nennt den
 * Einzelpreis, derselbe Aufruf geht mit `preise` erneut hinaus. Weggelassen wird
 * **nichts** und mit 0,00 € abgerechnet auch nichts — eine zu niedrige Rechnung,
 * die plausibel aussieht, ist der teuerste Fehler dieses Systems.
 */
export interface PreisKlaerung {
  /** ABWEICHUNG: der Nachtrag klärt je **Abweichung** des Soll-Ist (ihr
   *  Schlüssel), nicht je Berichtszeile — die Mehrmenge entsteht aus der Summe
   *  über alle Berichte. */
  quelle_art: 'BERICHTSPOSITION' | 'ZEITGRUPPE' | 'ABWEICHUNG';
  quelle_id: string;
  bezeichnung: string;
  menge: string | null;
  einheit: string | null;
  /** EK_FEHLT | KEINE_VK_REGEL | KEINE_HERKUNFT | LEISTUNG_UNVOLLSTAENDIG |
   *  LOHNGRUPPE_FEHLT | VK_NULL | LOHNSATZ_NULL */
  grund: string;
  grund_text: string;
  vorschlaege: PreisVorschlag[];
}

/** Der 422-Körper von `aus-auftrag`, wenn Preise fehlen. */
export interface PreisKlaerungFehler {
  detail: string;
  preis_unbekannt: PreisKlaerung[];
}
