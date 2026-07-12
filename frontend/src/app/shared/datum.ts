/**
 * Kleine Helfer fuer ISO-Datumsangaben (JJJJ-MM-TT), wie die API sie liefert.
 *
 * Bewusst ohne `new Date(iso)`: das interpretiert einen reinen Datums-String als
 * UTC-Mitternacht und verschiebt ihn in westlichen Zeitzonen auf den Vortag.
 * Ein Belegdatum ist ein Kalendertag, kein Zeitpunkt.
 */

/** '2026-07-11' -> '11.07.2026'. Unbekanntes Format bleibt unveraendert. */
export function isoDatumDe(iso: string): string {
  const [j, m, t] = iso.split('-');
  return t ? `${t}.${m}.${j}` : iso;
}

/** True, wenn der Kalendertag `iso` VOR dem heutigen Tag liegt (lokale Zeit). */
export function fristAbgelaufen(iso: string | null): boolean {
  if (!iso) return false;
  const heute = new Date();
  const heuteIso = [
    heute.getFullYear(),
    String(heute.getMonth() + 1).padStart(2, '0'),
    String(heute.getDate()).padStart(2, '0'),
  ].join('-');
  return iso < heuteIso; // ISO-Datumsstrings sind lexikografisch sortierbar.
}
