import { Component, computed, inject } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';
import { AuthService } from '../../core/auth.service';

/** Unternavigation der Einstellungen (Firmenprofil · Mahnstufen · Mailversand ·
 * Gewerke · Niederlassungen · Rechte & Rollen).
 *
 * „Rechte & Rollen" wird nur gezeigt, wenn `security/LESEN` vorliegt: die Rolle
 * BUCHHALTUNG sieht das Einstellungen-Nav (über `invoicing/AENDERN`), hat dieses
 * Recht aber nicht — der Link führte sonst auf „Kein Zugriff". */
@Component({
  selector: 'app-einstellungen-nav',
  imports: [RouterLink, RouterLinkActive],
  template: `
    <nav class="einstellungen-nav" aria-label="Einstellungen-Bereiche">
      <a
        routerLink="/einstellungen/profil"
        routerLinkActive="is-active"
        [routerLinkActiveOptions]="{ exact: true }"
        >Firmenprofil</a
      >
      <a routerLink="/einstellungen/mahnstufen" routerLinkActive="is-active">Mahnstufen</a>
      <a routerLink="/einstellungen/mailversand" routerLinkActive="is-active">Mailversand</a>
      <a routerLink="/einstellungen/gewerke" routerLinkActive="is-active">Gewerke</a>
      <a routerLink="/einstellungen/akquisekanaele" routerLinkActive="is-active"
        >Akquisekanäle</a
      >
      <a routerLink="/einstellungen/niederlassungen" routerLinkActive="is-active"
        >Niederlassungen</a
      >
      @if (darfLohngruppen()) {
        <a routerLink="/einstellungen/lohngruppen" routerLinkActive="is-active">Lohngruppen</a>
      }
      @if (darfZeiterfassung()) {
        <a routerLink="/einstellungen/zeiterfassung" routerLinkActive="is-active"
          >Zeiterfassung</a
        >
      }
      @if (darfRechteSehen()) {
        <a routerLink="/einstellungen/rechte" routerLinkActive="is-active">Rechte &amp; Rollen</a>
      }
    </nav>
  `,
  styles: [
    `
      .einstellungen-nav {
        display: flex;
        flex-wrap: wrap;
        gap: var(--space-1);
        margin-bottom: var(--space-5);
        border-bottom: 1.5px solid var(--line);
      }
      a {
        min-height: 40px;
        display: inline-flex;
        align-items: center;
        padding: 0 var(--space-4);
        border-bottom: 2.5px solid transparent;
        color: var(--ink-muted);
        font-size: var(--step--1);
        font-weight: 600;
        text-decoration: none;
        transition: color 0.15s ease, border-color 0.15s ease;
      }
      a:hover {
        color: var(--ink);
      }
      a.is-active {
        color: var(--accent-ink);
        border-bottom-color: var(--accent);
      }
      a:focus-visible {
        outline: 2.5px solid var(--focus-ring);
        outline-offset: 2px;
        border-radius: 2px;
      }
    `,
  ],
})
export class EinstellungenNav {
  private readonly auth = inject(AuthService);
  protected readonly darfRechteSehen = computed(() => this.auth.darf('security', 'LESEN'));
  protected readonly darfLohngruppen = computed(() => this.auth.darf('pricing', 'LESEN'));
  /** `darfAlle`: Kategorien-/Pausenpflege ist mit `require` gesichert (403 bei EIGENE). */
  protected readonly darfZeiterfassung = computed(() => this.auth.darfAlle('hr', 'LESEN'));
}
