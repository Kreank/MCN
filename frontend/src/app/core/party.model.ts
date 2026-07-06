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
