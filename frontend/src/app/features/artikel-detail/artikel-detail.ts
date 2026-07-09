import { Component, computed, effect, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { ArtikelService } from '../../core/artikel.service';
import {
  ArticleDetail,
  ArticleKalkulation,
  ArticleLineType,
  StammStatus,
} from '../../core/artikel.model';

type KalkState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; data: ArticleKalkulation }
  | { kind: 'error' };

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
    { id: 'kalkulation', label: 'Kalkulation' },
  ];

  protected readonly kalk = signal<KalkState>({ kind: 'idle' });
  private kalkReqId = 0;

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.tab.set('stammdaten');
      this.kalk.set({ kind: 'idle' });
      if (!id) {
        this.state.set({ kind: 'error' });
        return;
      }
      this.load(id);
    });

    // Kalkulation erst beim Öffnen des Tabs nachladen (einmal je Artikel).
    effect(() => {
      const art = this.daten();
      if (this.tab() === 'kalkulation' && art && this.kalk().kind === 'idle') {
        this.loadKalk(art.id);
      }
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

  retryKalk(): void {
    const art = this.daten();
    if (art) this.loadKalk(art.id);
  }

  private loadKalk(id: string): void {
    const rid = ++this.kalkReqId;
    this.kalk.set({ kind: 'loading' });
    this.svc.getKalkulation(id).subscribe({
      next: (data) => {
        if (rid === this.kalkReqId) this.kalk.set({ kind: 'ready', data });
      },
      error: () => {
        if (rid === this.kalkReqId) this.kalk.set({ kind: 'error' });
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

  basisLabel(b: string | null): string {
    if (b === 'EK') return 'Einkaufspreis';
    if (b === 'LISTENPREIS') return 'Listenpreis';
    return '—';
  }
  operatorLabel(o: string | null): string {
    if (o === 'AUFSCHLAG') return 'Aufschlag';
    if (o === 'ABSCHLAG') return 'Abschlag';
    return '';
  }
  formelText(v: {
    kind: string;
    basis_kind: string | null;
    operator: string | null;
    percent_change: string | null;
    amount_change: string | null;
  }): string {
    if (v.kind === 'FESTPREIS') return 'Festpreis';
    const change = v.percent_change !== null ? `${Number(v.percent_change)} %` : this.euro(v.amount_change);
    return `${this.basisLabel(v.basis_kind)} ${this.operatorLabel(v.operator)} ${change}`;
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
