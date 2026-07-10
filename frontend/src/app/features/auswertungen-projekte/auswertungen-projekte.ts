import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { AuswertungService } from '../../core/auswertungen.service';
import { ProjekteDashboard } from '../../core/auswertungen.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: ProjekteDashboard }
  | VerbotenState
  | { kind: 'error' };

interface Bar {
  label: string;
  display: string;
  widthPct: number;
}

const STATUS_LABEL: Record<string, string> = {
  OPEN: 'Offen',
  CLOSED: 'Abgeschlossen',
};

@Component({
  selector: 'app-auswertungen-projekte',
  imports: [RouterLink, KeinZugriff],
  templateUrl: './auswertungen-projekte.html',
  styleUrl: './auswertungen-projekte.scss',
})
export class AuswertungenProjekte {
  private readonly svc = inject(AuswertungService);
  protected readonly state = signal<ViewState>({ kind: 'loading' });

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  /** Top-Projekte nach Nettoumsatz als horizontale Balken (Wert als Text = WCAG). */
  protected readonly topBars = computed<Bar[]>(() => {
    const d = this.daten();
    if (!d) return [];
    const max = Math.max(1, ...d.top_projects.map((p) => Number(p.net_total)));
    return d.top_projects.map((p) => {
      const value = Number(p.net_total);
      return {
        label: p.name,
        display: this.euro(p.net_total),
        widthPct: Math.max(value > 0 ? 4 : 0, (value / max) * 100),
      };
    });
  });

  constructor() {
    this.load();
  }

  retry(): void {
    this.load();
  }

  private load(): void {
    this.state.set({ kind: 'loading' });
    this.svc.projekte().subscribe({
      next: (data) => this.state.set({ kind: 'ready', data }),
      error: (err) => this.state.set(fehlerState(err)),
    });
  }

  statusLabel(s: string): string {
    return STATUS_LABEL[s] ?? s;
  }

  /** Angenäherte Tageszahl als deutsche Ganzzahl, oder „—" wenn keine Daten. */
  days(n: number | null): string {
    if (n === null) return '—';
    return new Intl.NumberFormat('de-DE', { maximumFractionDigits: 0 }).format(n) + ' Tage';
  }

  euro(amount: string | null): string {
    if (amount === null) return '—';
    return new Intl.NumberFormat('de-DE', {
      style: 'currency',
      currency: 'EUR',
    }).format(Number(amount));
  }
}
