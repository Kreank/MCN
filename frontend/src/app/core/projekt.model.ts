import { PropertyType } from './property.model';

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
  // Optionale Adressteile — nur manche Endpunkte liefern sie mit (z. B. der
  // Einsatz/Termin, damit die Zieladresse angezeigt werden kann). Wo nicht
  // gesetzt, bleibt es bei Name · Stadt.
  street?: string | null;
  house_number?: string | null;
  postal_code?: string | null;
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

// --- Vorgang (service_case) Detail -----------------------------------------
export interface PartyRef {
  id: string;
  display_name: string;
}

export interface StatusChangeEntry {
  from_status: string | null;
  to_status: string;
  reason: string | null;
  changed_by: string | null;
  occurred_at: string;
}

export interface ServiceCaseDetail {
  id: string;
  case_number: string;
  subject: string;
  description: string | null;
  status: ServiceCaseStatus;
  priority: CasePriority;
  responsibility_scope: string;
  received_at: string;
  property: PropertyRef;
  project: ProjectMini | null;
  reported_by: PartyRef | null;
  history: StatusChangeEntry[];
}

// Projekt-Kurzreferenz (id/number/name) — im Vorgang-Detail.
export interface ProjectMini {
  id: string;
  project_number: string;
  name: string;
}

// GET /api/workflow/service_cases/{id}/transitions
// Ein erlaubter nächster Status eines Vorgangs (zur Laufzeit aus
// workflow.status_transition gelesen). `recht` = das je Übergang nötige
// Modulrecht: FREIGEBEN für die Beauftragung, sonst AENDERN.
export interface ServiceCaseTransition {
  to_status: ServiceCaseStatus;
  label: string;
  reason_required: boolean;
  recht: 'AENDERN' | 'FREIGEBEN';
}

// POST /api/workflow/service_cases/{id}/status
export interface ServiceCaseStatusInput {
  to_status: ServiceCaseStatus;
  reason: string | null;
}

// --- Vorgangs-Board (GET /api/workflow/service_cases) ----------------------
// Eine Spalte des Kanban-Boards (aus workflow.status_catalog, Reihenfolge
// sort_order). is_terminal markiert die Endspalten (ABGESCHLOSSEN/ABGELEHNT),
// deren Karten das Board per Default nicht lädt.
export interface BoardColumn {
  status: ServiceCaseStatus;
  label: string;
  sort_order: number;
  is_final: boolean;
  is_terminal: boolean;
}

// Kompakte Vorgangs-Karte fürs Board.
export interface ServiceCaseCard {
  id: string;
  case_number: string;
  subject: string;
  status: ServiceCaseStatus;
  priority: CasePriority;
  project_id: string | null;
  project_name: string | null;
  received_at: string;
}

export interface ServiceCaseBoard {
  columns: BoardColumn[];
  items: ServiceCaseCard[];
  total: number;
  page: number;
  page_size: number;
}

export interface ServiceCaseBoardQuery {
  project_id?: string | null;
  status?: ServiceCaseStatus | null;
  q?: string;
  include_terminal?: boolean;
  page?: number;
  page_size?: number;
}

// --- Projekt-Cockpit: Logbuch & Checklisten --------------------------------
export type LogCategory = 'NOTIZ' | 'ANRUF' | 'ABSPRACHE' | 'ENTSCHEIDUNG' | 'SYSTEM';

export interface LogEntry {
  category: LogCategory;
  entry: string;
  created_by: string | null;
  created_at: string;
}

export interface ChecklistItem {
  position: number;
  label: string;
  done: boolean;
  done_by: string | null;
  done_at: string | null;
}

export interface Checklist {
  id: string;
  name: string;
  items: ChecklistItem[];
}

// --- Schreib-Payloads ------------------------------------------------------
// POST /api/workflow/projects
export interface ProjectCreate {
  name: string;
  category_id?: string | null;
  property_ids?: string[];
  start_date?: string | null;
  target_end_date?: string | null;
}

// POST /api/workflow/projects/{id}/log
export interface LogEntryCreate {
  entry: string;
  category: LogCategory;
}

// POST /api/workflow/projects/{id}/checklists
export interface ChecklistCreate {
  name: string;
  items: string[];
}

// POST /api/workflow/projects/{id}/service_cases
export interface ServiceCaseCreate {
  property_id: string;
  subject: string;
  description?: string | null;
  reported_by_party_id?: string | null;
  priority: CasePriority;
}

// --- Schnelleinstieg "Meldung erfassen" ------------------------------------
// POST /api/workflow/quick-intake — legt Person + Liegenschaft + Vorgang in
// EINEM atomaren Aufruf an (EFH-Eigentümer meldet einen Defekt am Telefon).
export interface QuickIntakePerson {
  // Dedup: Ist der Anrufer schon Kontakt, wird er referenziert statt neu angelegt.
  existing_party_id?: string | null;
  salutation: string | null;
  first_name: string | null;
  last_name: string | null;
}

export interface QuickIntakeContact {
  phone: string | null;
  email: string | null;
}

export interface QuickIntakeProperty {
  // Dedup: Ist die Liegenschaft schon erfasst, wird sie referenziert statt neu
  // angelegt. Dann sind die Adressfelder überflüssig (Server ignoriert sie).
  existing_property_id?: string | null;
  property_type: PropertyType;
  // Der Liegenschaftsname wird serverseitig abgeleitet — Frontend sendet null.
  name: string | null;
  street: string | null;
  house_number: string | null;
  postal_code: string | null;
  city: string | null;
}

export interface QuickIntakeMeldung {
  subject: string;
  description: string | null;
  priority: CasePriority;
}

export interface QuickIntakeIn {
  person: QuickIntakePerson;
  contact: QuickIntakeContact;
  property: QuickIntakeProperty;
  meldung: QuickIntakeMeldung;
}

export interface QuickIntakeOut {
  party_id: string;
  property_id: string;
  service_case: ServiceCaseRef;
}
