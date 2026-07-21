// Vertrag zu /api/hr (hr.employee/employment_contract/absence/vacation_budget).
// Decimal-Werte kommen als String über die API (hourly_rate, weekly_hours,
// hours_*, vacation_days_per_year, days_count, *_days) — als String halten,
// nur zur Anzeige mit Number() formatieren.

export type EmployeeStatus = 'AKTIV' | 'INAKTIV' | 'AUSGETRETEN';
export type ContractStatus = 'AKTIV' | 'GEKUENDIGT';
export type AbsenceStatus =
  | 'ENTWURF'
  | 'EINGEREICHT'
  | 'GENEHMIGT'
  | 'ABGELEHNT'
  | 'ZURUECKGEZOGEN';
export type AbsenceType =
  | 'URLAUB'
  | 'KRANKHEIT'
  | 'ELTERNZEIT'
  | 'SONDERURLAUB'
  | 'UNBEZAHLT'
  | 'FORTBILDUNG';

export interface WageGroupRef {
  id: string;
  name: string;
  hourly_rate: string;
}

export interface Employee {
  id: string;
  employee_number: string;
  /** Optional seit Migration 0125 (Befund B1) — `null` = nicht erhoben. */
  first_name: string | null;
  last_name: string;
  display_name: string;
  status: EmployeeStatus;
  hired_on: string;
  left_on: string | null;
  wage_group: WageGroupRef | null;
}

export interface EmployeePage {
  items: Employee[];
  total: number;
  page: number;
  page_size: number;
}

export interface EmployeeQuery {
  page: number;
  page_size: number;
  q?: string;
  status?: EmployeeStatus | null;
}

export interface Contract {
  id: string;
  valid_from: string;
  valid_to: string | null;
  status: ContractStatus;
  weekly_hours: string;
  hours_monday: string;
  hours_tuesday: string;
  hours_wednesday: string;
  hours_thursday: string;
  hours_friday: string;
  hours_saturday: string;
  hours_sunday: string;
  vacation_days_per_year: string;
  wage_group: WageGroupRef | null;
  termination_reason: string | null;
  notes: string | null;
  is_current: boolean;
}

export interface Absence {
  id: string;
  absence_type: AbsenceType;
  start_date: string;
  end_date: string;
  half_day_start: boolean;
  half_day_end: boolean;
  days_count: string;
  status: AbsenceStatus;
  reason: string | null;
  decided_at: string | null;
  decision_note: string | null;
}

export interface VacationAccount {
  year: number;
  entitlement_days: string;
  carryover_days: string;
  adjustment_days: string;
  adjustment_reason: string | null;
  total_days: string;
  used_days: string;
  /** Verfallstag des Übertrags — null = kein Verfall eingestellt (Default). */
  expiry_date: string | null;
  /** Verfallener Übertrag. Ohne Verfallsregel immer „0.00". */
  expired_days: string;
  remaining_days: string;
}

export interface EmployeeDetail extends Employee {
  salutation: string | null;
  birth_date: string | null;
  notes: string | null;
  created_at: string;
  contracts: Contract[];
  absences: Absence[];
  vacation_account: VacationAccount;
}

// --- Schreib-Verträge (POST/PUT-Payloads) ----------------------------------
// Decimal-Felder (Stunden, Urlaubstage) sind Punkt-Strings, niemals number.

export interface EmployeeCreate {
  app_user_id: string;
  party_id: string;
  hired_on: string;
  wage_group_id?: string | null;
  notes?: string | null;
}

export interface EmployeeStatusChange {
  status: EmployeeStatus;
  left_on?: string | null;
}

export interface ContractCreate {
  valid_from: string;
  vacation_days_per_year: string;
  hours_monday: string;
  hours_tuesday: string;
  hours_wednesday: string;
  hours_thursday: string;
  hours_friday: string;
  hours_saturday: string;
  hours_sunday: string;
  valid_to?: string | null;
  wage_group_id?: string | null;
  notes?: string | null;
}

export interface ContractTerminate {
  valid_to: string;
  reason: string;
}

export interface AbsenceCreate {
  absence_type: AbsenceType;
  start_date: string;
  end_date: string;
  half_day_start?: boolean;
  half_day_end?: boolean;
  reason?: string | null;
}

export interface AbsenceDecision {
  note?: string | null;
}

export interface VacationBudget {
  year: number;
  entitlement_days: string;
  carryover_days?: string;
  adjustment_days?: string;
  adjustment_reason?: string | null;
}

