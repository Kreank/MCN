import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';
import { AuswertungService } from '../../core/auswertungen.service';
import { Kunden } from '../../core/auswertungen.model';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: Kunden }
  | VerbotenState
  | { kind: 'error' };

interface Bar {
  label: string;
  display: string;
  widthPct: number;
}

@Component({
  selector: 'app-auswertungen-kunden',
  imports: [RouterLink, KeinZugriff],
  templateUrl: './auswertungen-kunden.html',
  styleUrl: './auswertungen-kunden.scss',
})
export class AuswertungenKunden {
  private readonly svc = inject(AuswertungService);
  protected readonly state = signal<ViewState>({ kind: 'loading' });

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  /** Top-Kunden nach Netto-Umsatz als horizontale Balken (Wert als Text = WCAG). */
  protected readonly kundenBars = computed<Bar[]>(() => {
    const d = this.daten();
    if (!d) return [];
    const max = Math.max(1, ...d.customers.map((c) => Number(c.net_total)));
    return d.customers.map((c) => {
      const value = Number(c.net_total);
      return {
        label: c.display_name,
        display: this.euro(c.net_total),
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
    this.svc.kunden().subscribe({
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
}
