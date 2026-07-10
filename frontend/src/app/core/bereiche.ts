/**
 * Recht [Modul, Aktion], das ein Oberbereich (erstes Pfadsegment) verlangt.
 * Einzige Quelle für die Zuordnung Route → Recht; genutzt für die Prüfung der
 * returnUrl nach dem Login. Deckungsgleich mit den Route-Guards in
 * `app.routes.ts` und dem Nav-Filter in `app.ts`.
 */
export const BEREICH_RECHT: Record<string, readonly [string, string]> = {
  kontakte: ['identity', 'LESEN'],
  liegenschaften: ['property', 'LESEN'],
  projekte: ['workflow', 'LESEN'],
  vorgaenge: ['workflow', 'LESEN'],
  auftraege: ['workflow', 'LESEN'],
  planung: ['workflow', 'LESEN'],
  wartung: ['workflow', 'LESEN'],
  aufgaben: ['workflow', 'LESEN'],
  mitarbeiter: ['hr', 'LESEN'],
  dokumente: ['invoicing', 'LESEN'],
  rechnungen: ['invoicing', 'LESEN'],
  buchhaltung: ['invoicing', 'LESEN'],
  auswertungen: ['invoicing', 'LESEN'],
  artikel: ['pricing', 'LESEN'],
  leistungen: ['pricing', 'LESEN'],
  // Eingangsrechnungen (accounting.receipt) — eigener Belegkreis, eigenes Recht.
  belegerfassung: ['accounting', 'LESEN'],
  // Vier-Augen-Anträge: die Liste selbst verlangt nur security/LESEN; das
  // Entscheiden gatet der Server mit security/FREIGEBEN.
  freigaben: ['security', 'LESEN'],
  // Einstellungen: Read-Zugang mit company/LESEN (alle Rollen); die einzelnen
  // Unterseiten und das Bearbeiten gaten feiner (invoicing bzw. AENDERN).
  einstellungen: ['company', 'LESEN'],
};

/** Recht für einen (internen) Pfad, oder null, wenn der Bereich frei ist. */
export function rechtFuerPfad(url: string): readonly [string, string] | null {
  const segment = url.split(/[?#]/)[0].split('/').filter(Boolean)[0] ?? '';
  return BEREICH_RECHT[segment] ?? null;
}
