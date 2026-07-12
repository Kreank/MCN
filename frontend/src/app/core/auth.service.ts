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
   * Fordert einen Passwort-Reset-Link an. Der Server antwortet aus Sicherheits-
   * gründen IMMER gleich (200), egal ob die Adresse existiert — die Antwort
   * verrät also nie, ob ein Konto zu dieser E-Mail besteht.
   */
  requestPasswordReset(email: string): Observable<{ detail: string }> {
    return this.http.post<{ detail: string }>('/api/auth/password-reset/request', {
      email,
    });
  }

  /**
   * Setzt das Passwort anhand des Einmal-Links (uid + token) neu. Kein
   * automatisches Anmelden — der Nutzer meldet sich danach neu an.
   *
   * Fehler: 400 (Link ungültig/abgelaufen), 422 (neues Passwort zu schwach, mit
   * deutschen Validator-Meldungen). Passwörter werden nie gespeichert/geloggt.
   */
  confirmPasswordReset(
    uid: string,
    token: string,
    newPassword: string,
  ): Observable<{ detail: string }> {
    return this.http.post<{ detail: string }>('/api/auth/password-reset/confirm', {
      uid,
      token,
      new_password: newPassword,
    });
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

  /**
   * Wie `darf`, verlangt aber row_scope `ALLE`.
   *
   * Der Server ist **fail-closed**: `permissions.require()` wirft 403, sobald
   * der effektive Scope `EIGENE` ist und die Ansicht das nicht umsetzt
   * (`backend/api/permissions.py`). Ein Nav-Punkt, der auf so eine Ansicht
   * zeigt, führte den Benutzer direkt auf „Kein Zugriff".
   *
   * Konkreter Fall: seit Migration 0068 trägt MONTEUR `hr/LESEN` mit Scope
   * EIGENE (er braucht es für die eigene Zeiterfassung). Ohne diese Prüfung
   * erschienen ihm „Mitarbeiter" und die Zeiterfassungs-Verwaltung in der
   * Navigation — beide antworten mit 403.
   */
  darfAlle(module: string, action: string): boolean {
    const u = this.user();
    if (!u) return false;
    return u.permissions.some(
      (p) => p.module === module && p.action === action && p.row_scope === 'ALLE',
    );
  }
}
