/**
 * Belegung einer Einheit (`tenure.occupancy` + `tenure.occupancy_party`).
 *
 * **Der Mieter ist ein ganz normaler Kontakt** (`identity.party`) — mit Telefon
 * und E-Mail, verlinkbar in die Kontaktmappe. Genau dafür gibt es diesen Slice:
 * Der Monteur fährt zur Badenschen Straße, muss in die Wohnung EG rechts und
 * braucht Name und Nummer von Robco.
 *
 * Drei Unterscheidungen, die das UI aussprechen muss (und nie einebnen darf):
 *
 * * **„Nicht erfasst" ist nicht „leerstehend".** Eine Einheit ohne
 *   Belegungszeile (`belegung === null`) heißt: Niemand hat es eingetragen.
 *   Leerstand ist eine **erfasste** Belegung vom Typ `VACANT`.
 * * **Gemeinschaftsflächen und Technikräume tragen keine Belegung** (Beschluss
 *   F-12, `belegbar === false`). Dort bietet das UI gar nicht erst einen Knopf
 *   an, statt den 422 vorzuführen.
 * * **Mehrere Mieter sind der Normalfall** (Ehepaar, Mitbewohner) — nicht der
 *   Sonderfall.
 */

export type OccupancyType =
  | 'RENTED'
  | 'OWNER_OCCUPIED'
  | 'VACANT'
  | 'COMMERCIAL_USE'
  | 'OTHER'
  | 'UNKNOWN';

export type MieterRolle =
  | 'CONTRACTUAL_TENANT'
  | 'CO_TENANT'
  | 'OCCUPANT'
  | 'OWNER_OCCUPANT'
  | 'COMMERCIAL_USER';

export interface Mieter {
  readonly id: string;
  readonly party_id: string;
  readonly display_name: string;
  readonly role: MieterRolle;
  readonly valid_from: string;
  readonly valid_until: string | null;
  readonly is_current: boolean;
  /** `null` = nicht hinterlegt. Dann kann der Monteur nicht anrufen — das sagt das UI. */
  readonly telefon: string | null;
  readonly email: string | null;
}

export interface Belegung {
  readonly id: string;
  readonly unit_id: string;
  readonly unit_number: string;
  readonly unit_type: string;
  readonly occupancy_type: OccupancyType;
  readonly contract_reference: string | null;
  readonly valid_from: string;
  readonly valid_until: string | null;
  readonly is_current: boolean;
  readonly mieter: readonly Mieter[];
}

/** Eine Einheit mit ihrer geltenden Belegung — die Sicht der Liegenschaftsmappe. */
export interface EinheitBelegung {
  readonly unit_id: string;
  readonly unit_number: string;
  readonly unit_type: string;
  /** F-12: COMMON_AREA/TECHNICAL_ROOM tragen keine Belegung. */
  readonly belegbar: boolean;
  /** `null` = **nicht erfasst**. Nicht „leerstehend"! */
  readonly belegung: Belegung | null;
}

export interface MieterIn {
  party_id: string;
  role: MieterRolle;
  valid_from?: string | null;
  valid_until?: string | null;
}

/**
 * Beim Nachsetzen einer Person darf zugleich der Eigentümer mitkommen.
 *
 * Der Eigentümer ist **kein** Beteiligter der Belegung — wer vermietet, wohnt
 * dort gerade nicht. Er landet im Reiter „Eigentum"; der Server erledigt beides
 * in einer Transaktion.
 */
export interface MieterAddIn extends MieterIn {
  eigentuemer_party_id?: string | null;
}

export interface BelegungIn {
  unit_id: string;
  occupancy_type: OccupancyType;
  valid_from: string;
  valid_until?: string | null;
  contract_reference?: string | null;
  /** Leer = Leerstand. Ausdrücklich zulässig. */
  mieter?: MieterIn[];
  /**
   * Wem die Einheit gehört. Wird als Eigentumsstand der Einheit angelegt
   * (`PARTIAL`, ohne Anteil, unbestätigt) — damit niemand dieselbe Person
   * zweimal erfassen muss. Saschas Befund: „wollen ja keine doppelte Arbeit."
   */
  eigentuemer_party_id?: string | null;
}

/** PATCH: nur gesendete Felder. `unit_id` fehlt bewusst — die Wohnung wechselt nicht. */
export interface BelegungPatch {
  occupancy_type?: OccupancyType;
  contract_reference?: string | null;
  valid_from?: string;
  valid_until?: string | null;
}

// --- Beschriftungen (eine Stelle) ------------------------------------------

const NUTZUNG_LABELS: Record<OccupancyType, string> = {
  RENTED: 'Vermietet',
  OWNER_OCCUPIED: 'Eigennutzung',
  VACANT: 'Leerstand',
  COMMERCIAL_USE: 'Gewerbliche Nutzung',
  OTHER: 'Sonstige Nutzung',
  UNKNOWN: 'Nutzung unbekannt',
};

const ROLLE_LABELS: Record<MieterRolle, string> = {
  CONTRACTUAL_TENANT: 'Mieter (Vertrag)',
  CO_TENANT: 'Mitmieter',
  OCCUPANT: 'Bewohner',
  OWNER_OCCUPANT: 'Eigentümer (bewohnt)',
  COMMERCIAL_USER: 'Gewerbliche:r Nutzer:in',
};

const EINHEIT_LABELS: Record<string, string> = {
  APARTMENT: 'Wohnung',
  COMMERCIAL: 'Gewerbe',
  GARAGE: 'Garage',
  PARKING: 'Stellplatz',
  STORAGE: 'Lager',
  COMMON_AREA: 'Gemeinschaftsfläche',
  TECHNICAL_ROOM: 'Technikraum',
  OTHER: 'Sonstige',
};

export function nutzungLabel(t: OccupancyType): string {
  return NUTZUNG_LABELS[t] ?? t;
}

export function rolleLabel(r: MieterRolle): string {
  return ROLLE_LABELS[r] ?? r;
}

export function einheitLabel(t: string): string {
  return EINHEIT_LABELS[t] ?? t;
}

export const NUTZUNG_OPTIONEN = (Object.keys(NUTZUNG_LABELS) as OccupancyType[]).map(
  (wert) => ({ wert, label: NUTZUNG_LABELS[wert] }),
);

export const ROLLE_OPTIONEN = (Object.keys(ROLLE_LABELS) as MieterRolle[]).map((wert) => ({
  wert,
  label: ROLLE_LABELS[wert],
}));

/** Die geltenden Mieter einer Belegung (die beendeten bleiben lesbar, aber leise). */
export function aktuelleMieter(b: Belegung): readonly Mieter[] {
  return b.mieter.filter((m) => m.is_current);
}
