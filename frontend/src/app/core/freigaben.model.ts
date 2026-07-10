// Vertrag zu /api/security/approvals (backend/api/security.py ist die Quelle
// der Wahrheit). Vier-Augen-Freigaben: Anträge stellen, genehmigen, ablehnen,
// zurückziehen.

/** Lebenszyklus eines Freigabeantrags. */
export type ApprovalStatus = 'ANGEFORDERT' | 'GENEHMIGT' | 'ABGELEHNT' | 'ZURUECKGEZOGEN';

/**
 * Ein Freigabeantrag (ApprovalOut). Der `payload` trägt die beantragte Änderung
 * im Klartext (z. B. eine neue IBAN). Ist `payload_verborgen === true`, hat der
 * Server ihn absichtlich zurückgehalten (der Nutzer darf weder entscheiden noch
 * ist es sein Antrag) — dann ist `payload` leer und es wird ein Hinweis gezeigt.
 */
export interface Approval {
  id: string;
  action_code: string;
  action_label: string;
  status: ApprovalStatus;
  payload: Record<string, unknown>;
  payload_verborgen: boolean;
  target_table: string | null;
  target_id: string | null;
  reason: string | null;
  requested_by: string;
  requested_by_name: string | null;
  requested_at: string;
  decided_by: string | null;
  decided_by_name: string | null;
  decided_at: string | null;
  decision_note: string | null;
  applied_at: string | null;
}

// --- Darstellung -----------------------------------------------------------

const STATUS_LABELS: Record<ApprovalStatus, string> = {
  ANGEFORDERT: 'Angefordert',
  GENEHMIGT: 'Genehmigt',
  ABGELEHNT: 'Abgelehnt',
  ZURUECKGEZOGEN: 'Zurückgezogen',
};

export function approvalStatusLabel(s: ApprovalStatus): string {
  return STATUS_LABELS[s] ?? s;
}

/**
 * Stempel-Klasse je Status. Der Text bleibt der eigentliche Träger (WCAG:
 * Status nie nur über Farbe) — die Farbe ergänzt nur:
 *   ANGEFORDERT = Amber/Warnung, GENEHMIGT = Salbei/positiv,
 *   ABGELEHNT = Rot/Gefahr (lokal), ZURUECKGEZOGEN = neutral.
 */
export function approvalStatusClass(s: ApprovalStatus): string {
  if (s === 'GENEHMIGT') return 'stamp--positive';
  if (s === 'ANGEFORDERT') return 'stamp--warn';
  if (s === 'ABGELEHNT') return 'stamp--negativ';
  return '';
}

// Bekannte Payload-Schlüssel je Aktion → lesbares deutsches Label. Unbekannte
// Schlüssel werden generisch aufbereitet (siehe `payloadLabel`).
const PAYLOAD_LABELS: Record<string, string> = {
  bank_name: 'Bank',
  iban: 'IBAN',
  bic: 'BIC',
  operation: 'Vorgang',
  positions: 'Positionen',
};

/** Lesbares Label für einen Payload-Schlüssel (bekannt → fest, sonst generisch). */
export function payloadLabel(key: string): string {
  const bekannt = PAYLOAD_LABELS[key];
  if (bekannt) return bekannt;
  // Generisch: snake_case → „Snake Case".
  return key
    .split('_')
    .filter((w) => w.length > 0)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

/** Formatiert einen Payload-Wert lesbar (kein rohes JSON für Skalare). */
export function payloadWert(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (Array.isArray(value)) {
    return value
      .map((x) => (x !== null && typeof x === 'object' ? JSON.stringify(x) : String(x)))
      .join(', ');
  }
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}
