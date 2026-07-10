import { Component } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';

/** Unternavigation des Projekte-Bereichs (Register-Liste · Vorgangs-Board). */
@Component({
  selector: 'app-projekte-nav',
  imports: [RouterLink, RouterLinkActive],
  template: `
    <nav class="projekte-nav" aria-label="Projekte-Ansichten">
      <a
        routerLink="/projekte"
        routerLinkActive="is-active"
        [routerLinkActiveOptions]="{ exact: true }"
        >Register</a
      >
      <a routerLink="/projekte/kanban" routerLinkActive="is-active">Vorgang-Board</a>
    </nav>
  `,
  styles: [
    `
      .projekte-nav {
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
export class ProjekteNav {}
