import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';
import { WartungService } from '../../core/wartung.service';
import {
  ContractDetail,
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
  | { kind: 'ready'; data: ContractDetail }
  | VerbotenState
  | { kind: 'error' };

@Component({
  selector: 'app-wartung-detail',
  imports: [Mappe, RouterLink, KeinZugriff],
  templateUrl: './wartung-detail.html',
  styleUrl: './wartung-detail.scss',
})
export class WartungDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(WartungService);

  protected readonly tab = signal('details');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  protected readonly tabs: MappeTab[] = [
    { id: 'details', label: 'Details' },
    { id: 'erinnerung', label: 'Erinnerung' },
    { id: 'verlauf', label: 'Verlauf' },
  ];

  private readonly dateFmt = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  });

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.tab.set('details');
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
    this.svc.get(id).subscribe({
      next: (data) => {
        if (rid === this.reqId) this.state.set({ kind: 'ready', data });
      },
      error: (err) => {
        if (rid === this.reqId) this.state.set(fehlerState(err));
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
