import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { Subject, debounceTime, distinctUntilChanged } from 'rxjs';
import { BelegService } from '../../core/beleg.service';
import { Quote, QuotePage, QuoteStatus } from '../../core/beleg.model';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: QuotePage }
  | { kind: 'error' };

type Segment = { value: QuoteStatus | null; label: string };

@Component({
  selector: 'app-dokumente',
  imports: [RouterLink],
  templateUrl: './dokumente.html',
  styleUrl: './dokumente.scss',
})
export class Dokumente {
  private readonly svc = inject(BelegService);

  protected readonly pageSize = 20;
  protected readonly segments: Segment[] = [
    { value: null, label: 'Alle' },
    { value: 'ENTWURF', label: 'Entwurf' },
    { value: 'VERSENDET', label: 'Versendet' },
    { value: 'ANGENOMMEN', label: 'Angenommen' },
    { value: 'ABGELEHNT', label: 'Abgelehnt' },
  ];

  protected readonly query = signal('');
  protected readonly status = signal<QuoteStatus | null>(null);
  protected readonly page = signal(1);
  protected readonly state = signal<ViewState>({ kind: 'loading' });

  protected readonly skeletons = Array.from({ length: 6 });

  private readonly searchInput$ = new Subject<string>();
  private reqId = 0;

  protected readonly totalPages = computed(() => {
    const s = this.state();
    if (s.kind !== 'ready') return 1;
    return Math.max(1, Math.ceil(s.data.total / s.data.page_size));
  });

  protected readonly resultSummary = computed(() => {
    const s = this.state();
    if (s.kind === 'loading') return 'Belege werden geladen.';
    if (s.kind === 'error') return 'Belege konnten nicht geladen werden.';
    const t = s.data.total;
    if (t === 0) return 'Keine Belege gefunden.';
    return `${t} ${t === 1 ? 'Beleg' : 'Belege'} gefunden, Seite ${s.data.page} von ${this.totalPages()}.`;
  });

  constructor() {
    this.searchInput$
      .pipe(debounceTime(300), distinctUntilChanged(), takeUntilDestroyed())
      .subscribe((v) => {
        this.query.set(v);
        this.page.set(1);
        this.fetch();
      });
    this.fetch();
  }

  onSearch(value: string): void {
    this.searchInput$.next(value);
  }

  selectSegment(value: QuoteStatus | null): void {
    if (this.status() === value) return;
    this.status.set(value);
    this.page.set(1);
    this.fetch();
  }

  prev(): void {
    if (this.page() <= 1) return;
    this.page.update((p) => p - 1);
    this.fetch();
  }

  next(): void {
    if (this.page() >= this.totalPages()) return;
    this.page.update((p) => p + 1);
    this.fetch();
  }

  retry(): void {
    this.fetch();
  }

  private fetch(): void {
    const id = ++this.reqId;
    this.state.set({ kind: 'loading' });
    this.svc
      .list({
        page: this.page(),
        page_size: this.pageSize,
        q: this.query(),
        status: this.status(),
      })
      .subscribe({
        next: (data) => {
          if (id === this.reqId) this.state.set({ kind: 'ready', data });
        },
        error: () => {
          if (id === this.reqId) this.state.set({ kind: 'error' });
        },
      });
  }

  // ---- Darstellungshelfer -------------------------------------------------
  belegNummer(q: Quote): string {
    return q.quote_number ?? 'Entwurf';
  }

  euro(amount: string | null): string {
    if (amount === null) return '—';
    return new Intl.NumberFormat('de-DE', {
      style: 'currency',
      currency: 'EUR',
    }).format(Number(amount));
  }

  statusLabel(s: QuoteStatus): string {
    const map: Record<QuoteStatus, string> = {
      ENTWURF: 'Entwurf',
      INTERN_GEPRUEFT: 'Intern geprüft',
      FREIGEGEBEN: 'Freigegeben',
      VERSENDET: 'Versendet',
      ANGENOMMEN: 'Angenommen',
      ABGELEHNT: 'Abgelehnt',
      ABGELAUFEN: 'Abgelaufen',
      ERSETZT: 'Ersetzt',
    };
    return map[s] ?? s;
  }

  statusClass(s: QuoteStatus): string {
    if (s === 'ANGENOMMEN') return 'stamp--positive';
    if (s === 'ABGELEHNT' || s === 'ABGELAUFEN' || s === 'ERSETZT') return 'stamp--warn';
    return '';
  }
}
