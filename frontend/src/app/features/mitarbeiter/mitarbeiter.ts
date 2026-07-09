import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { Subject, debounceTime, distinctUntilChanged } from 'rxjs';
import { MitarbeiterService } from '../../core/mitarbeiter.service';
import {
  EmployeePage,
  EmployeeStatus,
  employeeStatusClass,
  employeeStatusLabel,
} from '../../core/mitarbeiter.model';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: EmployeePage }
  | { kind: 'error' };

type Segment = { value: EmployeeStatus | null; label: string };

@Component({
  selector: 'app-mitarbeiter',
  imports: [RouterLink],
  templateUrl: './mitarbeiter.html',
  styleUrl: './mitarbeiter.scss',
})
export class Mitarbeiter {
  private readonly svc = inject(MitarbeiterService);

  protected readonly pageSize = 20;
  protected readonly segments: Segment[] = [
    { value: null, label: 'Alle' },
    { value: 'AKTIV', label: 'Aktiv' },
    { value: 'INAKTIV', label: 'Inaktiv' },
    { value: 'AUSGETRETEN', label: 'Ausgetreten' },
  ];

  protected readonly query = signal('');
  protected readonly status = signal<EmployeeStatus | null>(null);
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
  private readonly rateFmt = new Intl.NumberFormat('de-DE', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

  protected readonly totalPages = computed(() => {
    const s = this.state();
    if (s.kind !== 'ready') return 1;
    return Math.max(1, Math.ceil(s.data.total / s.data.page_size));
  });

  protected readonly resultSummary = computed(() => {
    const s = this.state();
    if (s.kind === 'loading') return 'Mitarbeiter werden geladen.';
    if (s.kind === 'error') return 'Mitarbeiter konnten nicht geladen werden.';
    const t = s.data.total;
    if (t === 0) return 'Keine Mitarbeiter gefunden.';
    return `${t} Mitarbeiter gefunden, Seite ${s.data.page} von ${this.totalPages()}.`;
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

  selectSegment(value: EmployeeStatus | null): void {
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
  statusLabel(s: EmployeeStatus): string {
    return employeeStatusLabel(s);
  }
  statusClass(s: EmployeeStatus): string {
    return employeeStatusClass(s);
  }
  d(iso: string | null): string {
    if (!iso) return '—';
    return this.dateFmt.format(new Date(iso));
  }
  /** Lohngruppe inkl. Stundensatz (Decimal-String → nur zur Anzeige gerechnet). */
  rate(value: string): string {
    return `${this.rateFmt.format(Number(value))} €/h`;
  }
}
