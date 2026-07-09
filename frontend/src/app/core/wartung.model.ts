// Vertrag zu /api/maintenance (maintenance.maintenance_contract in der DB).
import { PropertyRef } from './projekt.model';

export type ContractStatus = 'AKTIV' | 'INAKTIV' | 'ARCHIVIERT';
export type IntervalKind =
  | 'JAEHRLICH'
  | 'MONATLICH'
  | 'WOECHENTLICH'
  | 'TAGE'
  | 'FESTES_DATUM';
export type DueAction = 'PROJEKT' | 'AUFTRAG' | 'AUFGABE' | 'BENACHRICHTIGUNG';

export interface MaintenanceContract {
  id: string;
  contract_number: string;
  name: string;
  status: ContractStatus;
  interval_kind: IntervalKind;
  interval_days: number | null;
  fixed_date: string | null;
  due_action: DueAction;
  start_date: string;
  next_due_date: string | null;
  lead_time_days: number | null;
  is_due: boolean;
  property: PropertyRef;
  customer: string | null;
  project_name: string | null;
}

export interface ContractPage {
  items: MaintenanceContract[];
  total: number;
  page: number;
  page_size: number;
}

export interface ContractQuery {
  page: number;
  page_size: number;
  q?: string;
  status?: ContractStatus | null;
  property_id?: string | null;
  due?: boolean | null;
}

export interface MaintenanceEvent {
  occurred_at: string;
  due_date: string | null;
  action: DueAction;
  result_object_type: string | null;
  result_object_id: string | null;
  note: string | null;
  triggered_by: string | null;
}

export interface ContractDetail extends MaintenanceContract {
  notes: string | null;
  created_at: string;
  events: MaintenanceEvent[];
}

// --- Schreib-Payloads ------------------------------------------------------
// POST /api/maintenance/contracts
export interface ContractCreate {
  property_id: string;
  name: string;
  start_date: string;
  interval_kind: IntervalKind;
  due_action: DueAction;
  interval_days?: number | null;
  fixed_date?: string | null;
  party_id?: string | null;
  project_id?: string | null;
  lead_time_days?: number | null;
  notes?: string | null;
}

// POST /api/maintenance/contracts/{id}/status
export interface ContractStatusInput {
  to_status: ContractStatus;
}

// POST /api/maintenance/contracts/{id}/trigger
export interface ContractTriggerInput {
  note?: string | null;
}

// --- Darstellung -----------------------------------------------------------
const STATUS_LABELS: Record<ContractStatus, string> = {
  AKTIV: 'Aktiv',
  INAKTIV: 'Inaktiv',
  ARCHIVIERT: 'Archiviert',
};

export function contractStatusLabel(s: ContractStatus): string {
  return STATUS_LABELS[s] ?? s;
}

export function contractStatusClass(s: ContractStatus): string {
  // AKTIV = positiv (grün); INAKTIV/ARCHIVIERT neutral — Amber bleibt dem
  // „Fällig"-Stempel vorbehalten, damit die Achtung-Signale nicht verwässern.
  if (s === 'AKTIV') return 'stamp--positive';
  return '';
}

const INTERVAL_LABELS: Record<IntervalKind, string> = {
  JAEHRLICH: 'Jährlich',
  MONATLICH: 'Monatlich',
  WOECHENTLICH: 'Wöchentlich',
  TAGE: 'Alle N Tage',
  FESTES_DATUM: 'Festes Datum',
};

export function intervalKindLabel(k: IntervalKind, days: number | null): string {
  if (k === 'TAGE' && days) return `Alle ${days} Tage`;
  return INTERVAL_LABELS[k] ?? k;
}

const ACTION_LABELS: Record<DueAction, string> = {
  PROJEKT: 'Projekt anlegen',
  AUFTRAG: 'Auftrag anlegen',
  AUFGABE: 'Aufgabe anlegen',
  BENACHRICHTIGUNG: 'Benachrichtigung',
};

export function dueActionLabel(a: DueAction): string {
  return ACTION_LABELS[a] ?? a;
}
