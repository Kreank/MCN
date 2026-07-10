// Vertrag zu /api/pricing (pricing.article / pricing.assembly).
// Preise als String (Decimal) — verlustfrei.
export type ArticleLineType =
  | 'MATERIAL'
  | 'ARBEITSZEIT'
  | 'PAUSCHALE'
  | 'FREMDLEISTUNG'
  | 'FAHRT'
  | 'ZUSCHLAG';
export type StammStatus = 'AKTIV' | 'INAKTIV';

/** Steuerschlüssel eines Artikels (Codeliste, deckungsgleich mit dem Backend). */
export type TaxCode = 'DE_19' | 'DE_7' | 'DE_0' | 'DE_13B';

/** Preiseinheit: der Preis gilt je 1 / 10 / 100 / 1000 Einheiten. */
export type PriceUnit = 1 | 10 | 100 | 1000;

export interface Article {
  id: string;
  article_number: string;
  description: string;
  unit: string;
  line_type: ArticleLineType;
  status: StammStatus;
  list_price: string | null;
}

export interface ArticlePage {
  items: Article[];
  total: number;
  page: number;
  page_size: number;
}

export interface ArticleDetail extends Article {
  long_description: string | null;
  gtin: string | null;
  manufacturer_name: string | null;
  manufacturer_number: string | null;
  manufacturer_type: string | null;
  product_group: string | null;
  matchcode: string | null;
  min_order_quantity: string | null;
  quantity_step: string | null;
  delivery_time_days: number | null;
  tax_code: TaxCode | null;
  cost_center_id: string | null;
  cost_center_label: string | null;
  price_unit: number;
  // Primärer Lieferantenbezug (Hero-Reiter „Informationen").
  supplier_party_id: string | null;
  supplier_name: string | null;
  supplier_article_number: string | null;
  last_purchase_price: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface KalkulationVariant {
  label: string;
  is_standard: boolean;
  kind: string; // FORMEL | FESTPREIS
  group_name: string | null;
  basis_kind: string | null; // EK | LISTENPREIS
  basis_amount: string | null;
  operator: string | null; // AUFSCHLAG | ABSCHLAG
  percent_change: string | null;
  amount_change: string | null;
  sale_price: string | null;
}

export interface ArticleKalkulation {
  article_id: string;
  article_number: string;
  description: string;
  list_price: string | null;
  ek: string | null;
  variants: KalkulationVariant[];
}

export interface Assembly {
  id: string;
  assembly_number: string;
  name: string;
  unit: string;
  status: StammStatus;
}

export interface AssemblyPage {
  items: Assembly[];
  total: number;
  page: number;
  page_size: number;
}

export interface AssemblyComponent {
  position: number;
  kind: 'MATERIAL' | 'LOHN';
  description: string;
  quantity: string | null;
  unit: string | null;
  minutes: string | null;
}

export interface AssemblyDetail extends Assembly {
  internal_name: string | null;
  description: string | null;
  version: number;
  components: AssemblyComponent[];
}

export interface StammQuery {
  page: number;
  page_size: number;
  q?: string;
  line_type?: ArticleLineType | null;
  status?: StammStatus | null;
}

// --- Anlage / Preis (POST/PUT /api/pricing/...) ----------------------------
export interface ArticleIn {
  article_number: string;
  description: string;
  unit: string;
  line_type?: ArticleLineType;
  list_price?: string | null;
  long_description?: string | null;
  manufacturer_name?: string | null;
  manufacturer_number?: string | null;
  manufacturer_type?: string | null;
  product_group?: string | null;
  matchcode?: string | null;
  min_order_quantity?: string | null;
  quantity_step?: string | null;
  delivery_time_days?: number | null;
  tax_code?: TaxCode | null;
  cost_center_id?: string | null;
  price_unit?: PriceUnit | null;
}

export interface AssemblyIn {
  assembly_number: string;
  name: string;
  unit: string;
  description?: string | null;
}

export interface ArticleSalePriceIn {
  label?: string;
  sale_price_group_id?: string | null;
  fixed_price?: string | null;
  is_standard?: boolean;
}

export interface ArticleSalePrice {
  id: string;
  label: string;
  sale_price_group_id: string | null;
  fixed_price: string | null;
  is_standard: boolean;
}

// --- Stammdaten-Listen (Auswahllisten für Schreib-UIs) ---------------------
/** Lohn-/Maschinengruppe (GET /api/pricing/wage_groups). */
export interface WageGroup {
  id: string;
  name: string;
  kind: string;
  hourly_rate: string;
  cost_rate: string | null;
  status: StammStatus;
}

/** VK-Kalkulationsgruppe (GET /api/pricing/sale_price_groups). */
export interface SalePriceGroup {
  id: string;
  name: string;
  calc_basis: string;
  operator: string;
  percent_change: string | null;
  amount_change: string | null;
  status: StammStatus;
}

/** Eine Stücklisten-Position: Material (article_id + quantity) ODER Lohn
 *  (wage_group_id + minutes). Dezimalwerte als Punkt-String. */
export interface ComponentIn {
  article_id?: string | null;
  quantity?: string | null;
  wage_group_id?: string | null;
  minutes?: string | null;
  note?: string | null;
}

export interface AssemblyComponentsInput {
  components: ComponentIn[];
}

// --- Artikel bearbeiten / Status / Historie / Stammdaten-Übernahme ----------

/** Artikelstammdaten ändern (PUT /articles/{id}). Nur gesetzte Felder wirken
 *  (der Server nutzt exclude_unset). Dezimalwerte als Punkt-String, list_price
 *  mit bis zu vier Nachkommastellen. */
export interface ArticleUpdateIn {
  article_number?: string;
  description?: string;
  long_description?: string | null;
  unit?: string;
  line_type?: ArticleLineType;
  list_price?: string | null;
  gtin?: string | null;
  manufacturer_name?: string | null;
  manufacturer_number?: string | null;
  manufacturer_type?: string | null;
  product_group?: string | null;
  matchcode?: string | null;
  min_order_quantity?: string | null;
  quantity_step?: string | null;
  delivery_time_days?: number | null;
  tax_code?: TaxCode | null;
  cost_center_id?: string | null;
  price_unit?: PriceUnit | null;
}

/** Artikel aktivieren/deaktivieren (POST /articles/{id}/status). */
export interface ArticleStatusIn {
  status: StammStatus;
}

/** Eine einzelne Feldänderung eines Historie-Eintrags (vorher → nachher). */
export interface HistorieFeld {
  feld: string;
  vorher: string | null;
  nachher: string | null;
}

/** Ein Eintrag im Änderungsverlauf eines Artikels (aus der Audit-Spur). */
export interface HistorieEintrag {
  occurred_at: string;
  action: string;
  akteur: string | null;
  felder: HistorieFeld[];
}

/** Werte aus einer Belegposition, die in den Artikelstamm übernommen werden
 *  (POST /articles/{id}/stammdaten-uebernehmen). Der Einkaufspreis fehlt
 *  bewusst — er ist die Aussage des Händlers, keine Meinung des Angebots. */
export interface StammdatenUebernahmeIn {
  description?: string | null;
  long_description?: string | null;
  unit?: string | null;
  verkaufspreis?: string | null;
}

// --- Verkaufspreise (Hero-Reiter „Kalkulation", rechte Tabelle) -------------

/** Eine VK-Gruppe mit errechnetem/überschriebenem VK je Stück. Alle Beträge als
 *  String (Decimal) — verlustfrei. */
export interface VerkaufspreisGruppe {
  sale_price_group_id: string;
  name: string;
  calc_basis: string; // EK | LISTENPREIS
  operator: string; // AUFSCHLAG | ABSCHLAG
  percent_change: string | null;
  amount_change: string | null;
  basis_amount: string | null; // je Stück (Basis / price_unit)
  computed_sale_price: string | null; // errechneter VK je Stück (Formelwert)
  override_price: string | null; // manuelle Überschreibung, wenn gesetzt
  effective_sale_price: string | null; // Überschreibung sonst errechnet
  is_standard: boolean;
}

/** GET /articles/{id}/verkaufspreise — alle aktiven VK-Gruppen samt Basiswerten. */
export interface VerkaufspreiseUebersicht {
  article_id: string;
  article_number: string;
  description: string;
  unit: string;
  price_unit: number;
  list_price: string | null;
  ek: string | null;
  groups: VerkaufspreisGruppe[];
}

/** Ein Tabelleneintrag beim Speichern: `fixed_price=null` ⇒ Formelwert gilt,
 *  gesetzt ⇒ Überschreibung. Genau ein Eintrag trägt `is_standard=true`. */
export interface VerkaufspreisEintragIn {
  sale_price_group_id: string;
  fixed_price: string | null;
  is_standard: boolean;
}

/** PUT /articles/{id}/verkaufspreise — setzt die GANZE Tabelle auf einmal. */
export interface VerkaufspreiseIn {
  entries: VerkaufspreisEintragIn[];
}

// --- Primärer Lieferantenbezug (Hero-Reiter „Informationen") ----------------

/** PUT /articles/{id}/lieferant — Lieferant, Lieferanten-Artikelnummer und
 *  Einkaufspreis (je `price_unit` Einheiten). Recht pricing/AENDERN. */
export interface LieferantIn {
  supplier_party_id: string;
  supplier_article_number: string;
  last_purchase_price?: string | null;
  currency?: string;
}
