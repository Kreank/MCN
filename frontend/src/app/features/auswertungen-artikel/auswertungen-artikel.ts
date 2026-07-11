import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { AuswertungService, csvDownloadAusloesen } from '../../core/auswertungen.service';
import { ArtikelDashboard } from '../../core/auswertungen.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { MargeBlock } from '../../shared/marge-block/marge-block';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: ArtikelDashboard }
  | VerbotenState
  | { kind: 'error' };

interface Bar {
  label: string;
  display: string;
  widthPct: number;
}

const LINE_TYPE_LABEL: Record<string, string> = {
  MATERIAL: 'Material',
  ARBEITSZEIT: 'Arbeitszeit',
  PAUSCHALE: 'Pauschale',
  FREMDLEISTUNG: 'Fremdleistung',
  FAHRT: 'Fahrt',
  ZUSCHLAG: 'Zuschlag',
};

@Component({
  selector: 'app-auswertungen-artikel',
  imports: [RouterLink, KeinZugriff, MargeBlock],
  templateUrl: './auswertungen-artikel.html',
  styleUrl: './auswertungen-artikel.scss',
})
export class AuswertungenArtikel {
  private readonly svc = inject(AuswertungService);
  private readonly destroyRef = inject(DestroyRef);
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly exportLaeuft = signal(false);
  protected readonly exportMeldung = signal<string | null>(null);

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  /** Top-Positionen nach Nettoumsatz als horizontale Balken (Wert als Text = WCAG). */
  protected readonly artikelBars = computed<Bar[]>(() => {
    const d = this.daten();
    if (!d) return [];
    const max = Math.max(1, ...d.articles.map((a) => Number(a.net_total)));
    return d.articles.map((a) => {
      const value = Number(a.net_total);
      return {
        label: a.description,
        display: this.euro(a.net_total),
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
      .artikelExport()
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
    this.svc.artikel().subscribe({
      next: (data) => this.state.set({ kind: 'ready', data }),
      error: (err) => this.state.set(fehlerState(err)),
    });
  }

  typeLabel(t: string): string {
    return LINE_TYPE_LABEL[t] ?? t;
  }

  /** Menge als deutsche Zahl (bis 3 Nachkommastellen, ohne unnötige Nullen). */
  menge(q: string): string {
    return new Intl.NumberFormat('de-DE', { maximumFractionDigits: 3 }).format(Number(q));
  }

  euro(amount: string | null): string {
    if (amount === null) return '—';
    return new Intl.NumberFormat('de-DE', {
      style: 'currency',
      currency: 'EUR',
    }).format(Number(amount));
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
