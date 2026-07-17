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
  // Wartung/Fälligkeiten hat seit Migration 0071 ein eigenes Rechtemodul
  // (`maintenance`): Wartungsverträge, Prüffristen und Gewährleistung sind ein
  // eigener Verantwortungsbereich. Verwerfen einer Frist gatet STORNIEREN.
  wartung: ['maintenance', 'LESEN'],
  aufgaben: ['workflow', 'LESEN'],
  mitarbeiter: ['hr', 'LESEN'],
  // Verwaltungssicht der Zeiterfassung (nicht die Stempeluhr „Meine Zeiten", die
  // jeder mit hr/AENDERN bedient). Steht zusätzlich in `BEREICH_NUR_ALLE`.
  zeiterfassung: ['hr', 'LESEN'],
  dokumente: ['invoicing', 'LESEN'],
  rechnungen: ['invoicing', 'LESEN'],
  buchhaltung: ['invoicing', 'LESEN'],
  auswertungen: ['invoicing', 'LESEN'],
  artikel: ['pricing', 'LESEN'],
  leistungen: ['pricing', 'LESEN'],
  // Gerätewissen: read-only-Sicht auf Hersteller-Ersatzteile (pricing.article).
  geraetewissen: ['pricing', 'LESEN'],
  // Eingangsrechnungen (accounting.receipt) — eigener Belegkreis, eigenes Recht.
  belegerfassung: ['accounting', 'LESEN'],
  // Vier-Augen-Anträge: die Liste selbst verlangt nur security/LESEN; das
  // Entscheiden gatet der Server mit security/FREIGEBEN.
  freigaben: ['security', 'LESEN'],
  // Einstellungen: Read-Zugang mit company/LESEN (alle Rollen); die einzelnen
  // Unterseiten und das Bearbeiten gaten feiner (invoicing bzw. AENDERN).
  einstellungen: ['company', 'LESEN'],
};

/**
 * Bereiche, die **row_scope ALLE** verlangen (Route-Guard `darfAlleGuard`).
 *
 * Ihre Ansichten werten den Zeilen-Scope nicht aus; der Server antwortet Konten mit
 * EIGENE deshalb mit 403 (`permissions.require`, fail-closed). Wer hier landet, sieht
 * „Kein Zugriff" — und genau das soll weder die Navigation noch der Login-Rücksprung
 * anbieten.
 *
 * `hr`: MONTEUR trägt hr/LESEN (EIGENE) für die eigene Zeiterfassung (0068).
 * `invoicing`: MONTEUR trägt invoicing/LESEN (EIGENE) für das Angebot ohne Preise
 * (0102) — Buchhaltung, Mahnwesen, Umsatzauswertung und die Rechnungsmappe bleiben
 * ihm trotzdem verschlossen. **`dokumente` steht bewusst NICHT hier**: Dort bekommt er
 * die preisfreie Angebotsliste.
 */
export const BEREICH_NUR_ALLE: ReadonlySet<string> = new Set([
  'buchhaltung',
  'auswertungen',
  'rechnungen',
  'mitarbeiter',
  'zeiterfassung',
]);

function segmentVon(url: string): string {
  return url.split(/[?#]/)[0].split('/').filter(Boolean)[0] ?? '';
}

/** Recht für einen (internen) Pfad, oder null, wenn der Bereich frei ist. */
export function rechtFuerPfad(url: string): readonly [string, string] | null {
  return BEREICH_RECHT[segmentVon(url)] ?? null;
}

/** Verlangt dieser Pfad row_scope ALLE? (siehe `BEREICH_NUR_ALLE`) */
export function nurAlleFuerPfad(url: string): boolean {
  return BEREICH_NUR_ALLE.has(segmentVon(url));
}
