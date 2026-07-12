import { AbstractControl, ValidationErrors } from '@angular/forms';

/**
 * Dezimalzahlen verlustfrei als String behandeln — nie als JS-`number`, das
 * bei Geldbetraegen rundet. Eingabe erfolgt in deutschem Format (Komma als
 * Dezimaltrenner, Punkt als optionaler Tausendertrenner); an die API geht ein
 * Punkt-String.
 */

/**
 * Deutsche Eingabe -> API-Punkt-String.
 *  - '1.234,56' -> '1234.56'
 *  - '1234,5'   -> '1234.5'
 *  - '1234.56'  -> '1234.56' (bereits Punkt-Dezimal)
 *  - '' / null  -> ''
 */
export function deZuApiDezimal(eingabe: string | null | undefined): string {
  if (eingabe == null) return '';
  let s = String(eingabe).trim().replace(/\s/g, '');
  if (!s) return '';
  if (s.includes(',')) {
    // Deutsches Format: Punkte sind Tausendertrenner, das Komma trennt Dezimalen.
    s = s.replace(/\./g, '').replace(',', '.');
  }
  // Sonst unveraendert: bereits Punkt-Dezimal oder Ganzzahl.
  return s;
}

/**
 * API-Punkt-String -> deutsche Anzeige, z. B. zum Vorbefuellen von Edit-Formularen.
 *  - '1234.56' -> '1.234,56'
 * `nachkomma` fixiert optional die Nachkommastellen (z. B. 2 fuer Geld).
 */
export function apiZuDeDezimal(wert: string | null | undefined, nachkomma?: number): string {
  if (wert == null || String(wert).trim() === '') return '';
  const n = Number(wert);
  if (!Number.isFinite(n)) return String(wert);
  return new Intl.NumberFormat('de-DE', {
    minimumFractionDigits: nachkomma ?? 0,
    maximumFractionDigits: nachkomma ?? 20,
  }).format(n);
}

/**
 * Validator: akzeptiert deutsches ODER Punkt-Dezimalformat. Leer ist gueltig —
 * Pflicht wird getrennt ueber `Validators.required` geprueft.
 */
export function dezimalValidator(control: AbstractControl): ValidationErrors | null {
  const roh = control.value;
  if (roh == null || String(roh).trim() === '') return null;
  const api = deZuApiDezimal(roh);
  return /^-?\d+(\.\d+)?$/.test(api) ? null : { dezimal: true };
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
