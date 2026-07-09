// Vertrag zu /api/auth/* (backend/api/auth.py ist die Quelle der Wahrheit).

/** Ein effektives Recht: Modul + Aktion + Zeilenbereich ('ALLE' | 'EIGENE'). */
export interface Permission {
  module: string;
  action: string;
  row_scope: string;
}

/** Profil des angemeldeten Kontos samt Rollen und effektiven Rechten (MeOut). */
export interface Me {
  id: number;
  email: string;
  display_name: string;
  app_user_id: string | null;
  is_staff: boolean;
  roles: string[];
  permissions: Permission[];
}
