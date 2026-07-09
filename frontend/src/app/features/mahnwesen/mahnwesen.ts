import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { BuchhaltungService } from '../../core/buchhaltung.service';
import { DunningList, euro } from '../../core/buchhaltung.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: DunningList }
  | VerbotenState
  | { kind: 'error' };

// Tab-Auswahl: 'alle' = alles, 'offen' = überfällig aber ungemahnt (Stufe 0),
// Zahl = Rechnungen auf genau dieser aktuellen Mahnstufe.
type Tab = 'alle' | 'offen' | number;

@Component({
  selector: 'app-mahnwesen',
  imports: [RouterLink, KeinZugriff],
  templateUrl: './mahnwesen.html',
  styleUrl: './mahnwesen.scss',
})
export class Mahnwesen {
  private readonly svc = inject(BuchhaltungService);

  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly activeTab = signal<Tab>('alle');

  private readonly dateFmt = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  });

  /** Tabs: Alle · Überfällig (ungemahnt) · je konfigurierter Mahnstufe. */
  protected readonly tabs = computed<{ id: Tab; label: string }[]>(() => {
    const s = this.state();
    const levels = s.kind === 'ready' ? s.data.levels : [];
    return [
      { id: 'alle' as Tab, label: 'Alle' },
      { id: 'offen' as Tab, label: 'Überfällig' },
      ...levels.map((l) => ({ id: l.level as Tab, label: `${l.level}. ${l.label}` })),
    ];
  });

  protected readonly filtered = computed(() => {
    const s = this.state();
    if (s.kind !== 'ready') return [];
    const tab = this.activeTab();
    return s.data.items.filter((it) => {
      if (tab === 'alle') return true;
      if (tab === 'offen') return !it.dunning_level;
      return it.dunning_level === tab;
    });
  });

  protected readonly resultSummary = computed(() => {
    const s = this.state();
    if (s.kind === 'loading') return 'Mahnfälle werden geladen.';
    if (s.kind === 'forbidden') return 'Keine Berechtigung für das Mahnwesen.';
    if (s.kind === 'error') return 'Mahnfälle konnten nicht geladen werden.';
    const n = this.filtered().length;
    return n === 0 ? 'Keine Mahnfälle in dieser Auswahl.' : `${n} Mahnfälle in dieser Auswahl.`;
  });

  constructor() {
    this.fetch();
  }

  selectTab(id: Tab): void {
    this.activeTab.set(id);
  }

  retry(): void {
    this.fetch();
  }

  private fetch(): void {
    this.state.set({ kind: 'loading' });
    this.svc.listDunning().subscribe({
      next: (data) => this.state.set({ kind: 'ready', data }),
      error: (err) => this.state.set(fehlerState(err)),
    });
  }

  // ---- Darstellungshelfer -------------------------------------------------
  euro(v: string | null): string {
    return euro(v);
  }
  d(iso: string | null): string {
    if (!iso) return '—';
    return this.dateFmt.format(new Date(iso));
  }
}
