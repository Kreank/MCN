/**
 * SMTP-Absenderkonto (company.mail_account). Das Passwort ist **write-only**:
 * es kommt NIE über die API zurück — nur `has_password` sagt, ob eins hinterlegt
 * ist. Beim Speichern wird `password` nur gesendet, wenn der Benutzer eins
 * eingegeben hat (leer = unverändert lassen).
 */
export interface MailAccount {
  exists: boolean;
  label: string | null;
  host: string | null;
  port: number | null;
  security: string | null;
  username: string | null;
  from_address: string | null;
  from_name: string | null;
  active: boolean | null;
  has_password: boolean;
}

export interface MailAccountInput {
  label: string;
  host: string;
  port: number;
  security: 'NONE' | 'STARTTLS' | 'SSL';
  username?: string | null;
  /** Nur senden, wenn geändert (write-only). Weglassen = unverändert. */
  password?: string;
  from_address: string;
  from_name?: string | null;
}

export interface TestMailResult {
  sent: boolean;
  to_address: string;
}
