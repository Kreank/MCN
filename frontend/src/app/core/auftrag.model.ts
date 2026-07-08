// Vertrag zu /api/workflow/work_orders (workflow.work_order in der DB).
import { PropertyRef, ProjectMini, StatusChangeEntry } from './projekt.model';

export type WorkOrderStatus =
  | 'ENTWURF'
  | 'FREIGABE_AUSSTEHEND'
  | 'FREIGEGEBEN'
  | 'IN_PLANUNG'
  | 'IN_AUSFUEHRUNG'
  | 'TECHNISCH_ABGESCHLOSSEN'
  | 'KAUFMAENNISCH_GEPRUEFT'
  | 'ABGERECHNET'
  | 'STORNIERT';

export type OrderPriority = 'NORMAL' | 'DRINGEND' | 'NOTFALL';

export interface WorkOrder {
  id: string;
  order_number: string;
  title: string;
  status: WorkOrderStatus;
  priority: OrderPriority;
  responsibility_scope: string;
  is_emergency: boolean;
  desired_date: string | null;
  created_at: string;
  property: PropertyRef;
  project: ProjectMini | null;
  service_case_number: string | null;
}

export interface WorkOrderPage {
  items: WorkOrder[];
  total: number;
  page: number;
  page_size: number;
}

export interface WorkOrderQuery {
  page: number;
  page_size: number;
  q?: string;
  status?: WorkOrderStatus | null;
  project_id?: string | null;
  property_id?: string | null;
  service_case_id?: string | null;
}

export interface WorkOrderParty {
  party_id: string;
  display_name: string;
  role: string;
  is_primary: boolean;
  allocation_percent: string | null;
  source: string;
}

export interface WorkOrderDetail extends WorkOrder {
  description: string | null;
  customer_reference: string | null;
  order_evidence_reference: string | null;
  responsibility_confirmed_at: string | null;
  version: number;
  parties: WorkOrderParty[];
  history: StatusChangeEntry[];
}

// --- Darstellung (eine Quelle für Auftrag-Detail und Projektmappe) ---------
const WORK_ORDER_STATUS_LABELS: Record<WorkOrderStatus, string> = {
  ENTWURF: 'Entwurf',
  FREIGABE_AUSSTEHEND: 'Freigabe ausstehend',
  FREIGEGEBEN: 'Freigegeben',
  IN_PLANUNG: 'In Planung',
  IN_AUSFUEHRUNG: 'In Ausführung',
  TECHNISCH_ABGESCHLOSSEN: 'Technisch abgeschlossen',
  KAUFMAENNISCH_GEPRUEFT: 'Kaufmännisch geprüft',
  ABGERECHNET: 'Abgerechnet',
  STORNIERT: 'Storniert',
};

export function workOrderStatusLabel(s: WorkOrderStatus): string {
  return WORK_ORDER_STATUS_LABELS[s] ?? s;
}

export function workOrderStatusClass(s: WorkOrderStatus): string {
  if (s === 'ABGERECHNET' || s === 'KAUFMAENNISCH_GEPRUEFT') return 'stamp--positive';
  if (s === 'STORNIERT') return 'stamp--warn';
  return '';
}
