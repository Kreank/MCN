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
  product_group: string | null;
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
  product_group?: string | null;
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
