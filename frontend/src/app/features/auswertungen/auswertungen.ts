import { Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { AuswertungService } from '../../core/auswertungen.service';
import { Dashboard } from '../../core/auswertungen.model';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; items: Dashboard[] }
  | { kind: 'error' };

@Component({
  selector: 'app-auswertungen',
  imports: [RouterLink],
  templateUrl: './auswertungen.html',
  styleUrl: './auswertungen.scss',
})
export class Auswertungen {
  private readonly svc = inject(AuswertungService);
  protected readonly state = signal<ViewState>({ kind: 'loading' });

  constructor() {
    this.load();
  }

  retry(): void {
    this.load();
  }

  private load(): void {
    this.state.set({ kind: 'loading' });
    this.svc.listDashboards().subscribe({
      next: (items) => this.state.set({ kind: 'ready', items }),
      error: () => this.state.set({ kind: 'error' }),
    });
  }
}
