import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { Subject, debounceTime, distinctUntilChanged } from 'rxjs';
import { ProjektService } from '../../core/projekt.service';
import {
  CasePriority,
  ServiceCaseBoard,
  ServiceCaseStatus,
} from '../../core/projekt.model';
import { EingangNav } from '../eingang-nav/eingang-nav';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: ServiceCaseBoard }
  | VerbotenState
  | { kind: 'error' };

type Segment = { value: ServiceCaseStatus | null; label: string };

/**
 * Der Eingang (Vorgangs-Eingangskorb): die flache, chronologische Vorgangsliste
 * (service_case), neueste zuerst. Hier landen Meldungen und warten auf eine
 * Entscheidung — annehmen (Auftrag anlegen / zum Projekt hochstufen) oder
 * ablehnen geschieht in der Vorgangsmappe. Board unter /eingang/board.
 *
 * Default = **wartet-auf-Entscheidung**: der Endpunkt blendet im nur_offen-Modus
 * terminale (ABGESCHLOSSEN/ABGELEHNT) UND BEAUFTRAGT aus — ein zum Auftrag
 * gemachter Vorgang hat den Eingang verlassen. Der Umschalter „Alle anzeigen"
 * zeigt beauftragte und erledigte mit; ein konkret gewählter Status filtert
 * direkt darauf (dann greift nur_offen nicht).
 */
@Component({
  selector: 'app-vorgang-liste',
  imports: [RouterLink, EingangNav, KeinZugriff],
  templateUrl: './vorgang-liste.html',
  styleUrl: './vorgang-liste.scss',
})
export class VorgangListe {
  private readonly svc = inject(ProjektService);

  protected readonly pageSize = 20;
  protected readonly segments: Segment[] = [
    { value: null, label: 'Offen' },
    { value: 'NEU', label: 'Neu' },
    { value: 'IN_PRUEFUNG', label: 'In Prüfung' },
    { value: 'FREIGABE_AUSSTEHEND', label: 'Freigabe' },
    { value: 'BEAUFTRAGT', label: 'Beauftragt' },
  ];

  protected readonly query = signal('');
  protected readonly status = signal<ServiceCaseStatus | null>(null);
  protected readonly includeTerminal = signal(false);
  protected readonly page = signal(1);
  protected readonly state = signal<ViewState>({ kind: 'loading' });

  protected readonly skeletons = Array.from({ length: 8 });

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
    if (s.kind === 'loading') return 'Vorgänge werden geladen.';
    if (s.kind === 'forbidden') return 'Keine Berechtigung für die Vorgänge.';
    if (s.kind === 'error') return 'Vorgänge konnten nicht geladen werden.';
    const t = s.data.total;
    if (t === 0) return 'Keine Vorgänge gefunden.';
    return `${t} ${t === 1 ? 'Vorgang' : 'Vorgänge'} gefunden, Seite ${s.data.page} von ${this.totalPages()}.`;
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

  selectSegment(value: ServiceCaseStatus | null): void {
    if (this.status() === value) return;
    this.status.set(value);
    this.page.set(1);
    this.fetch();
  }

  terminalUmschalten(): void {
    this.includeTerminal.update((v) => !v);
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
      .listServiceCases({
        page: this.page(),
        page_size: this.pageSize,
        q: this.query(),
        status: this.status(),
        // Default: nur der offene Eingang (ohne BEAUFTRAGT/terminal). „Alle
        // anzeigen" kehrt beides um. Bei explizitem Status ignoriert der Server
        // beide Flags.
        nur_offen: !this.includeTerminal(),
        include_terminal: this.includeTerminal(),
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
  eingang(iso: string): string {
    return this.dateFmt.format(new Date(iso));
  }

  statusLabel(s: ServiceCaseStatus): string {
    const map: Record<ServiceCaseStatus, string> = {
      NEU: 'Neu',
      IN_PRUEFUNG: 'In Prüfung',
      RUECKFRAGE: 'Rückfrage',
      FREIGABE_AUSSTEHEND: 'Freigabe ausstehend',
      BEAUFTRAGT: 'Beauftragt',
      ABGESCHLOSSEN: 'Abgeschlossen',
      ABGELEHNT: 'Abgelehnt',
    };
    return map[s] ?? s;
  }

  statusClass(s: ServiceCaseStatus): string {
    if (s === 'ABGESCHLOSSEN') return 'stamp--positive';
    if (s === 'ABGELEHNT') return 'stamp--warn';
    return '';
  }

  priorityLabel(p: CasePriority): string {
    const map: Record<CasePriority, string> = {
      NORMAL: 'Normal',
      DRINGEND: 'Dringend',
      NOTFALL: 'Notfall',
    };
    return map[p] ?? p;
  }

  priorityClass(p: CasePriority): string {
    if (p === 'NOTFALL') return 'stamp--negativ';
    if (p === 'DRINGEND') return 'stamp--warn';
    return '';
  }
}
