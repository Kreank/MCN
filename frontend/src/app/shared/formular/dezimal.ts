import { AbstractControl, ValidationErrors } from '@angular/forms';

/**
 * Dezimalzahlen verlustfrei als String behandeln — nie als JS-`number`, das
 * bei Geldbetraegen rundet. Eingabe erfolgt in deutschem Format (Komma als
 * Dezimaltrenner, Punkt als optionaler Tausendertrenner); an die API geht ein
 * Punkt-String.
 *
 * WICHTIG — zwei getrennte Ausgabewege, bitte nicht vermischen:
 *  - `apiZuDeEingabe()`  → fuer **Eingabefelder**: OHNE Tausenderpunkt.
 *  - `apiZuDeAnzeige()`  → fuer **reine Anzeige** (Listen, Summen, Fliesstext):
 *    MIT Tausenderpunkt.
 *
 * Grund: ein gruppierter Wert im Eingabefeld ist beim Zuruecklesen nicht mehr
 * eindeutig ("1.200" = 1200 oder 1,2?). Frueher wurde er still als 1,2 gelesen —
 * Datenverlust um Faktor 1000 ohne jede Warnung. Deshalb rendern Eingabefelder
 * grundsaetzlich ungruppiert, und mehrdeutige Eingaben werden abgelehnt statt
 * geraten (siehe `DEZIMAL_UNGUELTIG` / `dezimalValidator`).
 */

/** API-Form eines Dezimalwerts: optionales Minus, Ziffern, optional Punkt-Dezimalen. */
const API_RE = /^-?\d+(\.\d+)?$/;

/**
 * Mehrdeutige Eingabe: Punktgruppen zu genau drei Ziffern OHNE Komma —
 * "1.500", "12.345", "1.000.000". Das kann deutsche Tausendertrennung (1500)
 * oder ein Punkt-Dezimal (1,5) sein. Fuehrende Null ausgenommen ("0.500" ist
 * keine gueltige Tausendertrennung und damit eindeutig 0,5).
 */
const MEHRDEUTIG_RE = /^-?[1-9]\d{0,2}(\.\d{3})+$/;

/**
 * Sentinel-Rueckgabe von `deZuApiDezimal` fuer unlesbare oder mehrdeutige
 * Eingaben. Bewusst KEIN stiller Zahlenwert und kein leerer String: der
 * `dezimalValidator` faengt den Fall im Formular ab; sollte der Wert trotzdem
 * an die API gehen, lehnt der Server ihn ab (fail-loud) — statt eine falsche
 * Zahl zu buchen oder ein Feld stumm zu leeren.
 */
export const DEZIMAL_UNGUELTIG = 'UNGUELTIG';

type Parse =
  { art: 'leer' } | { art: 'ok'; api: string } | { art: 'mehrdeutig' } | { art: 'ungueltig' };

function parseDe(eingabe: string | null | undefined): Parse {
  if (eingabe == null) return { art: 'leer' };
  const s = String(eingabe).trim().replace(/\s/g, '');
  if (!s) return { art: 'leer' };

  if (s.includes(',')) {
    // Deutsches Format: Punkte sind Tausendertrenner, das Komma trennt Dezimalen.
    const api = s.replace(/\./g, '').replace(',', '.');
    return API_RE.test(api) ? { art: 'ok', api } : { art: 'ungueltig' };
  }
  if (MEHRDEUTIG_RE.test(s)) return { art: 'mehrdeutig' };
  return API_RE.test(s) ? { art: 'ok', api: s } : { art: 'ungueltig' };
}

/**
 * Deutsche Eingabe -> API-Punkt-String.
 *  - '1.234,56' -> '1234.56'
 *  - '1234,5'   -> '1234.5'
 *  - '1234.56'  -> '1234.56' (bereits Punkt-Dezimal)
 *  - '' / null  -> ''
 *  - '1.500'    -> `DEZIMAL_UNGUELTIG` (mehrdeutig: 1500 oder 1,5?)
 *  - 'abc'      -> `DEZIMAL_UNGUELTIG`
 */
