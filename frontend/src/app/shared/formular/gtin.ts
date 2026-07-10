import { AbstractControl, ValidationErrors } from '@angular/forms';

/**
 * GTIN/EAN-Prüfziffer-Vorprüfung — spiegelt EXAKT die Server-Regel
 * (`db_core/services/artikel.py::_gtin_gueltig`): 8, 12, 13 oder 14 Ziffern,
 * Modulo-10 mit den Gewichten 3 und 1 (von rechts, die letzte Ziffer ist die
 * Prüfziffer). Sie ersetzt die Server-Prüfung nicht — der Server bleibt
 * maßgeblich —, gibt dem Nutzer aber sofort Rückmeldung bei einem Tippfehler.
 *
 * Weicht diese Regel je von der Server-Regel ab, bitte hier UND dort anpassen
 * oder die Vorprüfung entfernen (eine widersprüchliche Vorprüfung wäre
 * schlimmer als keine).
 */
export function gtinGueltig(gtin: string): boolean {
  if (!/^\d+$/.test(gtin) || ![8, 12, 13, 14].includes(gtin.length)) return false;
  const ziffern = [...gtin].map((z) => Number(z));
  const pruef = ziffern[ziffern.length - 1];
  const rest = ziffern.slice(0, -1).reverse();
  const summe = rest.reduce((acc, z, i) => acc + z * (i % 2 === 0 ? 3 : 1), 0);
  return (10 - (summe % 10)) % 10 === pruef;
}

/**
 * Validator für ein GTIN-Feld. Leer ist gültig (die GTIN ist optional); nur
 * eine nicht-leere, aber ungültige GTIN meldet `{ gtin: true }`.
 */
export function gtinValidator(control: AbstractControl): ValidationErrors | null {
  const roh = control.value;
  if (roh == null || String(roh).trim() === '') return null;
  return gtinGueltig(String(roh).trim()) ? null : { gtin: true };
}
