/** Lieferanten-Anbindung (pricing.supplier_connection) — Katalog-Registry. */
export interface SupplierConnection {
  id: string;
  supplier_party_id: string;
  supplier_name: string | null;
  source_system: string; // 'IDS_CONNECT' | 'DATANORM'
  source_namespace: string;
  label: string;
  connection_kind: string; // 'GROSSHAENDLER' | 'HERSTELLER'
  shop_url: string | null;
  credential_reference: string | null;
  status: string; // 'ACTIVE' | 'INACTIVE'
  last_import_at: string | null;
}

export interface SupplierConnectionIn {
  supplier_party_id: string;
  source_namespace: string;
  label: string;
  source_system?: string;
  connection_kind?: string;
  shop_url?: string | null;
  credential_reference?: string | null;
}

export interface SupplierConnectionPatch {
  label?: string;
  connection_kind?: string;
  shop_url?: string | null;
  credential_reference?: string | null;
  status?: string;
}

export function sourceSystemLabel(s: string): string {
  return s === 'IDS_CONNECT' ? 'IDS-Connect' : s === 'DATANORM' ? 'DATANORM' : s;
}

export function kindLabel(k: string): string {
  return k === 'GROSSHAENDLER' ? 'Großhändler' : k === 'HERSTELLER' ? 'Hersteller' : k;
}

export function statusLabel(s: string): string {
  return s === 'ACTIVE' ? 'Aktiv' : s === 'INACTIVE' ? 'Inaktiv' : s;
}
