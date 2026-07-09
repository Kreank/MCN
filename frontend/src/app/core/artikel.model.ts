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
