import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { EinsatzService } from '../../core/einsatz.service';
import {
  ServiceJobDetail,
  ServiceJobStatus,
  assignmentRoleLabel,
  serviceJobStatusClass,
  serviceJobStatusLabel,
  serviceJobStatusLabelStr,
  timeTypeLabel,
  workOrderStatusLabel,
} from '../../core/einsatz.model';
import { WorkOrderStatus } from '../../core/auftrag.model';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: ServiceJobDetail }
  | { kind: 'error' };

@Component({
  selector: 'app-einsatz-detail',
  imports: [Mappe, RouterLink],
  templateUrl: './einsatz-detail.html',
  styleUrl: './einsatz-detail.scss',
})
export class EinsatzDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(EinsatzService);

  protected readonly tab = signal('uebersicht');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  protected readonly tabs: MappeTab[] = [
    { id: 'uebersicht', label: 'Übersicht' },
    { id: 'zuweisungen', label: 'Zuweisungen' },
    { id: 'erfassung', label: 'Zeiten & Material' },
    { id: 'verlauf', label: 'Verlauf' },
  ];

  private readonly dateFmt = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.tab.set('uebersicht');
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
      error: () => {
        if (rid === this.reqId) this.state.set({ kind: 'error' });
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
  statusLabelStr(s: string | null): string {
    return serviceJobStatusLabelStr(s);
  }
  orderStatusLabel(s: WorkOrderStatus): string {
    return workOrderStatusLabel(s);
  }
  timeTypeLabel(t: string): string {
    return timeTypeLabel(t);
  }
  roleLabel(r: string): string {
    return assignmentRoleLabel(r);
  }
  dt(iso: string | null): string {
    if (!iso) return '—';
    return this.dateFmt.format(new Date(iso));
  }
}
