import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';
import { ArtikelService } from '../../core/artikel.service';
import { AssemblyComponent, AssemblyDetail, StammStatus } from '../../core/artikel.model';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: AssemblyDetail }
  | VerbotenState
  | { kind: 'error' };

@Component({
  selector: 'app-leistung-detail',
  imports: [Mappe, RouterLink, KeinZugriff],
  templateUrl: './leistung-detail.html',
  styleUrl: './leistung-detail.scss',
})
export class LeistungDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(ArtikelService);

  protected readonly tab = signal('stueckliste');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  protected readonly tabs: MappeTab[] = [
    { id: 'stueckliste', label: 'Stückliste' },
    { id: 'stammdaten', label: 'Stammdaten' },
  ];

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.tab.set('stueckliste');
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
    this.svc.getAssembly(id).subscribe({
      next: (data) => {
        if (rid === this.reqId) this.state.set({ kind: 'ready', data });
      },
      error: (err) => {
        if (rid === this.reqId) this.state.set(fehlerState(err));
      },
    });
  }

  statusLabel(s: StammStatus): string {
    return s === 'AKTIV' ? 'Aktiv' : 'Inaktiv';
  }
  statusClass(s: StammStatus): string {
    return s === 'AKTIV' ? 'stamp--positive' : '';
  }

  menge(c: AssemblyComponent): string {
    if (c.kind === 'MATERIAL' && c.quantity !== null) {
      const q = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 3 }).format(
        Number(c.quantity),
      );
      return c.unit ? `${q} ${c.unit}` : q;
    }
    if (c.kind === 'LOHN' && c.minutes !== null) {
      const m = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 2 }).format(
        Number(c.minutes),
      );
      return `${m} min`;
    }
    return '';
  }

  kindLabel(k: 'MATERIAL' | 'LOHN'): string {
    return k === 'MATERIAL' ? 'Material' : 'Lohn';
  }
}
