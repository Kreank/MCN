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

export interface AcquisitionSourceRef {
  id: string;
  code: string;
  label: string;
}

export interface PartyDetail extends Party {
  person: Person | null;
  organization: Organization | null;
  acquisition_source: AcquisitionSourceRef | null;
  // Freies Notizfeld im Stammdaten-Tab (Kontakte-3). null = keine Notiz.
  note: string | null;
}

// PUT /api/identity/parties/{id}/note — null/leer entfernt die Notiz.
export interface PartyNoteInput {
  note: string | null;
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

// --- Kontaktmappe: Adressen / Kontaktwege / Ansprechpartner ----------------

export type AddressTypeCode = 'BUSINESS' | 'POSTAL' | 'BILLING' | 'PRIVATE';
export type ContactTypeCode = 'EMAIL' | 'PHONE' | 'MOBILE' | 'FAX' | 'PORTAL';

export interface Address {
  street: string;
  house_number: string | null;
  address_addition: string | null;
  postal_code: string;
  city: string;
  country_code: string;
}

export interface PartyAddress {
  id: string;
  address_type: AddressTypeCode;
  is_primary: boolean;
  valid_from: string;
  valid_until: string | null;
  // Freier Titel/Beschreibung der Objektadresse (Kontakte-6). null = ohne.
  label: string | null;
  address: Address;
}

export interface AddressIn {
  address_type: AddressTypeCode;
  street: string;
  postal_code: string;
  city: string;
  house_number?: string | null;
  address_addition?: string | null;
  country_code?: string;
  is_primary?: boolean;
  valid_from?: string | null;
  // Optionaler freier Titel/Beschreibung der Objektadresse (Kontakte-6).
  label?: string | null;
}

export interface ContactPoint {
  id: string;
  contact_type: ContactTypeCode;
  value: string;
  label: string | null;
  is_primary: boolean;
  valid_from: string;
  valid_until: string | null;
}

export interface ContactPointIn {
  contact_type: ContactTypeCode;
  value: string;
  label?: string | null;
  is_primary?: boolean;
  valid_from?: string | null;
}

export interface ContactPerson {
  relationship_id: string;
  person_party_id: string;
  display_name: string;
  valid_from: string;
  valid_until: string | null;
  // Anzahl der von dieser Person gemeldeten Vorgänge (Kontakte-8). Es gibt keine
  // Person→Projekt-Kante im Schema; der Vorgang ist die einzige zählbare
  // Fachkante. Anlegen/Entfernen liefern 0; die Liste lädt danach neu.
  case_count: number;
}

export interface ContactPersonIn {
  person_party_id?: string | null;
  first_name?: string | null;
  last_name?: string | null;
  salutation?: string | null;
  title?: string | null;
  valid_from?: string | null;
}
