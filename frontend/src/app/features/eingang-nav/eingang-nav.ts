import { Component } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';

/** Unternavigation des Eingangs (Liste · Board) — der Vorgangs-Eingangskorb. */
@Component({
  selector: 'app-eingang-nav',
  imports: [RouterLink, RouterLinkActive],
  template: `
    <nav class="eingang-nav" aria-label="Eingang-Ansichten">
      <a
        routerLink="/eingang"
        routerLinkActive="is-active"
        [routerLinkActiveOptions]="{ exact: true }"
        >Eingang</a
      >
      <a routerLink="/eingang/board" routerLinkActive="is-active">Board</a>
    </nav>
  `,
  styles: [
    `
      .eingang-nav {
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
export class EingangNav {}
