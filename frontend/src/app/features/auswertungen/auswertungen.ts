import { Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { AuswertungService } from '../../core/auswertungen.service';
import { Dashboard } from '../../core/auswertungen.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; items: Dashboard[] }
  | VerbotenState
  | { kind: 'error' };

@Component({
  selector: 'app-auswertungen',
  imports: [RouterLink, KeinZugriff],
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
      error: (err) => this.state.set(fehlerState(err)),
    });
  }
}
