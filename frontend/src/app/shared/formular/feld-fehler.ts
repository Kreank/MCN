import { AbstractControl } from '@angular/forms';

/**
 * Einheitliche deutsche Fehlermeldung fuer ein FormControl.
 *
 * Reihenfolge:
 *  1. Server-Fehler (aus einer 422-Antwort, siehe `api-fehler.ts`) haben immer
 *     Vorrang und werden auch vor der ersten Beruehrung gezeigt — sie stammen
 *     aus einem Absende-Versuch.
 *  2. Client-Validierungen erst, wenn das Feld beruehrt oder veraendert wurde,
 *     damit ein leeres Formular nicht sofort rot leuchtet.
 *
 * Bewusst eine reine Funktion (kein Signal): die Feld-Komponente ruft sie je
 * Change-Detection frisch auf, damit Fehler nach Events und HTTP-Antworten
 * sichtbar werden, ohne dass ein zusaetzlicher Tick noetig ist.
 */
export function feldFehlerText(control: AbstractControl | null | undefined): string | null {
  if (!control) return null;
  const e = control.errors;
  if (!e) return null;

  if (typeof e['server'] === 'string' && e['server'].trim()) return e['server'];

  if (!control.touched && !control.dirty) return null;

  if (e['required']) return 'Dieses Feld ist erforderlich.';
  if (e['email']) return 'Bitte eine gültige E-Mail-Adresse eingeben.';
  if (e['minlength']) return `Mindestens ${e['minlength'].requiredLength} Zeichen.`;
  if (e['maxlength']) return `Höchstens ${e['maxlength'].requiredLength} Zeichen.`;
  if (e['min'] != null) return `Der Wert ist zu klein (mindestens ${e['min'].min}).`;
  if (e['max'] != null) return `Der Wert ist zu groß (höchstens ${e['max'].max}).`;
  if (e['dezimal']) return 'Bitte eine gültige Zahl eingeben (z. B. 1.234,56).';
  if (e['gtin'])
    return 'Ungültige GTIN/EAN: 8, 12, 13 oder 14 Ziffern mit korrekter Prüfziffer.';
  return 'Ungültige Eingabe.';
}
