import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { ArtikelService } from '../../core/artikel.service';
import { ArticleDetail, ArticleLineType, StammStatus } from '../../core/artikel.model';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: ArticleDetail }
  | { kind: 'error' };

@Component({
  selector: 'app-artikel-detail',
  imports: [Mappe, RouterLink],
  templateUrl: './artikel-detail.html',
  styleUrl: './artikel-detail.scss',
})
export class ArtikelDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(ArtikelService);

  protected readonly tab = signal('stammdaten');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  protected readonly tabs: MappeTab[] = [
    { id: 'stammdaten', label: 'Stammdaten' },
    { id: 'preis', label: 'Preis' },
  ];

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.tab.set('stammdaten');
      if (!id) {
        this.state.set({ kind: 'error' });
        return;
      }
      this.load(id);
    });
  }

  retry(): void {
    const id = this.route.snapshot.paramMap.get('id');
    if (id) this.load(id);
  }

  private load(id: string): void {
    const rid = ++this.reqId;
    this.state.set({ kind: 'loading' });
    this.svc.getArticle(id).subscribe({
      next: (data) => {
        if (rid === this.reqId) this.state.set({ kind: 'ready', data });
      },
      error: () => {
        if (rid === this.reqId) this.state.set({ kind: 'error' });
      },
    });
  }

  euro(amount: string | null): string {
    if (amount === null) return '—';
    return new Intl.NumberFormat('de-DE', {
      style: 'currency',
      currency: 'EUR',
    }).format(Number(amount));
  }

  lineTypeLabel(t: ArticleLineType): string {
    const map: Record<ArticleLineType, string> = {
      MATERIAL: 'Material',
      ARBEITSZEIT: 'Arbeitszeit',
      PAUSCHALE: 'Pauschale',
      FREMDLEISTUNG: 'Fremdleistung',
      FAHRT: 'Fahrt',
      ZUSCHLAG: 'Zuschlag',
    };
    return map[t] ?? t;
  }

  statusLabel(s: StammStatus): string {
    return s === 'AKTIV' ? 'Aktiv' : 'Inaktiv';
  }
  statusClass(s: StammStatus): string {
    return s === 'AKTIV' ? 'stamp--positive' : '';
  }
}
