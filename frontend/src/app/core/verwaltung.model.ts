/**
 * Das Verwaltungsmandat (`management.management_mandate`).
 *
 * **Die Verwaltung ist KEINE Beteiligtenrolle an der Liegenschaft.** Sie läuft
 * ausschließlich über ein Mandat. Deshalb hat sie einen eigenen Reiter und
 * steht nicht bei den Beteiligten — der Unterschied wird bei der Rechnung
 * scharf:
 *
 * | Wer | Rolle |
 * |---|---|
 * | WEG Badensche Straße 53 | **Auftraggeber** (`principal`) — sie beauftragt und zahlt |
 * | Stegos Immobilien GmbH | **Verwaltung** (`management`) — sie ist der Ansprechpartner |
 * | Frau Stegemann | **Standardkontakt** (Pflicht, A-10) — sie nimmt ab |
 *
 * Ein Mandat wird **beendet**, nie gelöscht; sein **Umfang ist unveränderlich**
 * (A-11) — ein anderer Umfang ist ein Nachfolgemandat.
 */

export type MandateType =
  | 'WEG_MANAGEMENT'
  | 'RENTAL_MANAGEMENT'
  | 'SPECIAL_PROPERTY_MANAGEMENT'
  | 'SPECIAL_MANDATE';

export type ScopeType = 'ENTIRE_PROPERTY' | 'SELECTED_UNITS';

export type ResponsibilityType =
  | 'TECHNICAL_CONTACT'
  | 'COMMERCIAL_CONTACT'
  | 'ACCOUNTING_CONTACT'
  | 'EMERGENCY_CONTACT'
  | 'APPROVER';

export interface MandatKontakt {
  readonly party_id: string;
  readonly display_name: string;
  readonly telefon: string | null;
  readonly email: string | null;
}

export interface MandatsEinheit {
  readonly unit_id: string;
  readonly unit_number: string;
}

export interface Zustaendigkeit {
  readonly id: string;
  readonly responsibility_type: ResponsibilityType;
  readonly party_id: string;
  readonly display_name: string;
  readonly priority: number;
  readonly valid_from: string;
  readonly valid_until: string | null;
  readonly is_current: boolean;
  readonly telefon: string | null;
  readonly email: string | null;
}

export interface Mandat {
  readonly id: string;
  readonly property_id: string;
  readonly mandate_type: MandateType;
  readonly scope_type: ScopeType;
  readonly status: 'ACTIVE' | 'ENDED';
  readonly valid_from: string;
  readonly valid_until: string | null;
  /** „Gilt heute" — mehr als der Status (ein abgelaufenes Enddatum zählt auch). */
  readonly is_current: boolean;
  readonly contract_reference: string | null;
  readonly verwaltung: MandatKontakt;
  readonly auftraggeber: MandatKontakt;
  readonly standardkontakt: MandatKontakt;
  readonly einheiten: readonly MandatsEinheit[];
  readonly zustaendigkeiten: readonly Zustaendigkeit[];
}

export interface MandatIn {
  management_party_id: string;
  principal_party_id: string;
  /** Pflicht (A-10). Ein Mandat ohne Ansprechpartner ist eine Nummer, die niemand hat. */
  default_contact_party_id: string;
  mandate_type: MandateType;
  scope_type: ScopeType;
  valid_from: string;
  valid_until?: string | null;
  contract_reference?: string | null;
  /** Nur bei SELECTED_UNITS — und dort mindestens eine. */
  unit_ids?: string[];
}

export interface MandatPatch {
  default_contact_party_id?: string;
  contract_reference?: string | null;
}

export interface ZustaendigkeitIn {
  responsibility_type: ResponsibilityType;
  responsible_party_id: string;
  valid_from: string;
  valid_until?: string | null;
  priority?: number;
}

// --- Beschriftungen ---------------------------------------------------------

const MANDAT_LABELS: Record<MandateType, string> = {
  WEG_MANAGEMENT: 'WEG-Verwaltung',
  RENTAL_MANAGEMENT: 'Mietverwaltung',
  SPECIAL_PROPERTY_MANAGEMENT: 'Sondereigentumsverwaltung',
  SPECIAL_MANDATE: 'Sondermandat',
};

const SCOPE_LABELS: Record<ScopeType, string> = {
  ENTIRE_PROPERTY: 'Gesamte Liegenschaft',
  SELECTED_UNITS: 'Ausgewählte Einheiten',
};

const ZUSTAENDIG_LABELS: Record<ResponsibilityType, string> = {
  TECHNICAL_CONTACT: 'Technischer Kontakt',
  COMMERCIAL_CONTACT: 'Kaufmännischer Kontakt',
  ACCOUNTING_CONTACT: 'Buchhaltung',
  EMERGENCY_CONTACT: 'Notfallkontakt',
  APPROVER: 'Freigabeberechtigt',
};

export function mandatLabel(t: MandateType): string {
  return MANDAT_LABELS[t] ?? t;
}

export function scopeLabel(s: ScopeType): string {
  return SCOPE_LABELS[s] ?? s;
}

export function zustaendigLabel(t: ResponsibilityType): string {
  return ZUSTAENDIG_LABELS[t] ?? t;
}

export const MANDAT_OPTIONEN = (Object.keys(MANDAT_LABELS) as MandateType[]).map((wert) => ({
  wert,
  label: MANDAT_LABELS[wert],
}));

export const SCOPE_OPTIONEN = (Object.keys(SCOPE_LABELS) as ScopeType[]).map((wert) => ({
  wert,
  label: SCOPE_LABELS[wert],
}));

export const ZUSTAENDIG_OPTIONEN = (
  Object.keys(ZUSTAENDIG_LABELS) as ResponsibilityType[]
).map((wert) => ({ wert, label: ZUSTAENDIG_LABELS[wert] }));
