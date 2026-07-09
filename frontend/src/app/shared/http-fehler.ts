import { HttpErrorResponse } from '@angular/common/http';

/**
 * Gemeinsame Klassifizierung von HTTP-Fehlern für die Feature-ViewStates.
 *
 * Ein 403 ist KEIN Netzwerkfehler: die Verbindung stand, der Server hat die
 * Berechtigung verweigert. Wiederholen hilft nicht — deshalb ein eigener
 * ViewState-Zweig 'forbidden' (ohne „Erneut versuchen") statt 'error'.
 */

/** Der 403-Zweig, den betroffene ViewState-Unions mit aufnehmen. */
export interface VerbotenState {
  kind: 'forbidden';
  detail: string | null;
}

/** True, wenn der Server die Berechtigung verweigert hat (403). */
export function istVerboten(err: unknown): boolean {
  return err instanceof HttpErrorResponse && err.status === 403;
}

/** Die deutsche `detail`-Meldung des Servers, sofern vorhanden. */
export function fehlerDetail(err: unknown): string | null {
  if (err instanceof HttpErrorResponse) {
    const detail = (err.error as { detail?: unknown } | null)?.detail;
    if (typeof detail === 'string' && detail.trim()) return detail;
  }
  return null;
}

/**
 * Bildet einen HTTP-Fehler auf den passenden ViewState-Zweig ab: 403 →
 * 'forbidden' (mit Servermeldung), alles andere → 'error'.
 */
export function fehlerState(err: unknown): VerbotenState | { kind: 'error' } {
  return istVerboten(err) ? { kind: 'forbidden', detail: fehlerDetail(err) } : { kind: 'error' };
}
