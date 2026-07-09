import { HttpClient } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { Observable, catchError, finalize, of, shareReplay, tap } from 'rxjs';
import { Me } from './auth.model';

/**
 * Sitzungsstatus des Frontends. Die eigentliche Sitzung ist ein HttpOnly-Cookie
 * (Django-Session) — hier wird NUR das Profil gehalten, kein Token, nichts im
 * Storage. Ein Reload holt den Status über /api/auth/me neu.
 */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly http = inject(HttpClient);

  /** Aktuelles Profil oder null (nicht angemeldet). */
  readonly user = signal<Me | null>(null);
  /** True, sobald ein Profil vorliegt. */
  readonly istAngemeldet = computed(() => this.user() !== null);

  /** Laufende /me-Abfrage, damit parallele Guards nur einen Request auslösen. */
  private ladenInflight?: Observable<Me | null>;

  /**
   * Holt das aktuelle Profil. 401 (nicht angemeldet) ist ein regulärer Zustand,
   * kein Fehler: user wird auf null gesetzt, nichts landet im Log.
   *
   * Parallele Aufrufer (mehrere Guards derselben Navigation, App-Initializer)
   * teilen sich einen einzigen HTTP-Request.
   */
  laden(): Observable<Me | null> {
    if (this.ladenInflight) return this.ladenInflight;
    this.ladenInflight = this.http.get<Me>('/api/auth/me').pipe(
      tap((me) => this.user.set(me)),
      catchError(() => {
        this.user.set(null);
        return of(null);
      }),
      finalize(() => {
        this.ladenInflight = undefined;
      }),
      shareReplay(1),
    );
    return this.ladenInflight;
  }

  /** Anmeldung mit E-Mail + Passwort. Bei Erfolg wird das Profil gesetzt. */
  anmelden(email: string, password: string): Observable<Me> {
    return this.http
      .post<Me>('/api/auth/login', { email, password })
      .pipe(tap((me) => this.user.set(me)));
  }

  /** Meldet ab (idempotent). Das Profil wird lokal in jedem Fall verworfen. */
  abmelden(): Observable<unknown> {
    return this.http.post('/api/auth/logout', {}).pipe(tap(() => this.user.set(null)));
  }

  /** Setzt das csrftoken-Cookie vor dem ersten unsicheren Request (Login). */
  csrfHolen(): Observable<{ csrftoken: string }> {
    return this.http.get<{ csrftoken: string }>('/api/auth/csrf');
  }

  /**
   * Ändert das eigene Passwort. Der Server hält die Sitzung gültig
   * (update_session_auth_hash) — kein erneutes Anmelden nötig. Passwörter
   * werden nie geloggt oder gespeichert.
   *
   * Fehler: 400 (altes Passwort falsch), 422 (neues Passwort zu schwach, mit
   * deutschen Validator-Meldungen).
   */
  passwortAendern(oldPassword: string, newPassword: string): Observable<{ detail: string }> {
    return this.http.post<{ detail: string }>('/api/auth/password', {
      old_password: oldPassword,
      new_password: newPassword,
    });
  }

  /**
   * Prüft, ob das angemeldete Konto ein Recht besitzt. Dient nur dazu, im UI
   * Aktionen auszublenden, die der Server ohnehin mit 403 ablehnen würde — die
   * Durchsetzung liegt beim Server, nicht hier.
   */
  darf(module: string, action: string): boolean {
    const u = this.user();
    if (!u) return false;
    return u.permissions.some((p) => p.module === module && p.action === action);
  }
}
