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
  /** Ob ein Firmenlogo hinterlegt ist (die Bytes holt GET /company/profile/logo). */
  has_logo: boolean;

  // --- DATEV-Export (Migration 0051/0063) -----------------------------------
  /** Beraternummer des Steuerberaters (1001–9999999). */
  datev_consultant_number: string | null;
  /** Mandantennummer beim Steuerberater (1–99999). */
  datev_client_number: string | null;
  /** 'SKR03' | 'SKR04'. */
  datev_chart_of_accounts: string | null;
  datev_account_length: number | null;
  /** Wirtschaftsjahresbeginn als Monat (1–12). */
  datev_fiscal_year_start_month: number | null;
  /** Konto-Overrides; leer = SKR-Standard des Servers. */
  datev_debtor_account: string | null;
  datev_revenue_account_full: string | null;
  datev_revenue_account_reduced: string | null;
  datev_revenue_account_free: string | null;
  datev_revenue_account_reverse: string | null;
  /**
   * Wie Abschlags-/Teilrechnungen gebucht werden:
   * 'ERLOES' (Teilleistung, Default) oder 'ANZAHLUNG' (erhaltene Anzahlung —
   * die Schlussrechnung löst sie wieder auf).
   */
  datev_advance_mode: string | null;
  datev_advance_account_full: string | null;
  datev_advance_account_reduced: string | null;
  datev_advance_account_free: string | null;
  datev_advance_account_reverse: string | null;

  // --- Resturlaubs-Verfall (Migration 0072) --------------------------------
  /**
   * Verfallstag des Urlaubs-Uebertrags im Folgejahr (z. B. Monat 3 / Tag 31 =
   * „31.03."). **Beide null = KEIN Verfall — und das ist der Default.** Es wird
   * nichts weggerechnet, was der Betrieb nicht ausdruecklich eingestellt hat:
   * § 7 Abs. 3 BUrlG *erlaubt* den Verfall zum 31.03., er ordnet ihn nicht an,
   * und nach BAG/EuGH verfaellt Urlaub nur bei erfuellter Hinweispflicht.
   */
  vacation_carryover_expiry_month: number | null;
  vacation_carryover_expiry_day: number | null;

  /**
   * Betrieblicher Arbeitstag („07:00", „16:00", 60 Minuten Pause). Grundlage der
   * AUSLASTUNG auf der Plantafel: Was zwischen Feierabend und Arbeitsbeginn des
   * Folgetags liegt, ist keine Arbeitszeit, und je Tag mit mehr als sechs
   * Stunden geht die Pause ab. Ohne diese Rechnung stand ein Monteur mit einem
   * viertaegigen Einsatz bei 185 % Auslastung.
   *
   * Die SCHWELLE, ab der eine Pause faellig wird, ist bewusst kein Feld — sie
   * steht in § 4 ArbZG (mehr als sechs Stunden) und gehoert dem Gesetzgeber,
   * nicht dem Betrieb.
   */
  work_start: string | null;
  work_end: string | null;
  break_minutes: number | null;
}

/** Änderungs-Payload des Firmenprofils (nur gesetzte Felder werden gesendet). */
export type CompanyProfileInput = Partial<
  Omit<CompanyProfile, 'exists' | 'logo_file_id' | 'has_logo'>
>;

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

/** Akquisekanal/Quelle (company.acquisition_source) — „wie kam der Kunde zu uns". */
export interface AcquisitionSource {
  id: string;
  code: string;
  label: string;
  active: boolean;
  sort_order: number;
}

export interface AcquisitionSourceInput {
  code: string;
  label: string;
  sort_order?: number;
}

export interface AcquisitionSourcePatch {
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

/** Erste-Schritte-Fortschritt (GET /company/onboarding) — Ja/Nein je Meilenstein. */
export interface Onboarding {
  firmenprofil: boolean;
  logo: boolean;
  bankdaten: boolean;
  mailkonto: boolean;
  kontakt: boolean;
  liegenschaft: boolean;
  projekt: boolean;
  beleg: boolean;
}
