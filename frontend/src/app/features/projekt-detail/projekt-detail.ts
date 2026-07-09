import { Component, computed, effect, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { ProjektService } from '../../core/projekt.service';
import { AufgabeService } from '../../core/aufgabe.service';
import { AuftragService } from '../../core/auftrag.service';
import { Task, TaskStatus } from '../../core/aufgabe.model';
import {
  WorkOrder,
  WorkOrderStatus,
  workOrderStatusClass,
  workOrderStatusLabel,
} from '../../core/auftrag.model';
import {
  CasePriority,
  Checklist,
  LogCategory,
  LogEntry,
  ProjectDetail,
  ProjectStatus,
  ServiceCaseStatus,
} from '../../core/projekt.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: ProjectDetail }
  | VerbotenState
  | { kind: 'error' };

type TasksState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; items: Task[] }
  | VerbotenState
  | { kind: 'error' };

type LazyState<T> =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; items: T[] }
  | VerbotenState
  | { kind: 'error' };

@Component({
  selector: 'app-projekt-detail',
  imports: [Mappe, RouterLink, KeinZugriff],
  templateUrl: './projekt-detail.html',
  styleUrl: './projekt-detail.scss',
})
export class ProjektDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(ProjektService);
  private readonly aufgabeSvc = inject(AufgabeService);
  private readonly auftragSvc = inject(AuftragService);

  protected readonly tab = signal('uebersicht');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly tasksState = signal<TasksState>({ kind: 'idle' });
  protected readonly ordersState = signal<LazyState<WorkOrder>>({ kind: 'idle' });
  protected readonly logState = signal<LazyState<LogEntry>>({ kind: 'idle' });
  protected readonly checklistsState = signal<LazyState<Checklist>>({ kind: 'idle' });
  private reqId = 0;

  protected readonly tabs: MappeTab[] = [
    { id: 'uebersicht', label: 'Übersicht' },
    { id: 'liegenschaften', label: 'Liegenschaften' },
    { id: 'vorgaenge', label: 'Vorgänge' },
    { id: 'auftraege', label: 'Aufträge' },
    { id: 'aufgaben', label: 'Aufgaben' },
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
      this.tasksState.set({ kind: 'idle' });
      this.ordersState.set({ kind: 'idle' });
      this.logState.set({ kind: 'idle' });
      this.checklistsState.set({ kind: 'idle' });
      if (!id) {
        this.state.set({ kind: 'error' });
        return;
      }
      this.load(id);
    });

    // Cockpit-Tabs (Aufgaben/Logbuch/Checklisten) erst beim Öffnen laden (lazy).
    effect(() => {
      const d = this.daten();
      if (!d) return;
      const t = this.tab();
      if (t === 'aufgaben' && this.tasksState().kind === 'idle') this.loadTasks(d.id);
      if (t === 'auftraege' && this.ordersState().kind === 'idle') this.loadOrders(d.id);
      if (t === 'logbuch' && this.logState().kind === 'idle') this.loadLog(d.id);
      if (t === 'checklisten' && this.checklistsState().kind === 'idle') {
        this.loadChecklists(d.id);
      }
    });
  }

  private loadTasks(projectId: string): void {
    this.tasksState.set({ kind: 'loading' });
    this.aufgabeSvc.list({ page: 1, page_size: 50, project_id: projectId }).subscribe({
      next: (d) => this.tasksState.set({ kind: 'ready', items: d.items }),
      error: (err) => this.tasksState.set(fehlerState(err)),
    });
  }

  private loadOrders(projectId: string): void {
    this.ordersState.set({ kind: 'loading' });
    this.auftragSvc.list({ page: 1, page_size: 50, project_id: projectId }).subscribe({
      next: (d) => this.ordersState.set({ kind: 'ready', items: d.items }),
      error: (err) => this.ordersState.set(fehlerState(err)),
    });
  }

  private loadLog(projectId: string): void {
    this.logState.set({ kind: 'loading' });
    this.svc.getProjectLog(projectId).subscribe({
      next: (items) => this.logState.set({ kind: 'ready', items }),
      error: (err) => this.logState.set(fehlerState(err)),
    });
  }

  private loadChecklists(projectId: string): void {
    this.checklistsState.set({ kind: 'loading' });
    this.svc.getChecklists(projectId).subscribe({
      next: (items) => this.checklistsState.set({ kind: 'ready', items }),
      error: (err) => this.checklistsState.set(fehlerState(err)),
    });
  }

  logCategoryLabel(c: LogCategory): string {
    const map: Record<LogCategory, string> = {
      NOTIZ: 'Notiz',
      ANRUF: 'Anruf',
      ABSPRACHE: 'Absprache',
      ENTSCHEIDUNG: 'Entscheidung',
      SYSTEM: 'System',
    };
    return map[c] ?? c;
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

  taskStatusLabel(s: TaskStatus): string {
    switch (s) {
      case 'OFFEN':
        return 'Offen';
      case 'ERLEDIGT':
        return 'Erledigt';
      case 'VERWORFEN':
        return 'Verworfen';
    }
  }
  taskStatusClass(s: TaskStatus): string {
    if (s === 'ERLEDIGT') return 'stamp--positive';
    if (s === 'VERWORFEN') return 'stamp--warn';
    return '';
  }

  orderStatusLabel(s: WorkOrderStatus): string {
    return workOrderStatusLabel(s);
  }
  orderStatusClass(s: WorkOrderStatus): string {
    return workOrderStatusClass(s);
  }
}
