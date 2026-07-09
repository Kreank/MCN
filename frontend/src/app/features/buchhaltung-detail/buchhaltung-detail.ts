import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { BuchhaltungService } from '../../core/buchhaltung.service';
import {
  OpenItemDetail,
  PaymentStatus,
  euro,
  invoiceTypeLabel,
  paymentStatusClass,
  paymentStatusLabel,
  paymentTypeLabel,
} from '../../core/buchhaltung.model';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: OpenItemDetail }
  | { kind: 'error' };

@Component({
  selector: 'app-buchhaltung-detail',
  imports: [Mappe, RouterLink],
  templateUrl: './buchhaltung-detail.html',
  styleUrl: './buchhaltung-detail.scss',
})
export class BuchhaltungDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(BuchhaltungService);

  protected readonly tab = signal('uebersicht');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  protected readonly tabs: MappeTab[] = [
    { id: 'uebersicht', label: 'Übersicht' },
    { id: 'zahlungen', label: 'Zahlungen' },
    { id: 'mahnverlauf', label: 'Mahnverlauf' },
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
    this.svc.getOpenItem(id).subscribe({
      next: (data) => {
        if (rid === this.reqId) this.state.set({ kind: 'ready', data });
      },
      error: () => {
        if (rid === this.reqId) this.state.set({ kind: 'error' });
      },
    });
  }

  // ---- Darstellungshelfer -------------------------------------------------
  statusLabel(s: PaymentStatus): string {
    return paymentStatusLabel(s);
  }
  statusClass(s: PaymentStatus): string {
    return paymentStatusClass(s);
  }
  typeLabel(t: string): string {
    return invoiceTypeLabel(t);
  }
  paymentTypeLabel(t: string): string {
    return paymentTypeLabel(t);
  }
  euro(v: string | null): string {
    return euro(v);
  }
  d(iso: string | null): string {
    if (!iso) return '—';
    return this.dateFmt.format(new Date(iso));
  }
}
