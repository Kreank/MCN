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

/**
 * Wert eines `<input type="datetime-local">` → ISO-Zeitstempel MIT Offset.
 *
 * `datetime-local` liefert `"2026-07-21T08:00"` — ohne Zeitzone. Wer diesen
 * String roh an die API schickt, liefert einen naiven Zeitstempel; das Backend
 * laeuft mit `TIME_ZONE = "UTC"` und deutet ihn dann als 08:00Z, also 10:00
 * Ortszeit. Genau so sind Termine aus den Detail-Masken zwei Stunden versetzt in
 * der Plantafel gelandet.
 *
 * `new Date(wert)` liest den String als LOKALE Zeit (so schreibt es die
 * HTML-Spezifikation fuer datetime-local vor), `toISOString()` haengt den
 * korrekten Offset an. Leere Eingabe bleibt `null` — ein Termin ohne Zeit ist
 * der Rueckstand, kein Fehler.
 */
export function vonLokalerEingabe(wert: string | null | undefined): string | null {
  if (!wert) return null;
  const d = new Date(wert);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
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
