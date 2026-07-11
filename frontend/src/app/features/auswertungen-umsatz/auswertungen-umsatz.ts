import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { AuswertungService, csvDownloadAusloesen } from '../../core/auswertungen.service';
import { UmsatzProjekt } from '../../core/auswertungen.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { MargeBlock } from '../../shared/marge-block/marge-block';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';

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
  imports: [RouterLink, KeinZugriff, MargeBlock],
  templateUrl: './auswertungen-umsatz.html',
  styleUrl: './auswertungen-umsatz.scss',
})
export class AuswertungenUmsatz {
  private readonly svc = inject(AuswertungService);
  private readonly destroyRef = inject(DestroyRef);
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly exportLaeuft = signal(false);
  protected readonly exportMeldung = signal<string | null>(null);

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

  /** Laedt den CSV-Export mit den AKTUELLEN Filtern (Export = Ansicht). */
  exportieren(): void {
    if (this.exportLaeuft()) return;
    this.exportLaeuft.set(true);
    this.exportMeldung.set(null);
    this.svc
      .umsatzExport()
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

  /** Marge% (de-DE) oder das Wort „unbekannt" — nie eine erfundene 0. */
  prozent(p: string | null): string {
    if (p === null) return 'unbekannt';
    return (
      new Intl.NumberFormat('de-DE', {
        minimumFractionDigits: 1,
        maximumFractionDigits: 2,
      }).format(Number(p)) + ' %'
    );
  }
}