export function deZuApiDezimal(eingabe: string | null | undefined): string {
  const p = parseDe(eingabe);
  if (p.art === 'leer') return '';
  if (p.art === 'ok') return p.api;
  return DEZIMAL_UNGUELTIG;
}

/**
 * Ob eine Eingabe mehrdeutig ist ("1.500"). Fuer Felder ohne FormControl
 * (z. B. Inline-Edit), die eine eigene Fehlermeldung bauen.
 */
export function istMehrdeutigeDezimalEingabe(eingabe: string | null | undefined): boolean {
  return parseDe(eingabe).art === 'mehrdeutig';
}

/** Ob `deZuApiDezimal` einen brauchbaren Wert geliefert hat (leer zaehlt als brauchbar). */
export function istDezimalApiWert(api: string): boolean {
  return api === '' || API_RE.test(api);
}

/**
 * API-Punkt-String -> deutsche Schreibweise fuer **Eingabefelder**:
 * Komma als Dezimaltrenner, **ohne** Tausenderpunkt.
 *  - '1200'    -> '1200'
 *  - '1234.56' -> '1234,56'
 * `nachkomma` fixiert optional die Nachkommastellen (z. B. 2 fuer Geld).
 */
export function apiZuDeEingabe(wert: string | null | undefined, nachkomma?: number): string {
  return formatiere(wert, nachkomma, false);
}

/**
 * API-Punkt-String -> deutsche **Anzeige** (Listen, Summen, Fliesstext):
 * mit Tausenderpunkt. NICHT fuer Eingabefelder verwenden — der Punkt macht den
 * Wert beim Zuruecklesen mehrdeutig.
 *  - '1234.56' -> '1.234,56'
 */
export function apiZuDeAnzeige(wert: string | null | undefined, nachkomma?: number): string {
  return formatiere(wert, nachkomma, true);
}

function formatiere(
  wert: string | null | undefined,
  nachkomma: number | undefined,
  gruppieren: boolean,
): string {
  if (wert == null || String(wert).trim() === '') return '';
  const n = Number(wert);
  if (!Number.isFinite(n)) return String(wert);
  return new Intl.NumberFormat('de-DE', {
    minimumFractionDigits: nachkomma ?? 0,
    maximumFractionDigits: nachkomma ?? 20,
    useGrouping: gruppieren,
  }).format(n);
}

/**
 * Validator: akzeptiert deutsches ODER Punkt-Dezimalformat. Leer ist gueltig —
 * Pflicht wird getrennt ueber `Validators.required` geprueft. Mehrdeutige
 * Eingaben ("1.500") werden mit eigenem Fehlerschluessel abgelehnt, damit der
 * Nutzer sie selbst entscheidet, statt dass wir raten.
 */
export function dezimalValidator(control: AbstractControl): ValidationErrors | null {
  const p = parseDe(control.value);
  if (p.art === 'mehrdeutig') return { dezimalMehrdeutig: true };
  if (p.art === 'ungueltig') return { dezimal: true };
  return null;
}

/**
 * Validator fuer ganzzahlige Felder ohne Vorzeichen (z. B. Tagesangaben).
 * Leer ist gueltig. Markiert Fehleingaben ("30 Tage", "dreissig", "10,5") als
 * ungueltig, statt sie beim Senden stillschweigend zu verschlucken oder zu
 * truncaten — ein still geloeschtes Zahlungsziel waere schlimmer als ein Fehler.
 */
export function ganzzahlValidator(control: AbstractControl): ValidationErrors | null {
  const roh = control.value;
  if (roh == null || String(roh).trim() === '') return null;
  return /^\d+$/.test(String(roh).trim()) ? null : { ganzzahl: true };
}
