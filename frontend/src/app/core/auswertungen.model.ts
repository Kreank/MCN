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
