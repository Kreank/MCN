import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { PropertyService } from '../../core/property.service';
import {
  PropertyDetail,
  PropertyRoleCode,
  PropertyStatus,
  PropertyType,
} from '../../core/property.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: PropertyDetail }
  | VerbotenState
  | { kind: 'error' };

@Component({
  selector: 'app-liegenschaft-detail',
  imports: [Mappe, RouterLink, KeinZugriff],
  templateUrl: './liegenschaft-detail.html',
  styleUrl: './liegenschaft-detail.scss',
})
export class LiegenschaftDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(PropertyService);

  protected readonly tab = signal('uebersicht');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  protected readonly tabs: MappeTab[] = [
    { id: 'uebersicht', label: 'Übersicht' },
    { id: 'struktur', label: 'Struktur' },
    { id: 'beteiligte', label: 'Beteiligte' },
    { id: 'eigentum', label: 'Eigentum' },
    { id: 'belegung', label: 'Belegung' },
    { id: 'dokumente', label: 'Dokumente' },
  ];

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  protected readonly einheitenGesamt = computed(() => {
    const d = this.daten();
    if (!d) return 0;
    return d.buildings.reduce((n, b) => n + b.units.length, 0);
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
      error: (err) => {
        if (rid === this.reqId) this.state.set(fehlerState(err));
      },
    });
  }

  // ---- Darstellungshelfer -------------------------------------------------
  typeLabel(t: PropertyType): string {
    switch (t) {
      case 'WEG':
        return 'WEG';
      case 'RENTAL_PROPERTY':
        return 'Mietobjekt';
      case 'COMMERCIAL':
        return 'Gewerbe';
      case 'MIXED':
        return 'Gemischt';
      case 'OTHER':
        return 'Sonstige';
    }
  }

  statusLabel(s: PropertyStatus): string {
    return s === 'ACTIVE' ? 'Aktiv' : 'Inaktiv';
  }

  statusClass(s: PropertyStatus): string {
    return s === 'ACTIVE' ? 'stamp--positive' : '';
  }

  unitTypeLabel(t: string): string {
    const map: Record<string, string> = {
      APARTMENT: 'Wohnung',
      COMMERCIAL: 'Gewerbe',
      GARAGE: 'Garage',
      PARKING: 'Stellplatz',
      STORAGE: 'Lager',
      COMMON_AREA: 'Gemeinschaft',
      TECHNICAL_ROOM: 'Technikraum',
      OTHER: 'Sonstige',
    };
    return map[t] ?? t;
  }

  roleLabel(r: PropertyRoleCode): string {
    const map: Record<PropertyRoleCode, string> = {
      COMMUNITY_OF_OWNERS: 'Eigentümergemeinschaft',
      PROPERTY_OWNER: 'Eigentümer',
      OPERATOR: 'Betreiber',
      CARETAKER: 'Hausmeisterei',
    };
    return map[r] ?? r;
  }
}
