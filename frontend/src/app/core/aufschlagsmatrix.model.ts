/**
 * EK→VK-Aufschlagsmatrix (`/api/pricing/markup-rules`, Migration 0069).
 *
 * Beträge und Prozentsätze kommen als **String** (Decimal) über die API und
 * bleiben Strings — der Server rechnet verbindlich. `sale_price: null` heißt
 * „unbekannt", NIE 0.
 */

export type MarkupScope =
  | 'ARTIKEL'
  | 'WARENGRUPPE_LIEFERANT'
  | 'WARENGRUPPE'
  | 'LIEFERANT'
  | 'STANDARD';

export type CalcBasis = 'EK' | 'LISTENPREIS';
export type MatrixStatus = 'AKTIV' | 'INAKTIV';

/** Woher der Verkaufspreis kommt (Rangfolge, siehe Service-Doku). */
export type VkQuelle = 'ARTIKEL_FESTPREIS' | 'ARTIKEL_VK_GRUPPE' | 'MATRIX' | 'UNBEKANNT';

export interface MarkupTier {
  id: string;
  /** Ab dieser Menge gilt der Aufschlag der Stufe. */
  min_quantity: string;
  markup_percent: string;
  status: MatrixStatus;
}

export interface MarkupRule {
  id: string;
  name: string;
  scope: MarkupScope;
  /** Menschenlesbarer Geltungsbereich („Warengruppe „Sanitär"", „Standardregel"). */
  scope_text: string;
  article_id: string | null;
  article_number: string | null;
  product_group: string | null;
  supplier_party_id: string | null;
  supplier_name: string | null;
  calc_basis: CalcBasis;
  /** Vorzeichenbehaftet: negativ = Abschlag. */
  markup_percent: string;
  /** Handelsspanne auf den VK; Untergrenze, die auch eine Staffel nicht unterbietet. */
  min_margin_percent: string | null;
  status: MatrixStatus;
  tiers: MarkupTier[];
}

export interface MarkupRuleIn {
  name: string;
  calc_basis: CalcBasis;
  markup_percent: string;
  min_margin_percent?: string | null;
  article_id?: string | null;
  product_group?: string | null;
  supplier_party_id?: string | null;
}

export interface MarkupRuleUpdateIn {
  name?: string;
  calc_basis?: CalcBasis;
  markup_percent?: string;
  min_margin_percent?: string | null;
}

export interface MarkupTierIn {
  min_quantity: string;
  markup_percent: string;
}

export interface Warengruppe {
  product_group: string;
  anzahl: number;
}

/** Der Rechenweg, nicht nur die Zahl — der Anwender muss sehen, WARUM. */
export interface VkVorschlag {
  article_id: string;
  article_number: string;
  description: string;
  unit: string;
  price_unit: number;
  product_group: string | null;
  menge: string;
  ek: string | null;
  list_price: string | null;
  quelle: VkQuelle;
  regel: Pick<
    MarkupRule,
    'id' | 'name' | 'scope' | 'scope_text' | 'calc_basis' | 'markup_percent' | 'min_margin_percent' | 'tiers'
  > | null;
  basis_kind: CalcBasis | null;
  basis_amount: string | null;
  markup_percent: string | null;
  tier_min_quantity: string | null;
  min_margin_percent: string | null;
  min_margin_applied: boolean;
  /** null = unbekannt (nie 0). */
  sale_price: string | null;
  hinweis: string;
}

export type MassenpflegeAktion =
  | 'ANLEGEN'
  | 'AKTUALISIEREN'
  | 'UNVERAENDERT'
  | 'UEBERSPRUNGEN';

export interface MassenpflegeZeile {
  article_id: string;
  article_number: string;
  description: string;
  product_group: string | null;
  /** Bisheriger gespeicherter VK (null = es gab keinen). */
  alt: string | null;
  neu: string | null;
  aktion: MassenpflegeAktion;
  /** Nur bei UEBERSPRUNGEN: warum nichts passiert. */
  grund: string | null;
  /** Nur bei gerechneten Zeilen: welche Regel den Preis macht. */
  regel_name: string | null;
}

export interface MassenpflegeIn {
  product_group?: string | null;
  supplier_party_id?: string | null;
  dry_run: boolean;
  /** Fortsetzungspunkt: das `weiter` der vorigen Antwort. */
  ab_artikelnummer?: string | null;
}

export interface MassenpflegeErgebnis {
  product_group: string | null;
  supplier_party_id: string | null;
  dry_run: boolean;
  /** Umfang der gesamten Auswahl. */
  artikel_gesamt: number;
  /** In DIESEM Abschnitt betrachtet. */
  verarbeitet: number;
  angelegt: number;
  aktualisiert: number;
  unveraendert: number;
  uebersprungen: number;
  zeilen: MassenpflegeZeile[];
  /** Gesetzt, wenn noch Artikel folgen — als `ab_artikelnummer` erneut senden. */
  weiter: string | null;
}
