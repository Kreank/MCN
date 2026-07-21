import { AbstractControl, ValidationErrors } from '@angular/forms';

/**
 * Verlangt mindestens ein Zeichen, das kein Leerraum ist.
 *
 * Wozu, wenn es `Validators.required` gibt: `required` sieht ein einzelnes
 * Leerzeichen als gefüllt an. Bei Pflichtfeldern, deren Inhalt später gegen
 * einen getrimmten Wert geprüft oder angezeigt wird, entsteht daraus ein
 * Auseinanderlaufen von Speicherung und Anzeige — der Beauftragungsnachweis am
 * Auftrag ist der Anlassfall: die Datenbank kennt dort nur NOT NULL, die
 * Freigabe-Checkliste trimmt, und ein Nachweis aus Leerzeichen stünde dauerhaft
 * als „fehlt" da, obwohl die Freigabe durchginge.
 *
 * Bewusst ein benannter Validator statt `Validators.pattern(/\S/)`: `pattern`
 * fällt in `feld-fehler.ts` auf „Ungültige Eingabe." zurück — die
 * unhilfreichste Meldung ausgerechnet bei einem Feld, das gefüllt AUSSIEHT.
 *
 * Leer bleibt hier gültig; das Pflichtfeld-Thema gehört `Validators.required`.
 * Beide zusammen ergeben „nicht leer und nicht nur Leerraum".
 */
export function nichtNurLeerraumValidator(control: AbstractControl): ValidationErrors | null {
  const roh = control.value;
  if (roh == null || String(roh) === '') return null;
  return String(roh).trim() === '' ? { nurLeerraum: true } : null;
}
