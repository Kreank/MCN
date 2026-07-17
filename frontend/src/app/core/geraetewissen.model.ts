// Vertrag zu /api/geraetewissen — read-only-Sicht auf Hersteller-Ersatzteile
// (pricing.article, gefiltert auf die Hersteller-Namensräume vaillant/junkers/…).
// Preise als String (Decimal) — verlustfrei. Der Großhandels-Namensraum `bo`
// erscheint hier NIE.

/** Ein Ersatzteil-Treffer (Listenzeile). */
export interface Ersatzteil {
  article_id: string;
  /** interne MCN-Nummer (DN-… nach DATANORM-Import). */
  article_number: string;
  /** herstellereigene Sachnummer (die am Gerät gesuchte Nummer). */
  supplier_article_number: string | null;
  description: string;
  manufacturer_name: string | null;
  /** Hersteller-Namensraum (vaillant | junkers | …). */
  namespace: string | null;
  unit: string;
  list_price: string | null;
}

export interface ErsatzteilPage {
  items: Ersatzteil[];
  total: number;
  page: number;
  page_size: number;
}

/** Voll-Detail eines Ersatzteils (read-only). */
export interface ErsatzteilDetail extends Ersatzteil {
  long_description: string | null;
  manufacturer_number: string | null;
  manufacturer_type: string | null;
  product_group: string | null;
  matchcode: string | null;
  /** Hersteller-/Händler-Listenpreis aus der Referenz (falls vorhanden). */
  supplier_list_price: string | null;
  last_purchase_price: string | null;
  currency: string | null;
}

/** Ein Hersteller als Filter-Chip (mit Anzahl der Ersatzteile). */
export interface Hersteller {
  namespace: string;
  label: string;
  anzahl: number;
}

/** Abfrageparameter für die Ersatzteil-Liste. */
export interface ErsatzteilQuery {
  page: number;
  page_size: number;
  q?: string;
  /** auf genau einen Hersteller-Namensraum eingrenzen. */
  namespace?: string | null;
}
