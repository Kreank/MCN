import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { ProjektService } from '../../core/projekt.service';
import {
  CasePriority,
  ProjectDetail,
  ProjectStatus,
  ServiceCaseStatus,
} from '../../core/projekt.model';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: ProjectDetail }
  | { kind: 'error' };

@Component({
  selector: 'app-projekt-detail',
  imports: [Mappe, RouterLink],
  templateUrl: './projekt-detail.html',
  styleUrl: './projekt-detail.scss',
})
export class ProjektDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(ProjektService);

  protected readonly tab = signal('uebersicht');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  protected readonly tabs: MappeTab[] = [
    { id: 'uebersicht', label: 'Übersicht' },
    { id: 'liegenschaften', label: 'Liegenschaften' },
    { id: 'vorgaenge', label: 'Vorgänge' },
    { id: 'logbuch', label: 'Logbuch' },
    { id: 'checklisten', label: 'Checklisten' },
    { id: 'dateien', label: 'Dateien' },
  ];

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
  statusLabel(s: ProjectStatus): string {
    return s === 'OPEN' ? 'Offen' : 'Geschlossen';
  }
  statusClass(s: ProjectStatus): string {
    return s === 'OPEN' ? 'stamp--positive' : '';
  }

  caseStatusLabel(s: ServiceCaseStatus): string {
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
  caseStatusClass(s: ServiceCaseStatus): string {
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
    return p === 'NORMAL' ? '' : 'stamp--warn';
  }
}