/** Abwesenheitsarten für Auswahlfelder. */
export const ABSENCE_TYPES: { wert: AbsenceType; label: string }[] = [
  { wert: 'URLAUB', label: 'Urlaub' },
  { wert: 'KRANKHEIT', label: 'Krankheit' },
  { wert: 'ELTERNZEIT', label: 'Elternzeit' },
  { wert: 'SONDERURLAUB', label: 'Sonderurlaub' },
  { wert: 'UNBEZAHLT', label: 'Unbezahlt' },
  { wert: 'FORTBILDUNG', label: 'Fortbildung' },
];

// Ein Wochentag des Sollstunden-Rasters (Mo–So).
export interface WeekdayHours {
  key: keyof Pick<
    Contract,
    | 'hours_monday'
    | 'hours_tuesday'
    | 'hours_wednesday'
    | 'hours_thursday'
    | 'hours_friday'
    | 'hours_saturday'
    | 'hours_sunday'
  >;
  short: string;
  label: string;
}

export const WEEKDAYS: readonly WeekdayHours[] = [
  { key: 'hours_monday', short: 'Mo', label: 'Montag' },
  { key: 'hours_tuesday', short: 'Di', label: 'Dienstag' },
  { key: 'hours_wednesday', short: 'Mi', label: 'Mittwoch' },
  { key: 'hours_thursday', short: 'Do', label: 'Donnerstag' },
  { key: 'hours_friday', short: 'Fr', label: 'Freitag' },
  { key: 'hours_saturday', short: 'Sa', label: 'Samstag' },
  { key: 'hours_sunday', short: 'So', label: 'Sonntag' },
];

// --- Darstellung -----------------------------------------------------------
const EMPLOYEE_STATUS_LABELS: Record<EmployeeStatus, string> = {
  AKTIV: 'Aktiv',
  INAKTIV: 'Inaktiv',
  AUSGETRETEN: 'Ausgetreten',
};

export function employeeStatusLabel(s: EmployeeStatus): string {
  return EMPLOYEE_STATUS_LABELS[s] ?? s;
}

export function employeeStatusClass(s: EmployeeStatus): string {
  // AKTIV = positiv (grün); INAKTIV/AUSGETRETEN neutral. Farbe nie allein —
  // der Stempel trägt immer den Text.
  if (s === 'AKTIV') return 'stamp--positive';
  return '';
}

const CONTRACT_STATUS_LABELS: Record<ContractStatus, string> = {
  AKTIV: 'Aktiv',
  GEKUENDIGT: 'Gekündigt',
};

export function contractStatusLabel(s: ContractStatus): string {
  return CONTRACT_STATUS_LABELS[s] ?? s;
}

export function contractStatusClass(s: ContractStatus): string {
  if (s === 'AKTIV') return 'stamp--positive';
  return '';
}

const ABSENCE_STATUS_LABELS: Record<AbsenceStatus, string> = {
  ENTWURF: 'Entwurf',
  EINGEREICHT: 'Eingereicht',
  GENEHMIGT: 'Genehmigt',
  ABGELEHNT: 'Abgelehnt',
  ZURUECKGEZOGEN: 'Zurückgezogen',
};

export function absenceStatusLabel(s: AbsenceStatus): string {
  return ABSENCE_STATUS_LABELS[s] ?? s;
}

export function absenceStatusClass(s: AbsenceStatus): string {
  // Genehmigt = positiv, Abgelehnt = Warnung, Rest neutral. Immer mit Text.
  if (s === 'GENEHMIGT') return 'stamp--positive';
  if (s === 'ABGELEHNT') return 'stamp--warn';
  return '';
}

const ABSENCE_TYPE_LABELS: Record<AbsenceType, string> = {
  URLAUB: 'Urlaub',
  KRANKHEIT: 'Krankheit',
  ELTERNZEIT: 'Elternzeit',
  SONDERURLAUB: 'Sonderurlaub',
  UNBEZAHLT: 'Unbezahlt',
  FORTBILDUNG: 'Fortbildung',
};

export function absenceTypeLabel(t: AbsenceType): string {
  return ABSENCE_TYPE_LABELS[t] ?? t;
}

// --- Resturlaubs-Übertrag ins Folgejahr (Migration 0072) --------------------

/**
 * Eine Zeile der Übertrags-Vorschau. Der Übertrag **setzt** `carryover_days` des
 * Folgejahres auf den Rest des Vorjahres — er addiert nicht. Genau das macht ihn
 * idempotent: zweimal ausgeführt steht derselbe Wert.
 */
export interface CarryoverRow {
  employee_id: string;
  employee_number: string;
  name: string;
  year: number;
  target_year: number;
  entitlement_days: string;
  used_days: string;
  /** Verfallen (nur wenn im Firmenprofil ein Verfallstag eingestellt ist). */
  expired_days: string;
  remaining_days: string;
  carryover_current: string;
  carryover_new: string;
  target_entitlement_days: string;
  changes: boolean;
}
