/**
 * Technische Anlagen einer Liegenschaft (`property.technical_asset`).
 *
 * Die Anlage ist das technische Herz des Objekts: An ihr hängen Prüfungen,
 * Aufträge und Fälligkeiten. Für den Monteur ist `supply_type` die wichtigste
 * Angabe — „Heizkörper kalt" heißt bei einer ZENTRALEN Anlage etwas anderes als
 * bei einer Etagentherme.
 *
 * Seit Migration 0101 sind das alles **echte Spalten** mit CHECKs (vorher lagen
 * sie im `attributes`-JSON). Die Feldnamen folgen deshalb der Datenbank.
 *
 * **`power_kw = null` heißt UNBEKANNT, nie 0 kW** (Projektinvariante). Die API
 * liefert Dezimalzahlen als String; `null` bleibt `null`.
 */

// SHK-Anlagenarten des Betriebs (Backend-Codeliste, Migration 0112). Die
// Reihenfolge in ART_LABELS unten ist zugleich die Reihenfolge im Dropdown.
export type AssetType =
  | 'THERME_HEIZUNG'
  | 'THERME_COMBI'
  | 'ERDWAERMEPUMPE'
  | 'FERNWAERMESTATION'
  | 'KESSEL_HEIZUNG'
  | 'KESSEL_COMBI'
  | 'HEBEANLAGE'
  | 'SOLARANLAGE'
  | 'SONSTIGE';

/** Zentral oder dezentral. UNBEKANNT ist ein ehrlicher Wert, kein Fehler. */
export type SupplyType = 'ZENTRAL' | 'DEZENTRAL' | 'UNBEKANNT';

export type EnergySource =
  | 'GAS'
  | 'OEL'
  | 'FERNWAERME'
  | 'STROM'
  | 'PELLET'
  | 'HOLZ'
  | 'SOLAR'
  | 'UMWELTWAERME'
  | 'SONSTIGE';

/** Gelöscht wird nie — stillgelegt (der No-Delete-Trigger erzwingt es). */
export type AnlageStatus = 'AKTIV' | 'INAKTIV';

export interface Anlage {
  readonly id: string;
  readonly property_id: string;
  readonly name: string;
  readonly asset_type: AssetType;
  readonly status: AnlageStatus;
  readonly supply_type: SupplyType;
  readonly building_id: string | null;
  readonly unit_id: string | null;
  readonly building_label: string | null;
  readonly unit_label: string | null;
  readonly manufacturer: string | null;
  readonly model: string | null;
  readonly year_built: number | null;
  readonly serial_number: string | null;
  readonly location_note: string | null;
  readonly energy_source: EnergySource | null;
  /** `null` = unbekannt. NIE als 0 anzeigen. */
  readonly power_kw: string | number | null;
  readonly note: string | null;
}

/**
 * Wartungsvertrag am Objekt. `bezug` ist immer 'LIEGENSCHAFT':
 * `maintenance.maintenance_contract` kennt **kein** `asset_id`. Das UI spricht
 * das aus, statt Anlagenbezug vorzutäuschen.
 */
export interface AnlageVertrag {
  readonly id: string;
  readonly contract_number: string;
  readonly name: string;
  readonly status: string;
  readonly next_due_date: string | null;
  readonly bezug: 'LIEGENSCHAFT';
}

export interface AnlagePruefung {
  readonly id: string;
  readonly name: string;
  readonly status: string;
  readonly next_due_date: string | null;
}

export interface AnlageAuftrag {
  readonly id: string;
  readonly order_number: string;
  readonly title: string;
  readonly status: string;
}

export interface AnlageFaelligkeit {
  readonly id: string;
  readonly kind: string;
  readonly title: string;
  readonly due_date: string;
  readonly status: string;
}

/**
 * Das Detail bündelt drei Rechtemodule. Jeder Baustein hängt an SEINEM Modul:
 * Wartung/Prüfungen/Fälligkeiten an `maintenance`, Aufträge an `workflow`.
 *
 * **`*_sichtbar = false` heißt „darf ich nicht sehen", nicht „gibt es nicht".**
 * Eine leere Liste ohne dieses Flag wäre eine Lüge — das UI muss den Unterschied
 * aussprechen (dieselbe Linie wie `ek_allowed` in den Auswertungen).
 */
