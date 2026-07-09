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

/** Farb-Codeliste der Terminkategorie (Token, kein Hex). Das UI mappt jeden
 * Token WCAG-sicher; die Farbe ist stets nur Ergänzung zum Namen (Text). */
export type CategoryColorToken =
  | 'NAVY'
  | 'ORANGE'
  | 'SAGE'
  | 'AMBER'
  | 'TEAL'
  | 'PLUM'
  | 'ROSE'
  | 'SLATE';

export interface CategoryRef {
  id: string;
  name: string;
  color_token: CategoryColorToken;
}

export interface ResourceRef {
  id: string;
  resource_number: string;
  name: string;
  resource_type: ResourceType;
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
  category: CategoryRef | null;
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

/** Schlanke Zuweisungs-Auswahlliste (GET /api/planung/users): nur id + Name. */
export interface AssignableUser {
  id: string;
  display_name: string;
}

/** Rollen einer Einsatz-Zuweisung (workflow.job_assignment). */
export const ASSIGNMENT_ROLES: { wert: string; label: string }[] = [
  { wert: 'TECHNICIAN', label: 'Techniker' },
  { wert: 'LEAD', label: 'Einsatzleitung' },
];

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
  appointment_category_id?: string | null;
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

/** Bahn eines Betriebsmittels (Fahrzeug/Gerät/Raum) auf der Plantafel. */
export interface BoardResourceLane {
  id: string;
  display_name: string;
  resource_type: ResourceType;
}

export interface BoardJob {
  id: string;
  job_number: string;
  title: string;
  status: ServiceJobStatus;
  scheduled_start: string;
  scheduled_end: string | null;
  property_name: string | null;
  category: CategoryRef | null;
  assignee_ids: string[];
  resource_ids: string[];
}

export interface Plantafel {
  date_from: string;
  date_to: string;
  resources: BoardResource[];
  resource_lanes: BoardResourceLane[];
  jobs: BoardJob[];
  unassigned_count: number;
}

export interface ServiceJobDetail extends ServiceJob {
  access_instructions: string | null;
  completion_notes: string | null;
  on_site_contact: string | null;
  created_at: string;
  assignments: JobAssignment[];
  resources: ResourceRef[];
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

// ===========================================================================
// Planungs-Stammdaten: Terminkategorien + Ressourcen
// ===========================================================================

export type CategoryStatus = 'AKTIV' | 'ARCHIVIERT';

export interface AppointmentCategory {
  id: string;
  name: string;
  description: string | null;
  color_token: CategoryColorToken;
  status: CategoryStatus;
  sort_order: number;
}

export interface CategoryCreate {
  name: string;
  color_token: CategoryColorToken;
  description?: string | null;
  sort_order?: number;
}

export interface CategoryUpdate {
  name?: string | null;
  color_token?: CategoryColorToken | null;
  description?: string | null;
  sort_order?: number | null;
}

export type ResourceType = 'FAHRZEUG' | 'GERAET' | 'RAUM' | 'SONSTIGE';
export type ResourceStatus = 'AKTIV' | 'INAKTIV' | 'ARCHIVIERT';

export interface Resource {
  id: string;
  resource_number: string;
  name: string;
  resource_type: ResourceType;
  status: ResourceStatus;
  notes: string | null;
}

export interface ResourceCreate {
  name: string;
  resource_type: ResourceType;
  notes?: string | null;
}

export interface ResourceUpdate {
  name?: string | null;
  resource_type?: ResourceType | null;
  notes?: string | null;
}

export interface ResourceAssignResult {
  resource: ResourceRef;
  warnings: string[];
}

// --- Farb-Codeliste (Token -> Anzeige) -------------------------------------
// Jede Kategorie zeigt IMMER ihren Namen als Text; der Farbpunkt ist nur
// dekorative Ergaenzung (WCAG: Status nie nur ueber Farbe). Die CSS-Klasse
// `kat-<token>` faerbt Punkt/Tint (siehe styles.scss / plantafel.scss).
export const CATEGORY_COLORS: { token: CategoryColorToken; label: string }[] = [
  { token: 'NAVY', label: 'Marineblau' },
  { token: 'ORANGE', label: 'Orange' },
  { token: 'SAGE', label: 'Salbeigrün' },
  { token: 'AMBER', label: 'Amber' },
  { token: 'TEAL', label: 'Petrol' },
  { token: 'PLUM', label: 'Pflaume' },
  { token: 'ROSE', label: 'Rosé' },
  { token: 'SLATE', label: 'Schiefer' },
];

export function categoryColorLabel(token: CategoryColorToken): string {
  return CATEGORY_COLORS.find((c) => c.token === token)?.label ?? token;
}

export function categoryColorClass(token: CategoryColorToken): string {
  return `kat-${token.toLowerCase()}`;
}

// --- Ressourcen-Typen/-Status ----------------------------------------------
export const RESOURCE_TYPES: { wert: ResourceType; label: string }[] = [
  { wert: 'FAHRZEUG', label: 'Fahrzeug' },
  { wert: 'GERAET', label: 'Gerät' },
  { wert: 'RAUM', label: 'Raum' },
  { wert: 'SONSTIGE', label: 'Sonstige' },
];

const RESOURCE_TYPE_LABELS: Record<ResourceType, string> = {
  FAHRZEUG: 'Fahrzeug',
  GERAET: 'Gerät',
  RAUM: 'Raum',
  SONSTIGE: 'Sonstige',
};

export function resourceTypeLabel(t: ResourceType): string {
  return RESOURCE_TYPE_LABELS[t] ?? t;
}

const RESOURCE_STATUS_LABELS: Record<ResourceStatus, string> = {
  AKTIV: 'Aktiv',
  INAKTIV: 'Inaktiv',
  ARCHIVIERT: 'Archiviert',
};

export function resourceStatusLabel(s: ResourceStatus): string {
  return RESOURCE_STATUS_LABELS[s] ?? s;
}
