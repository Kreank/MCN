import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Subject, debounceTime, distinctUntilChanged } from 'rxjs';
import { GeraetewissenService } from '../../core/geraetewissen.service';
import {
  Ersatzteil,
  ErsatzteilDetail,
  ErsatzteilPage,
  Hersteller,
} from '../../core/geraetewissen.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';
import { Dialog } from '../../shared/dialog/dialog';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: ErsatzteilPage }
  | VerbotenState
  | { kind: 'error' };

/** Zustand des read-only-Detail-Dialogs eines Ersatzteils. */
type DetailState =
  | { kind: 'closed' }
  | { kind: 'loading'; titel: string }
  | { kind: 'ready'; data: ErsatzteilDetail }
  | { kind: 'error'; titel: string };

/**
 * „Gerätewissen" — durchsuchbare, read-only-Sicht auf Hersteller-Ersatzteile.
 *
 * Zeigt AUSSCHLIESSLICH Artikel aus den Hersteller-Namensräumen (vaillant,
 * junkers, …); der Großhandels-Namensraum `bo` erscheint hier nie. Der Import der
 * Kataloge läuft separat — ist noch keiner da, greift der erklärende Leerzustand.
 */
@Component({
  selector: 'app-geraetewissen',
  imports: [KeinZugriff, Dialog],
  templateUrl: './geraetewissen.html',
  styleUrl: './geraetewissen.scss',
})
export class Geraetewissen {
  private readonly svc = inject(GeraetewissenService);

  protected readonly pageSize = 20;
  protected readonly query = signal('');
  /** Aktiver Hersteller-Filter (null = alle konfigurierten Hersteller). */
  protected readonly namespace = signal<string | null>(null);
  protected readonly page = signal(1);
  protected readonly state = signal<ViewState>({ kind: 'loading' });

  protected readonly hersteller = signal<Hersteller[]>([]);
  protected readonly herstellerGeladen = signal(false);

  protected readonly skeletons = Array.from({ length: 6 });

  private readonly searchInput$ = new Subject<string>();
  private reqId = 0;

  // --- Detail-Dialog -------------------------------------------------------
  protected readonly detail = signal<DetailState>({ kind: 'closed' });
  protected readonly detailOffen = computed(() => this.detail().kind !== 'closed');

  protected readonly totalPages = computed(() => {
    const s = this.state();
    if (s.kind !== 'ready') return 1;
    return Math.max(1, Math.ceil(s.data.total / s.data.page_size));
  });

  /** Sind (noch) gar keine Kataloge importiert? Grundlage für den Leerzustand. */
  protected readonly katalogeLeer = computed(
    () =>
      this.herstellerGeladen() &&
      this.hersteller().length > 0 &&
      this.hersteller().every((h) => h.anzahl === 0),
  );

  protected readonly resultSummary = computed(() => {
    const s = this.state();
    if (s.kind === 'loading') return 'Ersatzteile werden geladen.';
    if (s.kind === 'forbidden') return 'Keine Berechtigung für das Gerätewissen.';
    if (s.kind === 'error') return 'Ersatzteile konnten nicht geladen werden.';
    const t = s.data.total;
    if (t === 0) {
      return this.katalogeLeer()
        ? 'Noch keine Ersatzteilkataloge importiert.'
        : 'Keine Ersatzteile für diese Auswahl gefunden.';
    }
    return `${t} Ersatzteile gefunden, Seite ${s.data.page} von ${this.totalPages()}.`;
  });

  constructor() {
    this.searchInput$
      .pipe(debounceTime(300), distinctUntilChanged(), takeUntilDestroyed())
      .subscribe((v) => {
        this.query.set(v);
        this.page.set(1);
        this.fetch();
      });
    this.herstellerLaden();
    this.fetch();
  }

  onSearch(value: string): void {
    this.searchInput$.next(value);
  }

  /** Hersteller-Chip wählen (erneuter Klick auf den aktiven hebt den Filter auf). */
  selectHersteller(ns: string | null): void {
    const neu = this.namespace() === ns ? null : ns;
    if (this.namespace() === neu) {
      if (ns === null) return; // „Alle" ist bereits aktiv
    }
    this.namespace.set(neu);
    this.page.set(1);
    this.fetch();
  }

  istAktiv(ns: string | null): boolean {
    return this.namespace() === ns;
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
    this.herstellerLaden();
    this.fetch();
  }

  private herstellerLaden(): void {
    this.svc.listHersteller().subscribe({
      next: (h) => {
        this.hersteller.set(h);
        this.herstellerGeladen.set(true);
      },
      // Facetten sind Beiwerk: scheitern sie (z. B. 403), bleibt die Liste die
      // maßgebliche Fehlerquelle. Chips bleiben dann einfach leer.
      error: () => this.herstellerGeladen.set(true),
    });
  }

  private fetch(): void {
    const id = ++this.reqId;
    this.state.set({ kind: 'loading' });
    this.svc
      .listErsatzteile({
        page: this.page(),
        page_size: this.pageSize,
        q: this.query(),
        namespace: this.namespace(),
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

  // ---- Detail-Dialog ------------------------------------------------------
  ersatzteilOeffnen(e: Ersatzteil): void {
    const titel = e.description;
    this.detail.set({ kind: 'loading', titel });
    this.svc.getErsatzteil(e.article_id).subscribe({
      next: (data) => this.detail.set({ kind: 'ready', data }),
      error: () => this.detail.set({ kind: 'error', titel }),
    });
  }

  detailSchliessen(): void {
    this.detail.set({ kind: 'closed' });
  }

  /** Titel des Detail-Dialogs (Bezeichnung des gewählten Ersatzteils). */
  detailTitel(): string {
    const d = this.detail();
    if (d.kind === 'ready') return d.data.description;
    if (d.kind === 'loading' || d.kind === 'error') return d.titel;
    return 'Ersatzteil';
  }

  // ---- Darstellungshelfer -------------------------------------------------
  euro(amount: string | null): string {
    if (amount === null) return '—';
    return new Intl.NumberFormat('de-DE', {
      style: 'currency',
      currency: 'EUR',
    }).format(Number(amount));
  }

  /** Anzeigename eines Herstellers zum Namensraum (für die Detail-/Listenzeile). */
  herstellerLabel(ns: string | null): string {
    if (!ns) return '—';
    const h = this.hersteller().find((x) => x.namespace === ns);
    return h?.label ?? ns;
  }
}
