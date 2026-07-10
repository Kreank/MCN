import { HttpErrorResponse } from '@angular/common/http';
import { Component, ElementRef, inject, signal, viewChild } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { AuthService } from '../../core/auth.service';

/**
 * „Passwort zurücksetzen" — Bestätigungs-Ansicht (anmeldefrei).
 *
 * Liest `uid` + `token` aus der Query (der Einmal-Link aus der E-Mail) und setzt
 * über /api/auth/password-reset/confirm ein neues Passwort. Kein automatisches
 * Anmelden — nach Erfolg meldet sich der Nutzer neu an.
 *
 * Fehlerbilder:
 *  - Link fehlt/unvollständig oder Server-400 → „ungültig oder abgelaufen" +
 *    Angebot, einen neuen Link anzufordern.
 *  - 422 → Meldung der Passwort-Policy (das Passwort selbst steht nie im Text).
 */
@Component({
  selector: 'app-passwort-zuruecksetzen',
  imports: [RouterLink],
  templateUrl: './passwort-zuruecksetzen.html',
  styleUrl: './passwort-reset.scss',
})
export class PasswortZuruecksetzen {
  private readonly auth = inject(AuthService);
  private readonly route = inject(ActivatedRoute);

  private readonly uid: string;
  private readonly token: string;

  protected readonly passwort = signal('');
  protected readonly wiederholung = signal('');
  protected readonly laedt = signal(false);
  protected readonly fehler = signal<string | null>(null);
  protected readonly feldFehler = signal<string | null>(null);
  protected readonly erfolg = signal(false);
  /** Link fehlt von Anfang an ODER der Server hat ihn als ungültig verworfen. */
  protected readonly linkUngueltig = signal(false);

  private readonly fehlerBox = viewChild<ElementRef<HTMLElement>>('fehlerBox');
  private readonly erfolgBox = viewChild<ElementRef<HTMLElement>>('erfolgBox');

  constructor() {
    const qp = this.route.snapshot.queryParamMap;
    this.uid = (qp.get('uid') ?? '').trim();
    this.token = (qp.get('token') ?? '').trim();
    if (!this.uid || !this.token) {
      this.linkUngueltig.set(true);
    }
    // CSRF-Cookie vorab holen, damit der POST den Header mitschicken kann.
    this.auth.csrfHolen().subscribe({ error: () => {} });
  }

  onPasswort(wert: string): void {
    this.passwort.set(wert);
  }
  onWiederholung(wert: string): void {
    this.wiederholung.set(wert);
  }

  absenden(): void {
    if (this.laedt() || this.linkUngueltig()) return;
    const pw = this.passwort();
    const wdh = this.wiederholung();

    this.feldFehler.set(null);
    this.fehler.set(null);

    if (!pw || !wdh) {
      this.fehlerZeigen('Bitte das neue Passwort zweimal eingeben.');
      return;
    }
    if (pw !== wdh) {
      this.feldFehler.set('Die beiden Passwörter stimmen nicht überein.');
      return;
    }

    this.laedt.set(true);
    this.auth.confirmPasswordReset(this.uid, this.token, pw).subscribe({
      next: () => {
        this.laedt.set(false);
        this.erfolg.set(true);
        setTimeout(() => this.erfolgBox()?.nativeElement.focus(), 0);
      },
      error: (err: HttpErrorResponse) => {
        this.laedt.set(false);
        if (err.status === 400) {
          // Einheitliche Server-Meldung: Link ungültig/abgelaufen.
          this.linkUngueltig.set(true);
          return;
        }
        const detail = (err.error as { detail?: unknown } | null)?.detail;
        if (err.status === 422 && typeof detail === 'string' && detail.trim()) {
          // Passwort-Policy (z. B. „mindestens 12 Zeichen").
          this.feldFehler.set(detail);
          return;
        }
        this.fehlerZeigen(
          err.status === 0
            ? 'Keine Verbindung zum Server. Bitte erneut versuchen.'
            : 'Das Zurücksetzen ist fehlgeschlagen. Bitte erneut versuchen.',
        );
      },
    });
  }

  private fehlerZeigen(text: string): void {
    this.fehler.set(text);
    setTimeout(() => this.fehlerBox()?.nativeElement.focus(), 0);
  }
}
