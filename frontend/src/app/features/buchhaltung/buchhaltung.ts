import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { Subject, debounceTime, distinctUntilChanged } from 'rxjs';
import { BuchhaltungService } from '../../core/buchhaltung.service';
import {
  OpenItemPage,
  PaymentStatus,
  euro,
  paymentStatusClass,
  paymentStatusLabel,
} from '../../core/buchhaltung.model';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: OpenItemPage }
  | { kind: 'error' };

type Segment = { value: PaymentStatus | null; label: string };

@Component({
  selector: 'app-buchhaltung',
  imports: [RouterLink],
  templateUrl: './buchhaltung.html',
  styleUrl: './buchhaltung.scss',
})
export class Buchhaltung {
  private readonly svc = inject(BuchhaltungService);

  protected readonly pageSize = 20;
  protected readonly segments: Segment[] = [
    { value: null, label: 'Alle' },
    { value: 'OFFEN', label: 'Offen' },
    { value: 'TEILZAHLUNG', label: 'Teilzahlung' },
    { value: 'BEZAHLT', label: 'Bezahlt' },
  ];

  protected readonly query = signal('');
  protected readonly status = signal<PaymentStatus | null>(null);
  protected readonly page = signal(1);
  protected readonly state = signal<ViewState>({ kind: 'loading' });

  protected readonly skeletons = Array.from({ length: 6 });

  private readonly searchInput$ = new Subject<string>();
  private reqId = 0;

  private readonly dateFmt = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  });

  protected readonly totalPages = computed(() => {
    const s = this.state();
    if (s.kind !== 'ready') return 1;
    return Math.max(1, Math.ceil(s.data.total / s.data.page_size));
  });

  protected readonly resultSummary = computed(() => {
    const s = this.state();
    if (s.kind === 'loading') return 'Offene Posten werden geladen.';
    if (s.kind === 'error') return 'Offene Posten konnten nicht geladen werden.';
    const t = s.data.total;
    if (t === 0) return 'Keine Rechnungen für diese Auswahl.';
    return `${t} ${t === 1 ? 'Rechnung' : 'Rechnungen'} gefunden, Seite ${s.data.page} von ${this.totalPages()}.`;
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

  selectSegment(value: PaymentStatus | null): void {
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
      .listOpenItems({
        page: this.page(),
        page_size: this.pageSize,
        q: this.query(),
        payment_status: this.status(),
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
  statusLabel(s: PaymentStatus): string {
    return paymentStatusLabel(s);
  }
  statusClass(s: PaymentStatus): string {
    return paymentStatusClass(s);
  }
  euro(v: string | null): string {
    return euro(v);
  }
  d(iso: string | null): string {
    if (!iso) return '—';
    return this.dateFmt.format(new Date(iso));
  }
}
