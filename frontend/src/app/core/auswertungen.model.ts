// Vertrag zu /api/auswertungen/* (rein lesende Aggregationen).
// Beträge kommen als String (Decimal) — verlustfrei; zur Anzeige formatieren.

export interface Dashboard {
  key: string;
  title: string;
  description: string;
  available: boolean;
}

export interface Revenue {
  net_total: string;
  gross_total: string;
  invoice_count: number;
  credit_count: number;
}

export interface GewerkCount {
  name: string;
  count: number;
}

/**
 * Deckungsbeitrag/Marge. Beträge als String (Decimal). `deckungsbeitrag` und
 * `marge_prozent` sind null, wenn keine Position einen EK trägt (= UNBEKANNT,
 * nicht 0). Sie beziehen sich stets nur auf `net_mit_ek`; `net_ohne_ek` /
 * `positionen_ohne_ek` weisen die Lücke aus. `ek_vollstaendig` = true bedeutet:
 * jede summenwirksame Position hat einen EK, die Marge ist belastbar.
 */
export interface Marge {
  net_total: string;
  net_mit_ek: string;
  net_ohne_ek: string;
  ek_total: string;
  deckungsbeitrag: string | null;
  marge_prozent: string | null;
  positionen: number;
  positionen_ohne_ek: number;
  ek_vollstaendig: boolean;
}

export interface MargeGewerk extends Marge {
  name: string;
}

export interface ProjectsSummary {
  total: number;
  open: number;
  closed: number;
  created_in_range: number;
  by_gewerk: GewerkCount[];
}

export interface TimelinePoint {
  month: string; // "YYYY-MM"
  net: string;
}

export interface UmsatzProjekt {
  filters: { date_from: string | null; date_to: string | null };
  revenue: Revenue;
  projects: ProjectsSummary;
  timeline: TimelinePoint[];
  marge_sichtbar: boolean;
  marge: Marge | null;
  marge_by_gewerk: MargeGewerk[];
}

export interface CustomerRevenue {
  party_id: string;
  display_name: string;
  net_total: string;
  gross_total: string;
  invoice_count: number;
  credit_count: number;
}

export interface Kunden {
  filters: { date_from: string | null; date_to: string | null };
  customer_count: number;
  net_total: string;
  customers: CustomerRevenue[];
}

export interface AuswertungQuery {
  date_from?: string | null;
  date_to?: string | null;
}

// --- Projekte-Dashboard ----------------------------------------------------
export interface ProjektStatusRow {
  status: string; // OPEN | CLOSED
  count: number;
  net_total: string;
}

export interface Throughput {
  avg_open_age_days: number | null;
  avg_closed_duration_days: number | null;
}

export interface TopProjekt {
  project_id: string;
  project_number: string;
  name: string;
  net_total: string;
  // Realisierte Marge (nur mit pricing/LESEN; sonst null).
  ek_total: string | null;
  deckungsbeitrag: string | null;
  marge_prozent: string | null;
  positionen_ohne_ek: number | null;
  ek_vollstaendig: boolean | null;
}

export interface ProjekteDashboard {
  filters: { date_from: string | null; date_to: string | null };
  total: number;
  open: number;
  closed: number;
  by_status: ProjektStatusRow[];
  throughput: Throughput;
  top_projects: TopProjekt[];
  marge_sichtbar: boolean;
  marge: Marge | null;
  geplante_marge: Marge | null;
}

// --- Artikel-Dashboard -----------------------------------------------------
export interface ArtikelPosition {
  description: string;
  count: number;
  quantity_total: string;
  net_total: string;
  // Marge je Position (nur mit pricing/LESEN; sonst null).
  ek_total: string | null;
  deckungsbeitrag: string | null;
  marge_prozent: string | null;
  positionen_ohne_ek: number | null;
  ek_vollstaendig: boolean | null;
}

export interface ArtikelTyp {
  line_type: string;
  count: number;
  net_total: string;
}

export interface ArtikelDashboard {
  filters: { date_from: string | null; date_to: string | null };
  line_count: number;
  net_total: string;
  by_type: ArtikelTyp[];
  articles: ArtikelPosition[];
  marge_sichtbar: boolean;
  marge: Marge | null;
}

// --- Mitarbeitenden-Dashboard (hr) -----------------------------------------
export interface MitarbeiterZeile {
  employee_id: string;
  employee_number: string;
  display_name: string;
  worked_hours: string;
  vacation_entitlement: string;
  vacation_used: string;
  vacation_remaining: string;
}

export interface AbsenceTypRow {
  absence_type: string;
  days: string;
  count: number;
}

export interface MitarbeitendeDashboard {
  year: number;
  employee_count: number;
  total_worked_hours: string;
  total_absence_days: string;
  people: MitarbeiterZeile[];
  absence_by_type: AbsenceTypRow[];
}
