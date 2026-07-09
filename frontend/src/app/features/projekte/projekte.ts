import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { Subject, debounceTime, distinctUntilChanged } from 'rxjs';
import { ProjektService } from '../../core/projekt.service';
import { Project, ProjectPage, ProjectStatus } from '../../core/projekt.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: ProjectPage }
  | VerbotenState
  | { kind: 'error' };

type Segment = { value: ProjectStatus | null; label: string };

@Component({
  selector: 'app-projekte',
  imports: [RouterLink, KeinZugriff],
  templateUrl: './projekte.html',
  styleUrl: './projekte.scss',
})
export class Projekte {
  private readonly svc = inject(ProjektService);

  protected readonly pageSize = 20;
  protected readonly segments: Segment[] = [
    { value: null, label: 'Alle' },
    { value: 'OPEN', label: 'Offen' },
    { value: 'CLOSED', label: 'Geschlossen' },
  ];

  protected readonly query = signal('');
  protected readonly status = signal<ProjectStatus | null>(null);
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
    if (s.kind === 'loading') return 'Projekte werden geladen.';
    if (s.kind === 'forbidden') return 'Keine Berechtigung für die Projekte.';
    if (s.kind === 'error') return 'Projekte konnten nicht geladen werden.';
    const t = s.data.total;
    if (t === 0) return 'Keine Projekte gefunden.';
    return `${t} ${t === 1 ? 'Projekt' : 'Projekte'} gefunden, Seite ${s.data.page} von ${this.totalPages()}.`;
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

  selectSegment(value: ProjectStatus | null): void {
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
  monogram(p: Project): string {
    const parts = p.name.trim().split(/\s+/).filter(Boolean);
    if (parts.length === 0) return '–';
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }

  statusLabel(s: ProjectStatus): string {
    return s === 'OPEN' ? 'Offen' : 'Geschlossen';
  }

  statusClass(s: ProjectStatus): string {
    return s === 'OPEN' ? 'stamp--positive' : '';
  }
}
