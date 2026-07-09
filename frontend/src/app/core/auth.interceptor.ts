import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';
import { AuthService } from './auth.service';

/** Methoden, für die Django ein CSRF-Token verlangt (Double-Submit). */
const UNSICHERE_METHODEN = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

/** Liest das (nicht-HttpOnly) csrftoken-Cookie aus. */
function csrftokenAusCookie(): string | null {
  const treffer = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
  return treffer ? decodeURIComponent(treffer[1]) : null;
}

/**
 * Zentrale HTTP-Regeln für die Sitzung:
 *  - Cookies auf JEDEN Request (Session + CSRF-Cookie),
 *  - X-CSRFToken-Header bei unsicheren Methoden,
 *  - 401 (außerhalb /api/auth/*): Profil verwerfen und zur Anmeldung leiten,
 *  - 403: unangetastet durchreichen — die Seite zeigt die Servermeldung.
 */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);
  const router = inject(Router);

  let angepasst = req.clone({ withCredentials: true });
  if (UNSICHERE_METHODEN.has(req.method.toUpperCase())) {
    const token = csrftokenAusCookie();
    if (token) {
      angepasst = angepasst.clone({ setHeaders: { 'X-CSRFToken': token } });
    }
  }

  const istAuthEndpunkt = req.url.includes('/api/auth/');

  return next(angepasst).pipe(
    catchError((fehler: HttpErrorResponse) => {
      if (fehler.status === 401 && !istAuthEndpunkt) {
        // Sitzung abgelaufen/fehlt: lokalen Zustand leeren und umleiten.
        auth.user.set(null);
        const returnUrl = router.url;
        void router.navigate(['/login'], {
          queryParams: returnUrl && returnUrl !== '/login' ? { returnUrl } : {},
        });
      }
      // 403 und alles andere unverändert weiterreichen.
      return throwError(() => fehler);
    }),
  );
};
