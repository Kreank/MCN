/**
 * Lohn-/Maschinengruppe (pricing.wage_group). `hourly_rate` ist der
 * Verrechnungssatz (VK je Stunde), `cost_rate` der optionale interne Kostensatz
 * (für die Marge; null = unbekannt). `kind` trennt Personal- von Maschinenstunden.
 * Beträge kommen als String (Decimal, verlustfrei).
 */
export type WageGroupKind = 'LOHN' | 'MASCHINE';
export type WageGroupStatus = 'AKTIV' | 'INAKTIV';

export interface WageGroup {
  id: string;
  name: string;
  kind: WageGroupKind;
  hourly_rate: string;
  cost_rate: string | null;
  status: WageGroupStatus;
}

export interface WageGroupInput {
  name: string;
  kind: WageGroupKind;
  hourly_rate: string;
  cost_rate?: string | null;
}

export interface WageGroupPatch {
  name?: string;
  kind?: WageGroupKind;
  hourly_rate?: string;
  cost_rate?: string | null;
  status?: WageGroupStatus;
}
