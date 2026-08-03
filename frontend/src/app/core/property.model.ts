// Vertrag zu GET /api/property/properties (property.property in der DB).
export type PropertyType =
  | 'EINFAMILIENHAUS'
  | 'WEG'
  | 'RENTAL_PROPERTY'
  | 'COMMERCIAL'
  | 'MIXED'
  | 'OTHER';
export type PropertyStatus = 'ACTIVE' | 'INACTIVE';

export interface Property {
  id: string;
  property_number: string;
  name: string;
  property_type: PropertyType;
  status: PropertyStatus;
  city: string;

  // --- Entscheidungsmerkmale (Dublettenvermeidung) --------------------------
  // Diese Felder füllt der Server nur in LISTEN-Antworten (Suche/Dubletten);
  // sie sind darum optional typisiert. Wer eine `Property` aus einem Detail
  // ableitet, darf sie nicht voraussetzen.
  /** Einzeilige Objektadresse, z. B. „Albrechtstraße 30, 12167 Berlin". */
  address_line?: string | null;
  /** Anzeigenamen der Eigentümer/WEG. */
  eigentuemer?: string[];
  /** Verwaltungsfirma. */
  verwaltung?: string | null;
  /** Erreichbare Telefonnummer zum Objekt. */
  telefon?: string | null;
  /** Woher die Nummer stammt, z. B. „Verwaltung Stegos GmbH". */
  telefon_quelle?: string | null;
  /** Anzahl erfasster Einheiten. */
  einheiten_anzahl?: number;
  /** Abweichende Gebäudeadressen (WEG über mehrere Hausnummern). */
  gebaeude_adressen?: string[];
}

export interface PropertyPage {
  items: Property[];
  total: number;
  page: number;
  page_size: number;
}

export interface PropertyQuery {
  page: number;
  page_size: number;
  q?: string;
  property_type?: PropertyType | null;
  status?: PropertyStatus | null;
}

// --- Dublettenprüfung (GET /api/property/properties/adress-dubletten) ------
/**
 * Trefferart, absteigend nach Schärfe:
 * - `EXAKT`    — Straße + Hausnummer + PLZ/Ort stimmen überein.
 * - `GEBAEUDE` — eine Gebäudeadresse der Liegenschaft passt.
 * - `STRASSE`  — nur Straße + Ort/PLZ passen, die Hausnummer weicht ab. Das ist
 *   der WEG-Fall: eine Gemeinschaft kann mehrere Hausnummern umfassen.
 */
export type AdressTrefferArt = 'EXAKT' | 'GEBAEUDE' | 'STRASSE';

export interface AdressTreffer {
  art: AdressTrefferArt;
  /** Vom Server formulierte Begründung des Treffers. */
  grund: string;
  property: Property;
}

export interface AdressDubletten {
  treffer: AdressTreffer[];
}

export interface AdressDublettenQuery {
  street: string;
  house_number?: string | null;
  postal_code?: string | null;
  city?: string | null;
  limit?: number;
}

// --- Detail (GET /api/property/properties/{id}) ----------------------------
export interface Address {
  street: string;
  house_number: string | null;
  address_addition: string | null;
  postal_code: string;
  city: string;
  country_code: string;
  latitude: number | null;
  longitude: number | null;
}

export type PropertyRoleCode =
  | 'COMMUNITY_OF_OWNERS'
  | 'PROPERTY_OWNER'
  | 'OPERATOR'
  | 'CARETAKER';

export interface PartyRole {
  party_id: string;
  party_display_name: string;
  role: PropertyRoleCode;
  valid_from: string;
  valid_until: string | null;
  is_current: boolean;
}

export interface Unit {
  id: string;
  unit_type: string;
  unit_number: string;
  /** Geschoss als Freitext (Migration 0124). `null` = nicht erfasst. */
  storey: string | null;
}

export interface Building {
  id: string;
  building_number: string;
  name: string | null;
  address_id: string | null;
  units: Unit[];
}

export interface PropertyDetail extends Property {
  version: number;
  address: Address;
  buildings: Building[];
  party_roles: PartyRole[];
}

// --- Anlage (POST /api/property/...) ---------------------------------------
export interface PropertyIn {
  name: string;
  property_type: PropertyType;
  street: string;
  postal_code: string;
  city: string;
  house_number?: string | null;
  address_addition?: string | null;
  country_code?: string;
}

export interface BuildingIn {
  /** Leer lassen: die DB zählt den Bestand dieser Liegenschaft hoch. */
  building_number?: string | null;
  name?: string | null;
}

export type UnitTypeCode =
  | 'APARTMENT'
  | 'COMMERCIAL'
  | 'GARAGE'
  | 'PARKING'
  | 'STORAGE'
  | 'COMMON_AREA'
  | 'TECHNICAL_ROOM'
  | 'OTHER';

export interface UnitIn {
  unit_type: UnitTypeCode;
  /** Leer lassen: die DB zählt je Liegenschaft hoch (A-09). */
  unit_number?: string | null;
  /** Etage gleich beim Anlegen — optional, `null` heißt „nicht erfasst". */
  storey?: string | null;
}

/**
 * PATCH-Nutzlasten (Migration 0124 / Befunde I1, I7, I12).
 *
 * Nur **gesendete** Felder werden geändert — die Felder sind deshalb optional,
 * und `null` bedeutet ausdrücklich „leeren", nicht „unverändert". Wer ein Feld
 * nicht anfassen will, lässt es weg.
 */
export interface BuildingPatch {
  building_number?: string;
  name?: string | null;
  // `address_id` fehlt bewusst — der Endpunkt nimmt es nicht an, solange die
  // Objektgrenze für Adressen nicht entschieden ist (siehe `BuildingPatch` in
  // `backend/api/property.py`).
}

export interface UnitPatch {
  unit_type?: UnitTypeCode;
  unit_number?: string;
  storey?: string | null;
}

export interface PartyRoleIn {
  party_id: string;
  role: PropertyRoleCode;
  valid_from: string;
  valid_until?: string | null;
}

// --- Deutsche Labels --------------------------------------------------------

/**
 * Deutsche Bezeichnung eines Einheitstyps. Zentral, damit Struktur-Tab,
 * Raumaufmaß und Anlagen-Dialog dieselben Begriffe zeigen (eine Wahrheit statt
 * dreier Kopien, die auseinanderlaufen).
 */
const UNIT_TYPE_LABELS: Record<UnitTypeCode, string> = {
  APARTMENT: 'Wohnung',
  COMMERCIAL: 'Gewerbe',
  GARAGE: 'Garage',
  PARKING: 'Stellplatz',
  STORAGE: 'Lager',
  COMMON_AREA: 'Gemeinschaft',
  TECHNICAL_ROOM: 'Technikraum',
  OTHER: 'Sonstige',
};

export function unitTypeLabel(t: string): string {
  return UNIT_TYPE_LABELS[t as UnitTypeCode] ?? t;
}

/** Anzeigename eines Gebäudes — Name, sonst „Gebäude <Nummer>". */
export function gebaeudeLabel(b: { name?: string | null; building_number: string }): string {
  return b.name || `Gebäude ${b.building_number}`;
}
