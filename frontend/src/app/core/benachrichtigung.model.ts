// Vertrag zu /api/benachrichtigungen (notify.notification in der DB).

/**
 * Art der Meldung. Geschlossenes Vokabular — eine neue Art kostet serverseitig
 * eine Migration (0137), damit das UI für jede eine verlässliche Beschriftung
 * hat. `string` als Rückfallwert: Ein Server, der eine hier noch unbekannte Art
 * liefert, soll die Glocke nicht leeren, sondern die Zeile schlicht ohne
 * Sonderbehandlung anzeigen.
 */
export type BenachrichtigungArt =
  | 'AUFGABE_ZUGEWIESEN'
  | 'AUFGABE_ENTZOGEN'
  | 'AUFGABE_ERLEDIGT'
  | 'AUFGABE_WIEDEROFFEN'
  | 'AUFGABE_VERWORFEN'
  | 'AUFGABE_KOMMENTAR';

export interface BenachrichtigungAusloeser {
  id: string;
  display_name: string;
}

export interface Benachrichtigung {
  id: string;
  kind: BenachrichtigungArt | string;
  /** Überschrift = das, was man wiedererkennt (z. B. der Aufgabentitel). */
  title: string;
  /** Ein Satz: was geschehen ist und wer es getan hat. */
  body: string | null;
  /** Weiche Referenz aufs Ziel, z. B. 'workflow.task'. */
  target_type: string;
  target_id: string;
  triggered_by: BenachrichtigungAusloeser | null;
  read_at: string | null;
  created_at: string;
}

export interface BenachrichtigungSeite {
  items: Benachrichtigung[];
  total: number;
  /** Zähler der Glocke — kommt in jeder Antwort mit, spart einen zweiten Ruf. */
  ungelesen: number;
  page: number;
  page_size: number;
}

export interface BenachrichtigungZaehler {
  ungelesen: number;
}

/**
 * Route zum Ziel einer Benachrichtigung. Die DB hält bewusst keine URL, nur
 * Typ + Id — die Zuordnung gehört ins Frontend, das die Routen kennt.
 * Unbekannter Typ → null (die Zeile bleibt lesbar, ist nur nicht anklickbar).
 */
export function zielRoute(n: Benachrichtigung): string[] | null {
  if (n.target_type === 'workflow.task') return ['/aufgaben', n.target_id];
  return null;
}
