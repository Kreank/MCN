// Vertrag zu /api/security/* (backend/api/security.py ist die Quelle der Wahrheit).
//
// Rechtematrix-Pflege: die Konfiguration Rolle × Modul × Aktion (+ row_scope)
// sowie die Rollenzuordnungen der Benutzer. `row_scope` kennt nur zwei Werte:
// 'ALLE' (voller Bestand) und 'EIGENE' (nur eigene Zeilen — projektweit
// fail-closed, siehe Hinweis im UI).

export type RowScope = 'ALLE' | 'EIGENE';

/** Eine Rolle (Codeliste security.role). */
export interface Role {
  code: string;
  label: string;
}

/** Eine Matrix-Zelle: darf Rolle X in Modul M die Aktion A — und in welchem Umfang. */
export interface PermissionCell {
  role_code: string;
  module: string;
  action: string;
  allowed: boolean;
  row_scope: string;
}

/**
 * Vollständige Matrix. `cells` enthält nur die im Backend hinterlegten Zeilen;
 * fehlt eine Kombination, gilt sie als nicht erlaubt (`allowed=false`).
 */
export interface PermissionMatrix {
  modules: string[];
  actions: string[];
  roles: Role[];
  cells: PermissionCell[];
}

/** Änderungs-Payload für eine einzelne Zelle (PUT /permissions). */
export interface PermissionSetInput {
  role_code: string;
  module: string;
  action: string;
  allowed: boolean;
  row_scope: string;
}

/** Ein fachlicher Benutzer (app_user) samt aktiver Rollen-Codes. */
export interface AppUserRow {
  id: string;
  display_name: string;
  status: string;
  roles: string[];
  /** Anmeldeadresse aus dem Login-Konto; null = Identität ohne Login. */
  email: string | null;
  kann_anmelden: boolean;
}

/**
 * Payload für einen neuen Benutzer (POST /users). Legt fachliche Identität und
 * Login in einem Schritt an — ohne diesen Weg verlangt das Mitarbeiterformular
 * ein Benutzerkonto, das sich nirgends anlegen ließ.
 */
export interface AppUserCreateInput {
  display_name: string;
  email: string;
  password: string;
}

/** Eine Rollenzuordnung (security.user_role), zeitlich gültig. */
export interface UserRole {
  id: string;
  user_id: string;
  user_name: string;
  role_code: string;
  role_label: string;
  valid_from: string;
  valid_until: string | null;
  is_active: boolean;
}

/** Payload für eine neue Zuordnung (POST /user-roles). */
export interface UserRoleAssignInput {
  user_id: string;
  role_code: string;
  valid_from?: string | null;
}

/** Payload zum Beenden einer Zuordnung (POST /user-roles/{id}/end). */
export interface UserRoleEndInput {
  valid_until?: string | null;
}

// --- Anzeigelabels ----------------------------------------------------------
// Best-effort-Übersetzungen der technischen Modul-/Aktions-Codes. Der rohe Code
// wird im UI zusätzlich (klein, monospace) gezeigt, damit eine unscharfe
// Übersetzung nie in die Irre führt. Unbekannte Codes fallen auf den Code zurück.

const MODUL_LABELS: Record<string, string> = {
  identity: 'Kontakte',
  property: 'Liegenschaften',
  management: 'Verwaltung',
  tenure: 'Mietverhältnisse',
  billing: 'Abrechnung',
  workflow: 'Vorgänge',
  // Die Labels folgen der Navigation, sonst schaltet man den falschen Bereich
  // frei: `invoicing` trägt die Nav-Punkte Dokumente, Buchhaltung UND
  // Auswertungen; `accounting` ist ausschließlich die Belegerfassung
  // (Eingangsrechnungen). Ein Modul namens „Buchhaltung" wäre hier irreführend.
  invoicing: 'Dokumente & Buchhaltung',
  pricing: 'Artikel & Preise',
  content: 'Dateien',
  security: 'Rechte & Sicherheit',
  ai: 'KI',
  hr: 'Personal',
  company: 'Firma',
  accounting: 'Belegerfassung',
  // Wartungsverträge, Prüffristen, Gewährleistung und die Fälligkeiten-Ansicht.
  // STORNIEREN ist hier das Tor fürs Verwerfen einer Fälligkeit — eine Frist
  // bewusst verstreichen zu lassen ist eine andere Entscheidung als sie zu
  // erledigen (AENDERN).
  maintenance: 'Wartung & Fristen',
};

const AKTION_LABELS: Record<string, string> = {
  LESEN: 'Lesen',
  ANLEGEN: 'Anlegen',
  AENDERN: 'Ändern',
  FREIGEBEN: 'Freigeben',
  VERSENDEN: 'Versenden',
  STORNIEREN: 'Stornieren',
  EXPORTIEREN: 'Exportieren',
  LOESCHEN: 'Löschen',
};

export function modulLabel(code: string): string {
  return MODUL_LABELS[code] ?? code;
}

export function aktionLabel(code: string): string {
  return AKTION_LABELS[code] ?? code;
}

const SCOPE_LABELS: Record<string, string> = {
  ALLE: 'Alle',
  EIGENE: 'Eigene',
};

export function scopeLabel(code: string): string {
  return SCOPE_LABELS[code] ?? code;
}
