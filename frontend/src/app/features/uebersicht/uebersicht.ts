import { Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';
import { AufgabeService } from '../../core/aufgabe.service';
import { ProjektService } from '../../core/projekt.service';
import { BelegService } from '../../core/beleg.service';
import { Task } from '../../core/aufgabe.model';
import { Project } from '../../core/projekt.model';
import { Quote } from '../../core/beleg.model';

type Tile<T> =
  | { kind: 'loading' }
  | { kind: 'ready'; total: number; items: T[] }
  | VerbotenState
  | { kind: 'error' };

@Component({
  selector: 'app-uebersicht',
  imports: [RouterLink],
  templateUrl: './uebersicht.html',
  styleUrl: './uebersicht.scss',
})
export class Uebersicht {
  private readonly aufgabeSvc = inject(AufgabeService);
  private readonly projektSvc = inject(ProjektService);
  private readonly belegSvc = inject(BelegService);

  protected readonly tasks = signal<Tile<Task>>({ kind: 'loading' });
  protected readonly projects = signal<Tile<Project>>({ kind: 'loading' });
  protected readonly quotes = signal<Tile<Quote>>({ kind: 'loading' });

  constructor() {
    this.aufgabeSvc.list({ page: 1, page_size: 5, status: 'OFFEN' }).subscribe({
      next: (d) => this.tasks.set({ kind: 'ready', total: d.total, items: d.items }),
      error: (err) => this.tasks.set(fehlerState(err)),
    });
    this.projektSvc.list({ page: 1, page_size: 5, status: 'OPEN' }).subscribe({
      next: (d) => this.projects.set({ kind: 'ready', total: d.total, items: d.items }),
      error: (err) => this.projects.set(fehlerState(err)),
    });
    this.belegSvc.list({ page: 1, page_size: 5, status: 'ENTWURF' }).subscribe({
      next: (d) => this.quotes.set({ kind: 'ready', total: d.total, items: d.items }),
      error: (err) => this.quotes.set(fehlerState(err)),
    });
  }

  euro(amount: string | null): string {
    if (amount === null) return '—';
    return new Intl.NumberFormat('de-DE', {
      style: 'currency',
      currency: 'EUR',
    }).format(Number(amount));
  }
}
