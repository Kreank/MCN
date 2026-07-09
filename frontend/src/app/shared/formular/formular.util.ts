import { FormGroup } from '@angular/forms';

/**
 * Alle Felder als „beruehrt" markieren, damit Pflicht- und Formatfehler beim
 * Absenden sichtbar werden (auch fuer nie fokussierte Felder). Vor der
 * `invalid`-Pruefung aufrufen.
 */
export function felderAlsBeruehrtMarkieren(form: FormGroup): void {
  form.markAllAsTouched();
}

/**
 * Server-Fehler (aus einem frueheren Absende-Versuch) von allen Feldern
 * entfernen — vor dem naechsten Versuch aufrufen, damit alte Meldungen nicht
 * stehen bleiben.
 */
export function serverFehlerZuruecksetzen(form: FormGroup): void {
  Object.values(form.controls).forEach((c) => {
    const e = c.errors;
    if (e && e['server'] != null) {
      const { server, ...rest } = e as Record<string, unknown>;
      c.setErrors(Object.keys(rest).length ? rest : null);
    }
  });
}
