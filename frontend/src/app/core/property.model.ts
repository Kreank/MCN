// Vertrag zu GET /api/property/properties (property.property in der DB).
export type PropertyType = 'WEG' | 'RENTAL_PROPERTY' | 'COMMERCIAL' | 'MIXED' | 'OTHER';
export type PropertyStatus = 'ACTIVE' | 'INACTIVE';

export interface Property {
  id: string;
  property_number: string;
  name: string;
  property_type: PropertyType;
  status: PropertyStatus;
  city: string;
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
}

export interface Building {
  id: string;
  building_number: string;
  name: string | null;
  units: Unit[];
}

export interface PropertyDetail extends Property {
  version: number;
  address: Address;
  buildings: Building[];
  party_roles: PartyRole[];
}
