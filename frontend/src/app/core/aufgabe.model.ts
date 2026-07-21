// Vertrag zu /api/workflow/tasks (workflow.task in der DB).
export type TaskStatus = 'OFFEN' | 'ERLEDIGT' | 'VERWORFEN';

export interface UserRef {
  id: string;
  display_name: string;
}
export interface TaskProjectRef {
  id: string;
  project_number: string;
  name: string;
}
export interface TaskPartyRef {
  id: string;
  display_name: string;
}

export interface Task {
  id: string;
  title: string;
  description: string | null;
  due_date: string | null;
  status: TaskStatus;
  completed_at: string | null;
  created_at: string;
  assigned_to: UserRef | null;
  project: TaskProjectRef | null;
  party: TaskPartyRef | null;
  /**
   * Auftragsbezug (Befund D2, Migration 0129). Kombinierbar mit Projekt und
   * Kontakt — eine Aufgabe am Auftrag hängt fast immer auch am Kunden, den man
   * deswegen anruft. Die DB erzwingt bewusst keine Exklusivität.
   */
  work_order: TaskWorkOrderRef | null;
}

export interface TaskWorkOrderRef {
  id: string;
  order_number: string;
  title: string;
}

export interface TaskPage {
  items: Task[];
  total: number;
  page: number;
  page_size: number;
}

// Anlage-Payload zu POST /api/workflow/tasks (Felder optional außer title).
export interface TaskCreate {
  title: string;
  description?: string | null;
  due_date?: string | null;
  assigned_to_user_id?: string | null;
  project_id?: string | null;
  party_id?: string | null;
  work_order_id?: string | null;
}

// Bearbeiten-Payload zu PATCH /api/workflow/tasks/{id}. Nur gesendete Felder
// werden geändert (Server: exclude_unset). `null` löscht eine Zuordnung.
export interface TaskUpdate {
  title?: string;
  description?: string | null;
  due_date?: string | null;
  assigned_to_user_id?: string | null;
  project_id?: string | null;
  party_id?: string | null;
  work_order_id?: string | null;
}

// Schlanke Zuweisungs-Auswahlliste (GET /api/planung/users): nur id + Name.
export interface AssignableUser {
  id: string;
  display_name: string;
}

export interface TaskQuery {
  page: number;
  page_size: number;
  q?: string;
  status?: TaskStatus | null;
  project_id?: string | null;
  party_id?: string | null;
  work_order_id?: string | null;
}
