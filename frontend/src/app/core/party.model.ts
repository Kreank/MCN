// Vertrag zu GET /api/identity/parties (identity.party in der DB).
export type PartyType = 'PERSON' | 'ORGANIZATION';
export type PartyStatus = 'ACTIVE' | 'INACTIVE' | 'MERGED';

export interface Party {
  id: string;
  party_type: PartyType;
  display_name: string;
  status: PartyStatus;

  // --- Entscheidungsmerkmale (Dublettenvermeidung) --------------------------
  // Nur in LISTEN-Antworten gefüllt (Suche); darum optional typisiert.
  /** Primäre Telefonnummer. */
  telefon?: string | null;
  /** Primäre E-Mail-Adresse. */
  email?: string | null;
  /** Einzeilige Primäradresse. */
  address_line?: string | null;
  /** Objektbezüge, z. B. „WEG Albrechtstr. 30 (Eigentümer)". */
  objekte?: string[];
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
  /**
   * Mitarbeiter in der Liste zeigen? Vorgabe des Servers ist `true`.
   *
   * `identity.party` kennt keinen Rollen-Diskriminator — eine
   * Mitarbeiter-Person ist von einem Kunden nicht zu unterscheiden. Ein
   * Schalter statt eines harten Ausschlusses, weil ein Monteur durchaus auch
   * privat Kunde sein kann (Befund F1).
   */
  mitarbeiter_zeigen?: boolean;
}

// --- Detail (GET /api/identity/parties/{id}) -------------------------------
export interface Person {
  salutation: string | null;
  title: string | null;
  /** Optional seit Migration 0125 (Befund B1) — `null` = nicht erhoben. */
  first_name: string | null;
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
/**
 * Telefon und E-Mail gleich bei der Anlage (Befund F1/F3). Beide optional;
 * was gesetzt ist, wird als primärer Kommunikationsweg angelegt.
 */
export interface KontaktwegeIn {
  phone?: string | null;
  email?: string | null;
}

/**
 * Adresse gleich bei der Anlage (Befund F1). Sie landet als `party_address` —
 * also im Reiter „Adressen" der Kontaktmappe.
 */
export interface AdresseIn {
  street: string;
  postal_code: string;
  city: string;
  house_number?: string | null;
  address_addition?: string | null;
  country_code?: string;
  /** Vorgabe: PRIVATE bei Personen, BUSINESS bei Organisationen. */
  address_type?: string;
  label?: string | null;
}

export interface PersonIn {
  /** Optional (B1) — leer lassen heißt „nicht erhoben", nicht Leerstring. */
  first_name?: string | null;
  last_name: string;
  /** Optionale Blöcke — ohne sie verhält sich der Endpunkt wie zuvor. */
  kontakt?: KontaktwegeIn | null;
  adresse?: AdresseIn | null;
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
  /** Wie bei der Person (F1) — eine Firma hat fast immer beides. */
  kontakt?: KontaktwegeIn | null;
  adresse?: AdresseIn | null;
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
