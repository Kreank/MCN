import { Component } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';

/** Unternavigation des Planung-Bereichs (Einsätze · Plantafel · Kalender). */
@Component({
  selector: 'app-planung-nav',
  imports: [RouterLink, RouterLinkActive],
  template: `
    <nav class="planung-nav" aria-label="Planung-Ansichten">
      <a
        routerLink="/planung"
        routerLinkActive="is-active"
        [routerLinkActiveOptions]="{ exact: true }"
        >Einsätze</a
      >
      <a routerLink="/planung/plantafel" routerLinkActive="is-active">Plantafel</a>
      <a routerLink="/planung/kalender" routerLinkActive="is-active">Kalender</a>
      <a routerLink="/planung/abwesend" routerLinkActive="is-active">Wer fehlt?</a>
      <a routerLink="/planung/einstellungen" routerLinkActive="is-active"
        >Kategorien &amp; Ressourcen</a
      >
    </nav>
  `,
  styles: [
    `
      .planung-nav {
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
      }
    `,
  ],
})
export class PlanungNav {}
