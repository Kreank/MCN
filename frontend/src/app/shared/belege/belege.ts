import {
  Component,
  DestroyRef,
  computed,
  effect,
  inject,
  input,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { BelegService } from '../../core/beleg.service';
import {
  Invoice,
  InvoiceQuery,
  InvoiceStatus,
  InvoiceType,
  Quote,
  QuoteQuery,
  QuoteStatus,
} from '../../core/beleg.model';
import { VerbotenState, fehlerState } from '../http-fehler';

/**
 * Kontext, an dem Belege gebündelt werden. Es wird **genau ein** Feld gesetzt —
 * der Vorgang, das Projekt oder die Liegenschaft, dessen Angebote und Rechnungen
 * hier zusammengezogen werden.
 */
export interface BelegKontext {
  service_case_id?: string;
  project_id?: string;
  property_id?: string;
}

type QuotesState =
  | { kind: 'loading' }
  | { kind: 'ready'; items: Quote[]; total: number }
  | VerbotenState
  | { kind: 'error' };

type InvoicesState =
  | { kind: 'loading' }
  | { kind: 'ready'; items: Invoice[]; total: number }
  | VerbotenState
  | { kind: 'error' };

/**
 * Wiederverwendbarer Beleg-Bereich für eine Detail-Mappe (Vorgang, Projekt,
 * Liegenschaft). Zieht die **Angebote** und **Rechnungen** eines Kontextes in
 * zwei Abschnitte zusammen — mit Status, Titel/Nummer, Betrag und Datum. Ein
 * Klick führt auf die bestehenden Detailrouten (`/dokumente/:id` bzw.
 * `/rechnungen/:id`).
 *
 * ```html
 * <app-belege [kontext]="belegKontext()" />
 * ```
 *
 * `kontext` sollte als STABILE Referenz übergeben werden (z. B. ein `computed`),
 * damit der Lade-Effekt nicht bei jeder Change-Detection erneut feuert.
 */
@Component({
  selector: 'app-belege',
  imports: [RouterLink],
  templateUrl: './belege.html',
  styleUrl: './belege.scss',
})
export class Belege {
  private readonly svc = inject(BelegService);
  private readonly destroyRef = inject(DestroyRef);

  /** Genau ein Kontextfeld gesetzt (service_case_id | project_id | property_id). */
  readonly kontext = input.required<BelegKontext>();

  /** Wie viele Belege je Abschnitt geladen werden (Kontexte sind überschaubar). */
  private readonly pageSize = 50;

  /** Verwirft veraltete Antworten beim schnellen Kontextwechsel. */
  private ladeReqId = 0;

  protected readonly quotesState = signal<QuotesState>({ kind: 'loading' });
  protected readonly invoicesState = signal<InvoicesState>({ kind: 'loading' });

  protected readonly skeletons = Array.from({ length: 2 });

  constructor() {
    // Lädt (neu), sobald sich die Kontextreferenz ändert. `kontext` muss dafür
    // stabil übergeben werden (computed), sonst läuft der Effekt in einer Schleife.
    effect(() => {
      const k = this.kontext();
      this.laden(k);
    });
  }

  neuLaden(): void {
    this.laden(this.kontext());
  }

  private laden(kontext: BelegKontext): void {
    const rid = ++this.ladeReqId;
    this.quotesState.set({ kind: 'loading' });
    this.invoicesState.set({ kind: 'loading' });

    const quoteQuery: QuoteQuery = { page: 1, page_size: this.pageSize, ...kontext };
    this.svc
      .list(quoteQuery)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (p) => {
          if (rid === this.ladeReqId) {
            this.quotesState.set({ kind: 'ready', items: p.items, total: p.total });
          }
        },
        error: (err) => {
          if (rid === this.ladeReqId) this.quotesState.set(fehlerState(err));
        },
      });

    const invoiceQuery: InvoiceQuery = { page: 1, page_size: this.pageSize, ...kontext };
    this.svc
      .listInvoices(invoiceQuery)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (p) => {
          if (rid === this.ladeReqId) {
            this.invoicesState.set({ kind: 'ready', items: p.items, total: p.total });
          }
        },
        error: (err) => {
          if (rid === this.ladeReqId) this.invoicesState.set(fehlerState(err));
        },
      });
  }

  /** Zusammenfassung für Screenreader (aria-live), ohne Farbe zu bemühen. */
  protected readonly summary = computed(() => {
    const q = this.quotesState();
    const r = this.invoicesState();
    if (q.kind === 'loading' || r.kind === 'loading') return 'Belege werden geladen.';
    const teile: string[] = [];
    if (q.kind === 'ready') teile.push(`${q.total} ${q.total === 1 ? 'Angebot' : 'Angebote'}`);
    if (r.kind === 'ready') teile.push(`${r.total} ${r.total === 1 ? 'Rechnung' : 'Rechnungen'}`);
    if (teile.length === 0) return 'Belege konnten nicht geladen werden.';
    return `${teile.join(', ')} in diesem Kontext.`;
  });

  // ---- Darstellungshelfer -------------------------------------------------
  belegNummer(nr: string | null): string {
    return nr ?? '—';
  }

  euro(amount: string | null): string {
    if (amount === null) return '—';
    return new Intl.NumberFormat('de-DE', {
      style: 'currency',
      currency: 'EUR',
    }).format(Number(amount));
  }

  datum(iso: string | null): string {
    if (!iso) return 'ohne Datum';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleDateString('de-DE', { dateStyle: 'medium' });
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
    if (s === 'VEROEFFENTLICHT') return 'Veröffentlicht';
    return s === 'VERWORFEN' ? 'Verworfen' : 'Entwurf';
  }
  invoiceStatusClass(s: InvoiceStatus): string {
    if (s === 'VEROEFFENTLICHT') return 'stamp--positive';
    return s === 'VERWORFEN' ? 'stamp--warn' : '';
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
