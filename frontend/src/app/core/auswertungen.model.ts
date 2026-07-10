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
}

export interface ProjekteDashboard {
  filters: { date_from: string | null; date_to: string | null };
  total: number;
  open: number;
  closed: number;
  by_status: ProjektStatusRow[];
  throughput: Throughput;
  top_projects: TopProjekt[];
}

// --- Artikel-Dashboard -----------------------------------------------------
export interface ArtikelPosition {
  description: string;
  count: number;
  quantity_total: string;
  net_total: string;
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
