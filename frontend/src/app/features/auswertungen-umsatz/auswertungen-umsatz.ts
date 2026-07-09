import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { AuswertungService } from '../../core/auswertungen.service';
import { UmsatzProjekt } from '../../core/auswertungen.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: UmsatzProjekt }
  | VerbotenState
  | { kind: 'error' };

interface Bar {
  label: string;
  value: number;
  display: string;
  heightPct: number;
}

@Component({
  selector: 'app-auswertungen-umsatz',
  imports: [RouterLink, KeinZugriff],
  templateUrl: './auswertungen-umsatz.html',
  styleUrl: './auswertungen-umsatz.scss',
})
export class AuswertungenUmsatz {
  private readonly svc = inject(AuswertungService);
  protected readonly state = signal<ViewState>({ kind: 'loading' });

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  /** Umsatzverlauf als Balken (Einzelserie; Werte als Text = WCAG). */
  protected readonly umsatzBars = computed<Bar[]>(() => {
    const d = this.daten();
    if (!d) return [];
    const max = Math.max(1, ...d.timeline.map((t) => Number(t.net)));
    return d.timeline.map((t) => {
      const value = Number(t.net);
      return {
        label: this.monthLabel(t.month),
        value,
        display: this.euro(t.net),
        heightPct: Math.max(value > 0 ? 4 : 0, (value / max) * 100),
      };
    });
  });

  /** Projekte nach Gewerk als horizontale Balken. */
  protected readonly gewerkBars = computed<Bar[]>(() => {
    const d = this.daten();
    if (!d) return [];
    const max = Math.max(1, ...d.projects.by_gewerk.map((g) => g.count));
    return d.projects.by_gewerk.map((g) => ({
      label: g.name,
      value: g.count,
      display: String(g.count),
      heightPct: Math.max(g.count > 0 ? 6 : 0, (g.count / max) * 100),
    }));
  });

  constructor() {
    this.load();
  }

  retry(): void {
    this.load();
  }

  private load(): void {
    this.state.set({ kind: 'loading' });
    this.svc.umsatzProjektuebersicht().subscribe({
      next: (data) => this.state.set({ kind: 'ready', data }),
      error: (err) => this.state.set(fehlerState(err)),
    });
  }

  euro(amount: string | null): string {
    if (amount === null) return '—';
    return new Intl.NumberFormat('de-DE', {
      style: 'currency',
      currency: 'EUR',
    }).format(Number(amount));
  }

  monthLabel(m: string): string {
    const [y, mo] = m.split('-').map(Number);
    if (!y || !mo) return m;
    return new Date(y, mo - 1, 1).toLocaleDateString('de-DE', {
      month: 'short',
      year: 'numeric',
    });
  }
}
