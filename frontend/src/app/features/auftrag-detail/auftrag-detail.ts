import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { AuftragService } from '../../core/auftrag.service';
import {
  OrderPriority,
  WorkOrderDetail,
  WorkOrderStatus,
  workOrderStatusClass,
  workOrderStatusLabel,
} from '../../core/auftrag.model';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: WorkOrderDetail }
  | { kind: 'error' };

@Component({
  selector: 'app-auftrag-detail',
  imports: [Mappe, RouterLink],
  templateUrl: './auftrag-detail.html',
  styleUrl: './auftrag-detail.scss',
})
export class AuftragDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(AuftragService);

  protected readonly tab = signal('uebersicht');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  protected readonly tabs: MappeTab[] = [
    { id: 'uebersicht', label: 'Übersicht' },
    { id: 'beteiligte', label: 'Beteiligte' },
    { id: 'verlauf', label: 'Verlauf' },
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
  statusLabel(s: WorkOrderStatus): string {
    return workOrderStatusLabel(s);
  }
  statusClass(s: WorkOrderStatus): string {
    return workOrderStatusClass(s);
  }
  // Auch für Verlaufseinträge (String-Status).
  statusLabelStr(s: string | null): string {
    if (s === null) return 'Anlage';
    return workOrderStatusLabel(s as WorkOrderStatus);
  }

  priorityLabel(p: OrderPriority): string {
    const map: Record<OrderPriority, string> = {
      NORMAL: 'Normal',
      DRINGEND: 'Dringend',
      NOTFALL: 'Notfall',
    };
    return map[p] ?? p;
  }
  priorityClass(p: OrderPriority): string {
    return p === 'NORMAL' ? '' : 'stamp--warn';
  }

  scopeLabel(s: string): string {
    const map: Record<string, string> = {
      UNKNOWN: 'Ungeklärt',
      COMMON_PROPERTY: 'Gemeinschaftseigentum',
      PRIVATE_UNIT: 'Sondereigentum',
      MIXED: 'Gemischt',
    };
    return map[s] ?? s;
  }

  roleLabel(r: string): string {
    const map: Record<string, string> = {
      PRINCIPAL: 'Auftraggeber',
      REPRESENTATIVE: 'Vertretung',
      SERVICE_RECIPIENT: 'Leistungsempfänger',
      OCCUPANT: 'Nutzer',
      COST_BEARER: 'Kostenträger',
      INVOICE_DEBTOR: 'Rechnungsschuldner',
      INVOICE_RECIPIENT: 'Rechnungsempfänger',
      REPORTER: 'Melder',
      ON_SITE_CONTACT: 'Ansprechpartner vor Ort',
    };
    return map[r] ?? r;
  }
}
