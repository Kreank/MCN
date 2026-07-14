import { Component, ElementRef, inject, signal, viewChild } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { AuthService } from '../../core/auth.service';
import { nurAlleFuerPfad, rechtFuerPfad } from '../../core/bereiche';

/**
 * Anmeldeseite — schlankes, eigenes Layout ohne Bereichsnavigation.
 * E-Mail + Passwort gegen /api/auth/login; die Sitzung ist ein Cookie.
 */
@Component({
  selector: 'app-login',
  imports: [RouterLink],
  templateUrl: './login.html',
  styleUrl: './login.scss',
})
export class Login {
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  protected readonly email = signal('');
  protected readonly password = signal('');
  protected readonly laedt = signal(false);
  protected readonly fehler = signal<string | null>(null);

  private readonly fehlerBox = viewChild<ElementRef<HTMLElement>>('fehlerBox');

  constructor() {
    // CSRF-Cookie vorab holen, damit der Login-POST den Header mitschicken kann.
    this.auth.csrfHolen().subscribe({ error: () => {} });
  }

  onEmail(wert: string): void {
    this.email.set(wert);
  }
  onPassword(wert: string): void {
    this.password.set(wert);
  }

  absenden(): void {
    if (this.laedt()) return;
    const email = this.email().trim();
    const password = this.password();
    if (!email || !password) {
      this.fehlerZeigen('Bitte E-Mail-Adresse und Passwort eingeben.');
      return;
    }

    this.laedt.set(true);
    this.fehler.set(null);
    this.auth.anmelden(email, password).subscribe({
      next: () => {
        this.laedt.set(false);
        this.router.navigateByUrl(this.zielUrl());
      },
      error: (err) => {
        this.laedt.set(false);
        const detail = err?.error?.detail;
        this.fehlerZeigen(
          typeof detail === 'string' && detail.trim()
            ? detail
            : 'Anmeldung fehlgeschlagen. Bitte erneut versuchen.',
        );
      },
    });
  }

  /**
   * Sicheres Rücksprungziel: nur interne Pfade, niemals zurück auf /login und
   * niemals in einen Bereich, für den das Recht fehlt (sonst landete der Nutzer
   * direkt nach dem Login auf /kein-zugriff). In beiden Fällen: Übersicht.
   */
  private zielUrl(): string {
    const ret = this.route.snapshot.queryParamMap.get('returnUrl');
    if (ret && ret.startsWith('/') && !ret.startsWith('//') && !ret.startsWith('/login')) {
      const recht = rechtFuerPfad(ret);
      // Manche Bereiche verlangen row_scope ALLE (Buchhaltung, Auswertungen,
      // Rechnungsmappe, Personal): Ein Konto mit EIGENE bekäme dort 403. Der
      // Rücksprung muss dieselbe Regel rechnen wie der Route-Guard — sonst landet
      // der Monteur direkt nach dem Login auf „Kein Zugriff".
      const erlaubt = !recht
        ? true
        : nurAlleFuerPfad(ret)
          ? this.auth.darfAlle(recht[0], recht[1])
          : this.auth.darf(recht[0], recht[1]);
      if (erlaubt) {
        return ret;
      }
    }
    return '/uebersicht';
  }

  private fehlerZeigen(text: string): void {
    this.fehler.set(text);
    // Nach dem Rendern in den Alert-Container fokussieren (Screenreader + Sicht).
    setTimeout(() => this.fehlerBox()?.nativeElement.focus(), 0);
  }
}
