import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';
import { AuswertungService, csvDownloadAusloesen } from '../../core/auswertungen.service';
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
  private readonly destroyRef = inject(DestroyRef);
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly exportLaeuft = signal(false);
  protected readonly exportMeldung = signal<string | null>(null);

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

  /** Laedt den CSV-Export mit den AKTUELLEN Filtern (Export = Ansicht). */
  exportieren(): void {
    if (this.exportLaeuft()) return;
    this.exportLaeuft.set(true);
    this.exportMeldung.set(null);
    this.svc
      .kundenExport()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: ({ blob, filename }) => {
          csvDownloadAusloesen(blob, filename);
          this.exportLaeuft.set(false);
          this.exportMeldung.set(`Export „${filename}" wurde heruntergeladen.`);
        },
        error: (err) => {
          this.exportLaeuft.set(false);
          this.exportMeldung.set(fehlerDetail(err) ?? 'Der Export konnte nicht erstellt werden.');
        },
      });
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
