import { HttpErrorResponse } from '@angular/common/http';
import { FormGroup } from '@angular/forms';
import { fehlerDetail, istVerboten } from '../http-fehler';

/**
 * Ergebnis der Fehlerzuordnung: die formularweite Meldung (fuer eine
 * `role="alert"`-Flaeche ueber dem Formular) oder `null`, wenn alle Fehler auf
 * einzelne Felder verteilt werden konnten.
 */
export interface ApiFehlerErgebnis {
  formular: string | null;
}

/**
 * Ordnet eine Server-Fehlerantwort einem Formular zu. Kennt beide Formen, die
 * das Backend liefert:
 *
 *  - **Pydantic-422** `{"detail": [{"loc": ["body","payload","feld"], "msg": …}]}`
 *    -> Feldfehler auf das passende `FormControl` (via letztes loc-Segment, das
 *    einem Feld entspricht). Nicht zuordenbare Meldungen wandern in die
 *    Formularmeldung.
 *  - **Freitext-422** `{"detail": "…"}` (unsere `HttpError(422, str(exc))` aus
 *    der Service-Schicht — der haeufigste Fall) -> Formularmeldung.
 *  - **403** -> „Keine Berechtigung" (Servermeldung, sonst Standardtext).
 *  - Sonstiges / Netzwerk -> generische Meldung.
 *
 * Setzt Feldfehler als `{ server: msg }` und markiert die Felder als beruehrt,
 * damit die Meldung sofort erscheint. `serverFehlerZuruecksetzen()` (util)
 * raeumt sie vor dem naechsten Versuch wieder ab.
 */
export function apiFehlerZuweisen(err: unknown, form: FormGroup): ApiFehlerErgebnis {
  if (istVerboten(err)) {
    return { formular: fehlerDetail(err) ?? 'Keine Berechtigung für diese Aktion.' };
  }

  if (err instanceof HttpErrorResponse) {
    const detail = (err.error as { detail?: unknown } | null)?.detail;

    if (err.status === 422 && Array.isArray(detail)) {
      return { formular: pydanticVerteilen(detail, form) };
    }
    if (typeof detail === 'string' && detail.trim()) {
      return { formular: detail };
    }
    if (err.status === 0) {
      return { formular: 'Keine Verbindung zum Server. Bitte erneut versuchen.' };
    }
  }

  return { formular: 'Die Aktion ist fehlgeschlagen. Bitte erneut versuchen.' };
}

function pydanticVerteilen(detail: unknown[], form: FormGroup): string | null {
  const rest: string[] = [];
  for (const item of detail) {
    const eintrag = item as { loc?: unknown; msg?: unknown };
    const loc = Array.isArray(eintrag.loc) ? eintrag.loc : [];
    const msg =
      typeof eintrag.msg === 'string' && eintrag.msg.trim()
        ? eintrag.msg
        : 'Ungültige Eingabe.';
    const feld = feldAusLoc(loc, form);
    if (feld) {
      const control = form.get(feld)!;
      control.setErrors({ ...(control.errors ?? {}), server: msg });
      control.markAsTouched();
    } else {
      rest.push(msg);
    }
  }
  return rest.length ? rest.join(' ') : null;
}

/** Letztes loc-Segment, das einem Formularfeld entspricht (z. B. 'title'). */
function feldAusLoc(loc: unknown[], form: FormGroup): string | null {
  for (let i = loc.length - 1; i >= 0; i -= 1) {
    const seg = loc[i];
    if (typeof seg === 'string' && form.get(seg)) return seg;
  }
  return null;
}