export interface AnlageDetail extends Anlage {
  readonly wartungsvertraege: readonly AnlageVertrag[];
  readonly pruefungen: readonly AnlagePruefung[];
  readonly faelligkeiten: readonly AnlageFaelligkeit[];
  readonly auftraege: readonly AnlageAuftrag[];
  readonly maintenance_sichtbar: boolean;
  readonly workflow_sichtbar: boolean;
}

/** Anlegen. Die Liegenschaft steht in der Route, nicht im Payload. */
export interface AnlageIn {
  name: string;
  asset_type: AssetType;
  building_id?: string | null;
  unit_id?: string | null;
  supply_type?: SupplyType | null;
  manufacturer?: string | null;
  model?: string | null;
  year_built?: number | null;
  serial_number?: string | null;
  location_note?: string | null;
  energy_source?: EnergySource | null;
  power_kw?: string | null;
  note?: string | null;
}

/** PATCH: nur gesendete Felder werden geändert; `null` leert ein Feld. */
export type AnlagePatch = Partial<AnlageIn> & { status?: AnlageStatus };

// --- Beschriftungen (eine Stelle, damit Liste und Detail nicht auseinanderlaufen)

const ART_LABELS: Record<AssetType, string> = {
  THERME_HEIZUNG: 'Therme Heizung',
  THERME_COMBI: 'Therme Combi',
  ERDWAERMEPUMPE: 'Erdwärmepumpe',
  FERNWAERMESTATION: 'Fernwärmestation',
  KESSEL_HEIZUNG: 'Kessel Heizung',
  KESSEL_COMBI: 'Kessel Combi',
  HEBEANLAGE: 'Hebeanlage',
  SOLARANLAGE: 'Solaranlage',
  SONSTIGE: 'Sonstiges',
};

const SUPPLY_LABELS: Record<SupplyType, string> = {
  ZENTRAL: 'Zentrale Anlage',
  DEZENTRAL: 'Dezentral (je Einheit)',
  UNBEKANNT: 'Versorgung unbekannt',
};

const ENERGIE_LABELS: Record<EnergySource, string> = {
  GAS: 'Gas',
  OEL: 'Öl',
  FERNWAERME: 'Fernwärme',
  STROM: 'Strom',
  PELLET: 'Pellet',
  HOLZ: 'Holz',
  SOLAR: 'Solar',
  UMWELTWAERME: 'Umweltwärme',
  SONSTIGE: 'Sonstige',
};

export function artLabel(t: AssetType | null): string {
  return t ? (ART_LABELS[t] ?? t) : 'Ohne Art';
}

export function supplyLabel(v: SupplyType): string {
  return SUPPLY_LABELS[v] ?? v;
}

export function energieLabel(e: EnergySource | null): string {
  return e ? (ENERGIE_LABELS[e] ?? e) : '—';
}

export const ART_OPTIONEN = (Object.keys(ART_LABELS) as AssetType[]).map((wert) => ({
  wert,
  label: ART_LABELS[wert],
}));

export const SUPPLY_OPTIONEN = (Object.keys(SUPPLY_LABELS) as SupplyType[]).map((wert) => ({
  wert,
  label: SUPPLY_LABELS[wert],
}));

export const ENERGIE_OPTIONEN = (Object.keys(ENERGIE_LABELS) as EnergySource[]).map((wert) => ({
  wert,
  label: ENERGIE_LABELS[wert],
}));

/** Anzeigewert einer Zahl aus der API — `null` bleibt „unbekannt", nie 0. */
export function kwAnzeige(w: string | number | null | undefined): string {
  if (w === null || w === undefined || w === '') return 'unbekannt';
  const n = Number(w);
  if (!Number.isFinite(n)) return 'unbekannt';
  return `${n.toLocaleString('de-DE', { maximumFractionDigits: 2 })} kW`;
}

export function istStillgelegt(a: Anlage): boolean {
  return a.status === 'INAKTIV';
}
