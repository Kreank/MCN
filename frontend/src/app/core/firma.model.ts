/** Firmenprofil (Singleton) — alle Felder optional, `exists` sagt, ob gepflegt. */
export interface CompanyProfile {
  exists: boolean;
  company_name: string | null;
  legal_form: string | null;
  street: string | null;
  postal_code: string | null;
  city: string | null;
  country: string | null;
  state_code: string | null;
  phone: string | null;
  email: string | null;
  web: string | null;
  tax_number: string | null;
  vat_id: string | null;
  commercial_register: string | null;
  bank_name: string | null;
  iban: string | null;
  bic: string | null;
  managing_director: string | null;
  managing_director_title: string | null;
  default_language: string | null;
  logo_file_id: string | null;
}

/** Änderungs-Payload des Firmenprofils (nur gesetzte Felder werden gesendet). */
export type CompanyProfileInput = Partial<Omit<CompanyProfile, 'exists' | 'logo_file_id'>>;

export interface Branch {
  id: string;
  name: string;
  street: string | null;
  postal_code: string | null;
  city: string | null;
  country: string;
  phone: string | null;
  email: string | null;
  active: boolean;
}

export interface BranchInput {
  name: string;
  street?: string | null;
  postal_code?: string | null;
  city?: string | null;
  country?: string;
  phone?: string | null;
  email?: string | null;
}

export interface BranchPatch {
  name?: string;
  street?: string | null;
  postal_code?: string | null;
  city?: string | null;
  country?: string;
  phone?: string | null;
  email?: string | null;
  active?: boolean;
}

export interface Trade {
  id: string;
  code: string;
  label: string;
  active: boolean;
  sort_order: number;
}

export interface TradeInput {
  code: string;
  label: string;
  sort_order?: number;
}

export interface TradePatch {
  label?: string;
  active?: boolean;
  sort_order?: number;
}

/** Mahnstufe (Konfiguration). fee/interest_note bleiben NULL (STB-Vorbehalt). */
export interface DunningLevel {
  level: number;
  label: string;
  days_after_due: number;
  active: boolean;
  fee: string | null;
  interest_note: string | null;
}

export interface DunningLevelPatch {
  label?: string;
  days_after_due?: number;
  active?: boolean;
}
