import { inject } from '@angular/core';
import { CanActivateFn, Router, UrlTree } from '@angular/router';
import { map } from 'rxjs';
import { AuthService } from './auth.service';

/**
 * Schützt Routen: nur angemeldete Konten kommen durch. Ist der Status noch
 * unbekannt (frischer Reload), wird einmal /api/auth/me abgewartet, bevor
 * entschieden wird — so springt ein Reload nicht kurz auf /login.
 */
export const authGuard: CanActivateFn = (_route, state) => {
  const auth = inject(AuthService);
  const router = inject(Router);

  const anmeldung = () =>
    router.createUrlTree(['/login'], { queryParams: { returnUrl: state.url } });

  if (auth.istAngemeldet()) {
    return true;
  }
  return auth.laden().pipe(map(() => (auth.istAngemeldet() ? true : anmeldung())));
};

/**
 * Rechte-Guard: verlangt neben der Anmeldung ein konkretes Recht (Modul +
 * Aktion). Fehlt die Anmeldung → /login; fehlt nur das Recht → /kein-zugriff.
 * Das spiegelt die Server-Durchsetzung und schließt die Lücke, die reine
 * Nav-Ausblendung offen lässt (direkte URL-Eingabe, returnUrl).
 */
export function darfGuard(module: string, action: string): CanActivateFn {
  return (_route, state) => {
    const auth = inject(AuthService);
    const router = inject(Router);

    const entscheide = (): boolean | UrlTree => {
      if (!auth.istAngemeldet()) {
        return router.createUrlTree(['/login'], { queryParams: { returnUrl: state.url } });
      }
      return auth.darf(module, action) ? true : router.createUrlTree(['/kein-zugriff']);
    };

    if (auth.istAngemeldet()) {
      return entscheide();
    }
    return auth.laden().pipe(map(() => entscheide()));
  };
}

/**
 * Wie `darfGuard`, verlangt aber **row_scope ALLE**.
 *
 * Für Ansichten, die den Zeilen-Scope nicht auswerten: Der Server ist dort
 * fail-closed (`permissions.require` → 403 bei EIGENE), die Route führte also
 * zwangsläufig auf einen Fehler. Seit Migration 0102 ist das keine Theorie mehr —
 * MONTEUR trägt `invoicing/LESEN` mit Scope EIGENE (Angebot ohne Preise) und käme
 * sonst über Buchhaltung, Mahnwesen, Auswertungen und die Rechnungsmappe genau
 * dorthin. Spiegelt die Server-Durchsetzung, setzt sie nicht durch.
 */
export function darfAlleGuard(module: string, action: string): CanActivateFn {
  return (_route, state) => {
    const auth = inject(AuthService);
    const router = inject(Router);

    const entscheide = (): boolean | UrlTree => {
      if (!auth.istAngemeldet()) {
        return router.createUrlTree(['/login'], { queryParams: { returnUrl: state.url } });
      }
      return auth.darfAlle(module, action) ? true : router.createUrlTree(['/kein-zugriff']);
    };

    if (auth.istAngemeldet()) {
      return entscheide();
    }
    return auth.laden().pipe(map(() => entscheide()));
  };
}
