import { Component, ElementRef, inject, signal, viewChild } from '@angular/core';
import { RouterLink } from '@angular/router';
import { AuthService } from '../../core/auth.service';

/**
 * „Passwort vergessen?" — Anfrage-Ansicht (anmeldefrei, wie die Anmeldung).
 *
 * Der Server antwortet aus Sicherheitsgründen IMMER gleich (Anti-Enumeration).
 * Diese Seite zeigt deshalb nach dem Absenden eine neutrale Bestätigung, die
 * NIE verrät, ob zu der Adresse ein Konto existiert.
 */
@Component({
  selector: 'app-passwort-vergessen',
  imports: [RouterLink],
  templateUrl: './passwort-vergessen.html',
  styleUrl: './passwort-reset.scss',
})
export class PasswortVergessen {
  private readonly auth = inject(AuthService);

  protected readonly email = signal('');
  protected readonly laedt = signal(false);
  protected readonly gesendet = signal(false);
  protected readonly fehler = signal<string | null>(null);

  private readonly fehlerBox = viewChild<ElementRef<HTMLElement>>('fehlerBox');
  private readonly bestaetigung = viewChild<ElementRef<HTMLElement>>('bestaetigung');

  constructor() {
    // CSRF-Cookie vorab holen, damit der POST den Header mitschicken kann.
    this.auth.csrfHolen().subscribe({ error: () => {} });
  }

  onEmail(wert: string): void {
    this.email.set(wert);
  }

  absenden(): void {
    if (this.laedt()) return;
    const email = this.email().trim();
    if (!email) {
      this.fehlerZeigen('Bitte E-Mail-Adresse eingeben.');
      return;
    }

    this.laedt.set(true);
    this.fehler.set(null);
    this.auth.requestPasswordReset(email).subscribe({
      next: () => {
        this.laedt.set(false);
        // Neutrale Bestätigung — unabhängig davon, ob die Adresse existiert.
        this.gesendet.set(true);
        setTimeout(() => this.bestaetigung()?.nativeElement.focus(), 0);
      },
      error: () => {
        // Ein echter Übertragungsfehler (der Server antwortet sonst stets 200).
        this.laedt.set(false);
        this.fehlerZeigen('Die Anfrage ist fehlgeschlagen. Bitte erneut versuchen.');
      },
    });
  }

  private fehlerZeigen(text: string): void {
    this.fehler.set(text);
    setTimeout(() => this.fehlerBox()?.nativeElement.focus(), 0);
  }
}
