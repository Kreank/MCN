import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { Subject, debounceTime, distinctUntilChanged } from 'rxjs';
import { BelegService } from '../../core/beleg.service';
import {
  InvoicePage,
  InvoiceStatus,
  InvoiceType,
  QuotePage,
  QuoteStatus,
} from '../../core/beleg.model';

type Modus = 'angebote' | 'rechnungen';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; modus: 'angebote'; data: QuotePage }
  | { kind: 'ready'; modus: 'rechnungen'; data: InvoicePage }
  | { kind: 'error' };

@Component({
  selector: 'app-dokumente',
  imports: [RouterLink],
  templateUrl: './dokumente.html',
  styleUrl: './dokumente.scss',
})
export class Dokumente {
  private readonly svc = inject(BelegService);

  protected readonly pageSize = 20;
  protected readonly modus = signal<Modus>('angebote');

  protected readonly query = signal('');
  protected readonly page = signal(1);
  protected readonly state = signal<ViewState>({ kind: 'loading' });

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
    const wort = this.modus() === 'angebote' ? 'Angebote' : 'Rechnungen';
    if (s.kind === 'loading') return `${wort} werden geladen.`;
    if (s.kind === 'error') return `${wort} konnten nicht geladen werden.`;
    const t = s.data.total;
    if (t === 0) return `Keine ${wort} gefunden.`;
    return `${t} Belege gefunden, Seite ${s.data.page} von ${this.totalPages()}.`;
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

  selectModus(value: Modus): void {
    if (this.modus() === value) return;
    this.modus.set(value);
    this.query.set('');
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
    const modus = this.modus();
    this.state.set({ kind: 'loading' });
    const query = { page: this.page(), page_size: this.pageSize, q: this.query() };
    if (modus === 'angebote') {
      this.svc.list(query).subscribe({
        next: (data) => {
          if (id === this.reqId) this.state.set({ kind: 'ready', modus, data });
        },
        error: () => {
          if (id === this.reqId) this.state.set({ kind: 'error' });
        },
      });
    } else {
      this.svc.listInvoices(query).subscribe({
        next: (data) => {
          if (id === this.reqId) this.state.set({ kind: 'ready', modus, data });
        },
        error: () => {
          if (id === this.reqId) this.state.set({ kind: 'error' });
        },
      });
    }
  }

  // ---- Darstellungshelfer -------------------------------------------------
  belegNummer(nr: string | null): string {
    return nr ?? 'Entwurf';
  }

  euro(amount: string | null): string {
    if (amount === null) return '—';
    return new Intl.NumberFormat('de-DE', {
      style: 'currency',
      currency: 'EUR',
    }).format(Number(amount));
  }

  quoteStatusLabel(s: QuoteStatus): string {
    const map: Record<QuoteStatus, string> = {
      ENTWURF: 'Entwurf',
      INTERN_GEPRUEFT: 'Intern geprüft',
      FREIGEGEBEN: 'Freigegeben',
      VERSENDET: 'Versendet',
      ANGENOMMEN: 'Angenommen',
      ABGELEHNT: 'Abgelehnt',
      ABGELAUFEN: 'Abgelaufen',
      ERSETZT: 'Ersetzt',
    };
    return map[s] ?? s;
  }
  quoteStatusClass(s: QuoteStatus): string {
    if (s === 'ANGENOMMEN') return 'stamp--positive';
    if (s === 'ABGELEHNT' || s === 'ABGELAUFEN' || s === 'ERSETZT') return 'stamp--warn';
    return '';
  }

  invoiceStatusLabel(s: InvoiceStatus): string {
    return s === 'VEROEFFENTLICHT' ? 'Veröffentlicht' : 'Entwurf';
  }
  invoiceStatusClass(s: InvoiceStatus): string {
    return s === 'VEROEFFENTLICHT' ? 'stamp--positive' : '';
  }

  invoiceTypeLabel(t: InvoiceType): string {
    const map: Record<InvoiceType, string> = {
      RECHNUNG: 'Rechnung',
      ABSCHLAGSRECHNUNG: 'Abschlagsrechnung',
      TEILRECHNUNG: 'Teilrechnung',
      SCHLUSSRECHNUNG: 'Schlussrechnung',
      GUTSCHRIFT: 'Gutschrift',
      STORNO: 'Storno',
    };
    return map[t] ?? t;
  }
}
