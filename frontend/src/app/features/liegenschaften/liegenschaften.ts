import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { Subject, debounceTime, distinctUntilChanged } from 'rxjs';
import { PropertyService } from '../../core/property.service';
import {
  Property,
  PropertyPage,
  PropertyStatus,
  PropertyType,
} from '../../core/property.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: PropertyPage }
  | VerbotenState
  | { kind: 'error' };

type Segment = { value: PropertyType | null; label: string };

@Component({
  selector: 'app-liegenschaften',
  imports: [RouterLink, KeinZugriff],
  templateUrl: './liegenschaften.html',
  styleUrl: './liegenschaften.scss',
})
export class Liegenschaften {
  private readonly svc = inject(PropertyService);

  protected readonly pageSize = 20;
  protected readonly segments: Segment[] = [
    { value: null, label: 'Alle' },
    { value: 'WEG', label: 'WEG' },
    { value: 'RENTAL_PROPERTY', label: 'Mietobjekt' },
    { value: 'COMMERCIAL', label: 'Gewerbe' },
    { value: 'MIXED', label: 'Gemischt' },
    { value: 'OTHER', label: 'Sonstige' },
  ];

  protected readonly query = signal('');
  protected readonly propertyType = signal<PropertyType | null>(null);
  protected readonly page = signal(1);
  protected readonly state = signal<ViewState>({ kind: 'loading' });

  // Fuer das Laden-Skelett.
  protected readonly skeletons = Array.from({ length: 6 });

  private readonly searchInput$ = new Subject<string>();
  private reqId = 0;

  protected readonly totalPages = computed(() => {
    const s = this.state();
    if (s.kind !== 'ready') return 1;
    return Math.max(1, Math.ceil(s.data.total / s.data.page_size));
  });

  protected readonly resultSummary = computed(() => {
    const s = this.state();
    if (s.kind === 'loading') return 'Liegenschaften werden geladen.';
    if (s.kind === 'forbidden') return 'Keine Berechtigung für die Liegenschaften.';
    if (s.kind === 'error') return 'Liegenschaften konnten nicht geladen werden.';
    const t = s.data.total;
    if (t === 0) return 'Keine Liegenschaften gefunden.';
    return `${t} ${t === 1 ? 'Liegenschaft' : 'Liegenschaften'} gefunden, Seite ${s.data.page} von ${this.totalPages()}.`;
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

  selectSegment(value: PropertyType | null): void {
    if (this.propertyType() === value) return;
    this.propertyType.set(value);
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
      .list({
        page: this.page(),
        page_size: this.pageSize,
        q: this.query(),
        property_type: this.propertyType(),
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
  monogram(p: Property): string {
    const parts = p.name.trim().split(/\s+/).filter(Boolean);
    if (parts.length === 0) return '–';
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }

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
    // Aktiv = gruener Stempel; Inaktiv nutzt den neutralen Basis-Stempel.
    return s === 'ACTIVE' ? 'stamp--positive' : '';
  }
}
