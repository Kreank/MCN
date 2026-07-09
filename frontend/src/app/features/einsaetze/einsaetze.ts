import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { Subject, debounceTime, distinctUntilChanged } from 'rxjs';
import { EinsatzService } from '../../core/einsatz.service';
import {
  ServiceJobPage,
  ServiceJobStatus,
  serviceJobStatusClass,
  serviceJobStatusLabel,
} from '../../core/einsatz.model';
import { PlanungNav } from '../planung-nav/planung-nav';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: ServiceJobPage }
  | VerbotenState
  | { kind: 'error' };

type Segment = { value: ServiceJobStatus | null; label: string };

@Component({
  selector: 'app-einsaetze',
  imports: [RouterLink, PlanungNav, KeinZugriff],
  templateUrl: './einsaetze.html',
  styleUrl: './einsaetze.scss',
})
export class Einsaetze {
  private readonly svc = inject(EinsatzService);

  protected readonly pageSize = 20;
  protected readonly segments: Segment[] = [
    { value: null, label: 'Alle' },
    { value: 'GEPLANT', label: 'Geplant' },
    { value: 'VOR_ORT', label: 'Vor Ort' },
    { value: 'ABGESCHLOSSEN', label: 'Abgeschlossen' },
  ];

  protected readonly query = signal('');
  protected readonly status = signal<ServiceJobStatus | null>(null);
  protected readonly page = signal(1);
  protected readonly state = signal<ViewState>({ kind: 'loading' });

  protected readonly skeletons = Array.from({ length: 6 });

  private readonly searchInput$ = new Subject<string>();
  private reqId = 0;

  private readonly dateFmt = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });

  protected readonly totalPages = computed(() => {
    const s = this.state();
    if (s.kind !== 'ready') return 1;
    return Math.max(1, Math.ceil(s.data.total / s.data.page_size));
  });

  protected readonly resultSummary = computed(() => {
    const s = this.state();
    if (s.kind === 'loading') return 'Einsätze werden geladen.';
    if (s.kind === 'forbidden') return 'Keine Berechtigung für die Einsätze.';
    if (s.kind === 'error') return 'Einsätze konnten nicht geladen werden.';
    const t = s.data.total;
    if (t === 0) return 'Keine Einsätze gefunden.';
    return `${t} ${t === 1 ? 'Einsatz' : 'Einsätze'} gefunden, Seite ${s.data.page} von ${this.totalPages()}.`;
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

  selectSegment(value: ServiceJobStatus | null): void {
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
        error: (err) => {
          if (id === this.reqId) this.state.set(fehlerState(err));
        },
      });
  }

  // ---- Darstellungshelfer -------------------------------------------------
  statusLabel(s: ServiceJobStatus): string {
    return serviceJobStatusLabel(s);
  }
  statusClass(s: ServiceJobStatus): string {
    return serviceJobStatusClass(s);
  }
  planLabel(iso: string | null): string {
    if (!iso) return 'ungeplant';
    return this.dateFmt.format(new Date(iso));
  }
}
