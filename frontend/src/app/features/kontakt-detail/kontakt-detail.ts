import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { PartyService } from '../../core/party.service';
import { PartyDetail, PartyStatus, PartyType } from '../../core/party.model';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: PartyDetail }
  | { kind: 'error' };

@Component({
  selector: 'app-kontakt-detail',
  imports: [Mappe, RouterLink],
  templateUrl: './kontakt-detail.html',
  styleUrl: './kontakt-detail.scss',
})
export class KontaktDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(PartyService);

  protected readonly tab = signal('stammdaten');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  protected readonly tabs: MappeTab[] = [
    { id: 'stammdaten', label: 'Stammdaten' },
    { id: 'objektadressen', label: 'Objektadressen' },
    { id: 'ansprechpartner', label: 'Ansprechpartner' },
    { id: 'dokumente', label: 'Dokumente' },
    { id: 'logbuch', label: 'Logbuch' },
  ];

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.tab.set('stammdaten');
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
  typeLabel(t: PartyType): string {
    return t === 'PERSON' ? 'Person' : 'Organisation';
  }

  statusLabel(s: PartyStatus): string {
    switch (s) {
      case 'ACTIVE':
        return 'Aktiv';
      case 'INACTIVE':
        return 'Inaktiv';
      case 'MERGED':
        return 'Zusammengeführt';
    }
  }

  statusClass(s: PartyStatus): string {
    switch (s) {
      case 'ACTIVE':
        return 'stamp--positive';
      case 'MERGED':
        return 'stamp--warn';
      default:
        return '';
    }
  }

  orgTypeLabel(t: string): string {
    const map: Record<string, string> = {
      PROPERTY_MANAGEMENT: 'Hausverwaltung',
      WEG: 'WEG',
      COMPANY: 'Firma',
      AUTHORITY: 'Behörde',
      INSURER: 'Versicherer',
      OTHER: 'Sonstige',
    };
    return map[t] ?? t;
  }
}
