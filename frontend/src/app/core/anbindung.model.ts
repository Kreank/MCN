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

// --- IDS-Connect: Zugangsdaten ---------------------------------------------

/** Status der IDS-Zugangsdaten — das Passwort wird NIE zurückgegeben. */
export interface CredentialStatus {
  username: string | null;
  customer_number: string | null;
  has_password: boolean;
}

export interface CredentialIn {
  username?: string | null;
  customer_number?: string | null;
  // Write-only: weglassen = unverändert, "" = löschen, sonst neu setzen.
  password?: string | null;
}

// --- IDS-Connect: Warenkorb-Roundtrip (Punchout-Session) -------------------

/** Punchout-Formular (itek 2.5): der Client submittet ein POST-Formular an `url`. */
export interface PunchoutForm {
  url: string;
  method: string;
  enctype: string;
  fields: Record<string, string>;
}

export interface PunchoutSessionStart {
  session_id: string;
  action: string;
  punchout: PunchoutForm;
}

/** Eine gegen den Artikelstamm aufgelöste Warenkorb-Position. */
export interface ResolvedPosition {
  art_no: string;
  qty: string;
  unit: string | null;
  short_text: string | null;
  ean: string | null;
  net_price: string | null;
  vat: string | null;
  article_id: string | null;
  article_number: string | null;
  article_name: string | null;
  matched: boolean;
  ambiguous: boolean;
}

export interface PunchoutSession {
  id: string;
  connection_id: string;
  quote_id: string | null;
  action: string;
  status: string; // 'OFFEN' | 'EINGELOEST'
  redeemed_at: string | null;
  total: number;
  matched: number;
  positions: ResolvedPosition[];
}

/** Eine an den Shop zu übergebende Position (WKS). */
export interface CartPositionIn {
  art_no: string;
  qty: string;
  unit?: string | null;
}

export interface PunchoutSessionIn {
  action?: string; // 'WKE' | 'WKS'
  quote_id?: string | null;
  positions?: CartPositionIn[];
}

// --- DATANORM-Import -------------------------------------------------------

export interface DatanormBeispiel {
  artikelnummer: string;
  bezeichnung: string;
  aktion: string; // 'angelegt' | 'aktualisiert' | 'deaktiviert'
  einkaufspreis: string | null;
}

export interface DatanormImportErgebnis {
  namespace: string;
  version: string | null;
  waehrung: string | null;
  stand: string | null;
  angelegt: number;
  aktualisiert: number;
  deaktiviert: number;
  ohne_einkaufspreis: number;
  verarbeitet: number;
  fehler: string[];
  beispiele: DatanormBeispiel[];
  dry_run: boolean;
}
