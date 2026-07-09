import { Component, computed, inject, signal } from '@angular/core';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { filter } from 'rxjs';
import { ThemeService } from './core/theme';
import { AuthService } from './core/auth.service';

interface NavItem {
  path: string;
  label: string;
  /** Kurzkennung fuer die Messkante-Bemaszung. */
  mark: string;
  /** Benötigtes Recht [Modul, Aktion]; ohne Angabe immer sichtbar. */
  recht?: readonly [string, string];
  /** Alternativ: sichtbar, sobald EINES dieser Rechte vorliegt (ODER-Logik). */
  rechtOder?: readonly (readonly [string, string])[];
}

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class App {
  private readonly router = inject(Router);
  protected readonly themeSvc = inject(ThemeService);
  protected readonly auth = inject(AuthService);

  // Rechte-Gates spiegeln die Server-Durchsetzung (permissions.py): das UI
  // blendet aus, was ohnehin mit 403 abgelehnt würde. Übersicht bleibt frei.
  protected readonly nav: NavItem[] = [
    { path: '/uebersicht', label: 'Übersicht', mark: '00' },
    { path: '/kontakte', label: 'Kontakte', mark: '10', recht: ['identity', 'LESEN'] },
    { path: '/liegenschaften', label: 'Liegenschaften', mark: '20', recht: ['property', 'LESEN'] },
    // Begriffe an Hero angelehnt (Wiedererkennung): Projekte/Dokumente statt
    // Vorgänge/Belege — siehe docs/roadmap/00-informationsarchitektur.md.
    { path: '/projekte', label: 'Projekte', mark: '30', recht: ['workflow', 'LESEN'] },
    { path: '/dokumente', label: 'Dokumente', mark: '40', recht: ['invoicing', 'LESEN'] },
    { path: '/planung', label: 'Planung', mark: '50', recht: ['workflow', 'LESEN'] },
    // Wartung liegt fachlich beim Service-/Einsatz-Cluster (wiederkehrende
    // Einsätze) → Zwischenschritt 55 statt Renummerierung der Folgepunkte.
    { path: '/wartung', label: 'Wartung', mark: '55', recht: ['workflow', 'LESEN'] },
    { path: '/aufgaben', label: 'Aufgaben', mark: '60', recht: ['workflow', 'LESEN'] },
    // Personal/HR liegt fachlich zwischen interner Arbeitsorganisation (Aufgaben)
    // und dem Stammdaten-Cluster (Artikel) → Zwischenschritt 65 statt
    // Renummerierung der Folgepunkte.
    { path: '/mitarbeiter', label: 'Mitarbeiter', mark: '65', recht: ['hr', 'LESEN'] },
    { path: '/artikel', label: 'Artikel', mark: '70', recht: ['pricing', 'LESEN'] },
    { path: '/buchhaltung', label: 'Buchhaltung', mark: '80', recht: ['invoicing', 'LESEN'] },
    { path: '/auswertungen', label: 'Auswertungen', mark: '90', recht: ['invoicing', 'LESEN'] },
    // Einstellungen: nur für Rollen, die etwas ändern dürfen (Firmenprofil/
    // Gewerke/Niederlassungen = company/AENDERN, Mahnstufen = invoicing/AENDERN).
    {
      path: '/einstellungen',
      label: 'Einstellungen',
      mark: '95',
      rechtOder: [
        ['company', 'AENDERN'],
        ['invoicing', 'AENDERN'],
      ],
    },
  ];

  /** Nur Navigationspunkte, für die (mindestens) ein Recht vorliegt. */
  protected readonly sichtbareNav = computed(() =>
    this.nav.filter((n) => {
      if (n.recht && !this.auth.darf(n.recht[0], n.recht[1])) return false;
      if (n.rechtOder && !n.rechtOder.some((r) => this.auth.darf(r[0], r[1]))) return false;
      return true;
    }),
  );

  /** Aktuelle URL — Grundlage für die aktive Bemaszungsmarke. */
  private readonly aktuelleUrl = signal('/');

  /** Index des aktiven Punkts innerhalb der sichtbaren Liste. */
  protected readonly activeIndex = computed(() => {
    const url = this.aktuelleUrl();
    const idx = this.sichtbareNav().findIndex((n) => url.startsWith(n.path));
    return idx < 0 ? 0 : idx;
  });

  protected readonly rollenText = computed(() => {
    const rollen = this.auth.user()?.roles ?? [];
    return rollen.length ? rollen.join(' · ') : 'Ohne Rolle';
  });

  protected readonly themeLabel = computed(() =>
    this.themeSvc.theme() === 'dark' ? 'Zu hellem Design wechseln' : 'Zu dunklem Design wechseln',
  );

  constructor() {
    this.themeSvc.init();
    this.aktuelleUrl.set(this.router.url);
    this.router.events
      .pipe(filter((e): e is NavigationEnd => e instanceof NavigationEnd))
      .subscribe((e) => this.aktuelleUrl.set(e.urlAfterRedirects));
  }

  abmelden(): void {
    this.auth.abmelden().subscribe({
      next: () => this.router.navigate(['/login']),
      error: () => this.router.navigate(['/login']),
    });
  }
}
