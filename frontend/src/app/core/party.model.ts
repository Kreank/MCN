// Vertrag zu GET /api/identity/parties (identity.party in der DB).
export type PartyType = 'PERSON' | 'ORGANIZATION';
export type PartyStatus = 'ACTIVE' | 'INACTIVE' | 'MERGED';

export interface Party {
  id: string;
  party_type: PartyType;
  display_name: string;
  status: PartyStatus;
}

export interface PartyPage {
  items: Party[];
  total: number;
  page: number;
  page_size: number;
}

export interface PartyQuery {
  page: number;
  page_size: number;
  q?: string;
  party_type?: PartyType | null;
}

// --- Detail (GET /api/identity/parties/{id}) -------------------------------
export interface Person {
  salutation: string | null;
  title: string | null;
  first_name: string;
  last_name: string;
  birth_date: string | null;
}

export interface Organization {
  organization_type: string;
  legal_name: string;
  legal_form: string | null;
  registration_number: string | null;
  tax_number: string | null;
  vat_id: string | null;
}

export interface PartyDetail extends Party {
  person: Person | null;
  organization: Organization | null;
}

// --- Anlage (POST /api/identity/parties/person | /organization) ------------
export interface PersonIn {
  first_name: string;
  last_name: string;
  salutation?: string | null;
  title?: string | null;
  birth_date?: string | null;
}

export type OrganizationTypeCode =
  | 'PROPERTY_MANAGEMENT'
  | 'WEG'
  | 'COMPANY'
  | 'AUTHORITY'
  | 'INSURER'
  | 'OTHER';

export interface OrganizationIn {
  legal_name: string;
  organization_type: OrganizationTypeCode;
  display_name?: string | null;
  legal_form?: string | null;
  registration_number?: string | null;
  tax_number?: string | null;
  vat_id?: string | null;
}
