// Vertrag zu /api/workflow/projects (workflow.project in der DB).
export type ProjectStatus = 'OPEN' | 'CLOSED';

export interface ProjectCategory {
  id: string;
  name: string;
  color_hex: string | null;
}

export interface Project {
  id: string;
  project_number: string;
  name: string;
  status: ProjectStatus;
  start_date: string | null;
  target_end_date: string | null;
  category: ProjectCategory | null;
}

export interface ProjectPage {
  items: Project[];
  total: number;
  page: number;
  page_size: number;
}

export interface ProjectQuery {
  page: number;
  page_size: number;
  q?: string;
  status?: ProjectStatus | null;
  category_id?: string | null;
}

// --- Detail ----------------------------------------------------------------
export interface PropertyRef {
  id: string;
  property_number: string;
  name: string;
  city: string;
}

export type ServiceCaseStatus =
  | 'NEU'
  | 'IN_PRUEFUNG'
  | 'RUECKFRAGE'
  | 'FREIGABE_AUSSTEHEND'
  | 'BEAUFTRAGT'
  | 'ABGESCHLOSSEN'
  | 'ABGELEHNT';

export type CasePriority = 'NORMAL' | 'DRINGEND' | 'NOTFALL';

export interface ServiceCaseRef {
  id: string;
  case_number: string;
  subject: string;
  status: ServiceCaseStatus;
  priority: CasePriority;
  received_at: string;
}

export interface ProjectDetail extends Project {
  version: number;
  created_at: string;
  updated_at: string;
  properties: PropertyRef[];
  service_cases: ServiceCaseRef[];
}
