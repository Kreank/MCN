import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { Subject, debounceTime, distinctUntilChanged } from 'rxjs';
import { WartungService } from '../../core/wartung.service';
import {
  ContractPage,
  ContractStatus,
  DueAction,
  IntervalKind,
  contractStatusClass,
  contractStatusLabel,
  dueActionLabel,
  intervalKindLabel,
} from '../../core/wartung.model';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: ContractPage }
  | { kind: 'error' };

type Segment = { value: ContractStatus | null; label: string };

@Component({
  selector: 'app-wartung',
  imports: [RouterLink],
  templateUrl: './wartung.html',
  styleUrl: './wartung.scss',
})
export class Wartung {
  private readonly svc = inject(WartungService);

  protected readonly pageSize = 20;
  protected readonly segments: Segment[] = [
    { value: null, label: 'Alle' },
    { value: 'AKTIV', label: 'Aktiv' },
    { value: 'INAKTIV', label: 'Inaktiv' },
    { value: 'ARCHIVIERT', label: 'Archiviert' },
  ];

  protected readonly query = signal('');
  protected readonly status = signal<ContractStatus | null>(null);
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
    if (s.kind === 'loading') return 'Wartungsverträge werden geladen.';
    if (s.kind === 'error') return 'Wartungsverträge konnten nicht geladen werden.';
    const t = s.data.total;
    if (t === 0) return 'Keine Wartungsverträge gefunden.';
    return `${t} ${t === 1 ? 'Vertrag' : 'Verträge'} gefunden, Seite ${s.data.page} von ${this.totalPages()}.`;
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

  selectSegment(value: ContractStatus | null): void {
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
  statusLabel(s: ContractStatus): string {
    return contractStatusLabel(s);
  }
  statusClass(s: ContractStatus): string {
    return contractStatusClass(s);
  }
  intervalLabel(k: IntervalKind, days: number | null): string {
    return intervalKindLabel(k, days);
  }
  actionLabel(a: DueAction): string {
    return dueActionLabel(a);
  }
  d(iso: string | null): string {
    if (!iso) return '—';
    return this.dateFmt.format(new Date(iso));
  }
}
