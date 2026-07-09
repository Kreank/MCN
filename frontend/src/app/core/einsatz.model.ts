// Vertrag zu /api/planung/einsaetze (workflow.service_job in der DB).
import { PropertyRef, StatusChangeEntry } from './projekt.model';
import { WorkOrderStatus, workOrderStatusLabel } from './auftrag.model';

export type ServiceJobStatus =
  | 'UNGEPLANT'
  | 'GEPLANT'
  | 'BESTAETIGT'
  | 'UNTERWEGS'
  | 'VOR_ORT'
  | 'PAUSIERT'
  | 'ABGESCHLOSSEN'
  | 'NACHARBEIT'
  | 'AUSGEFALLEN';

export interface WorkOrderRef {
  id: string;
  order_number: string;
  title: string;
  status: WorkOrderStatus;
}

export interface ServiceJob {
  id: string;
  job_number: string;
  status: ServiceJobStatus;
  scheduled_start: string | null;
  scheduled_end: string | null;
  actual_start: string | null;
  actual_end: string | null;
  work_order: WorkOrderRef;
  property: PropertyRef | null;
  assignee_count: number;
}

export interface ServiceJobPage {
  items: ServiceJob[];
  total: number;
  page: number;
  page_size: number;
}

export interface ServiceJobQuery {
  page: number;
  page_size: number;
  q?: string;
  status?: ServiceJobStatus | null;
  work_order_id?: string | null;
}

export interface JobAssignment {
  assignee_id: string;
  display_name: string;
  role: string;
}

export interface TimeEntry {
  time_type: string;
  started_at: string;
  ended_at: string;
  note: string | null;
  user: string | null;
}

export interface MaterialEntry {
  description: string;
  quantity: string;
  unit: string;
  note: string | null;
}

// --- Schreib-Payloads ------------------------------------------------------
// POST /api/planung/einsaetze
export interface ServiceJobCreate {
  work_order_id: string;
  scheduled_start?: string | null;
  scheduled_end?: string | null;
  on_site_contact_party_id?: string | null;
  access_instructions?: string | null;
}

// POST /api/planung/einsaetze/{id}/schedule
export interface ScheduleInput {
  scheduled_start: string;
  scheduled_end?: string | null;
}

// POST /api/planung/einsaetze/{id}/status
export interface JobStatusInput {
  to_status: ServiceJobStatus;
  reason?: string | null;
}

// POST /api/planung/einsaetze/{id}/assignments
export interface JobAssignmentInput {
  assignee_user_id: string;
  role: string;
}

// POST /api/planung/einsaetze/{id}/times — Zeiten als ISO-Datetime.
export interface TimeLogInput {
  time_type: string;
  started_at: string;
  ended_at: string;
  user_id?: string | null;
  note?: string | null;
}

// POST /api/planung/einsaetze/{id}/materials — Menge als Dezimal-String.
export interface MaterialLogInput {
  description: string;
  quantity: string;
  unit: string;
  note?: string | null;
}

// --- Plantafel-Board -------------------------------------------------------
export interface BoardResource {
  id: string;
  display_name: string;
}

export interface BoardJob {
  id: string;
  job_number: string;
  title: string;
  status: ServiceJobStatus;
  scheduled_start: string;
  scheduled_end: string | null;
  property_name: string | null;
  assignee_ids: string[];
}

export interface Plantafel {
  date_from: string;
  date_to: string;
  resources: BoardResource[];
  jobs: BoardJob[];
  unassigned_count: number;
}

export interface ServiceJobDetail extends ServiceJob {
  access_instructions: string | null;
  completion_notes: string | null;
  on_site_contact: string | null;
  created_at: string;
  assignments: JobAssignment[];
  history: StatusChangeEntry[];
  time_entries: TimeEntry[];
  material_entries: MaterialEntry[];
}

// --- Darstellung (eine Quelle für Liste und Einsatz-Mappe) -----------------
const SERVICE_JOB_STATUS_LABELS: Record<ServiceJobStatus, string> = {
  UNGEPLANT: 'Ungeplant',
  GEPLANT: 'Geplant',
  BESTAETIGT: 'Bestätigt',
  UNTERWEGS: 'Unterwegs',
  VOR_ORT: 'Vor Ort',
  PAUSIERT: 'Pausiert',
  ABGESCHLOSSEN: 'Abgeschlossen',
  NACHARBEIT: 'Nacharbeit',
  AUSGEFALLEN: 'Ausgefallen',
};

export function serviceJobStatusLabel(s: ServiceJobStatus): string {
  return SERVICE_JOB_STATUS_LABELS[s] ?? s;
}

export function serviceJobStatusClass(s: ServiceJobStatus): string {
  if (s === 'ABGESCHLOSSEN') return 'stamp--positive';
  if (s === 'AUSGEFALLEN') return 'stamp--warn';
  return '';
}

// Auch für Verlaufseinträge (String-Status).
export function serviceJobStatusLabelStr(s: string | null): string {
  if (s === null) return 'Anlage';
  return serviceJobStatusLabel(s as ServiceJobStatus);
}

export { workOrderStatusLabel };

const TIME_TYPE_LABELS: Record<string, string> = {
  ARBEITSZEIT: 'Arbeitszeit',
  FAHRTZEIT: 'Fahrtzeit',
  PAUSE: 'Pause',
  BEREITSCHAFT: 'Bereitschaft',
  NACHARBEIT: 'Nacharbeit',
  INTERNE_ZEIT: 'Interne Zeit',
};

export function timeTypeLabel(t: string): string {
  return TIME_TYPE_LABELS[t] ?? t;
}

const ASSIGNMENT_ROLE_LABELS: Record<string, string> = {
  TECHNICIAN: 'Techniker',
  LEAD: 'Einsatzleitung',
};

export function assignmentRoleLabel(r: string): string {
  return ASSIGNMENT_ROLE_LABELS[r] ?? r;
}
