import { Component, computed, inject, signal } from '@angular/core';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { filter } from 'rxjs';
import { ThemeService } from './core/theme';

interface NavItem {
  path: string;
  label: string;
  /** Kurzkennung fuer die Messkante-Bemaszung. */
  mark: string;
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

  protected readonly nav: NavItem[] = [
    { path: '/uebersicht', label: 'Übersicht', mark: '00' },
    { path: '/kontakte', label: 'Kontakte', mark: '10' },
    { path: '/liegenschaften', label: 'Liegenschaften', mark: '20' },
    // Begriffe an Hero angelehnt (Wiedererkennung): Projekte/Dokumente statt
    // Vorgänge/Belege — siehe docs/roadmap/00-informationsarchitektur.md.
    { path: '/projekte', label: 'Projekte', mark: '30' },
    { path: '/dokumente', label: 'Dokumente', mark: '40' },
    { path: '/planung', label: 'Planung', mark: '50' },
    // Wartung liegt fachlich beim Service-/Einsatz-Cluster (wiederkehrende
    // Einsätze) → Zwischenschritt 55 statt Renummerierung der Folgepunkte.
    { path: '/wartung', label: 'Wartung', mark: '55' },
    { path: '/aufgaben', label: 'Aufgaben', mark: '60' },
    // Personal/HR liegt fachlich zwischen interner Arbeitsorganisation (Aufgaben)
    // und dem Stammdaten-Cluster (Artikel) → Zwischenschritt 65 statt
    // Renummerierung der Folgepunkte.
    { path: '/mitarbeiter', label: 'Mitarbeiter', mark: '65' },
    { path: '/artikel', label: 'Artikel', mark: '70' },
    { path: '/buchhaltung', label: 'Buchhaltung', mark: '80' },
    { path: '/auswertungen', label: 'Auswertungen', mark: '90' },
  ];

  /** Index des aktiven Navigationspunkts — steuert die Bemaszungsmarke. */
  protected readonly activeIndex = signal(0);

  protected readonly themeLabel = computed(() =>
    this.themeSvc.theme() === 'dark' ? 'Zu hellem Design wechseln' : 'Zu dunklem Design wechseln',
  );

  constructor() {
    this.themeSvc.init();
    this.syncActive(this.router.url);
    this.router.events
      .pipe(filter((e): e is NavigationEnd => e instanceof NavigationEnd))
      .subscribe((e) => this.syncActive(e.urlAfterRedirects));
  }

  private syncActive(url: string): void {
    const idx = this.nav.findIndex((n) => url.startsWith(n.path));
    this.activeIndex.set(idx < 0 ? 0 : idx);
  }
}
