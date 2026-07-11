import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormControl, FormGroup, ReactiveFormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Subject, debounceTime, distinctUntilChanged } from 'rxjs';
import { BuchhaltungService } from '../../core/buchhaltung.service';
import {
  OpenItemPage,
  PaymentStatus,
  euro,
  invoiceTypeLabel,
  paymentStatusClass,
  paymentStatusLabel,
} from '../../core/buchhaltung.model';
import { csvDownloadAusloesen } from '../../core/auswertungen.service';
import { AuthService } from '../../core/auth.service';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld } from '../../shared/formular/feld';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: OpenItemPage }
  | VerbotenState
  | { kind: 'error' };

type Segment = { value: PaymentStatus | null; label: string };

@Component({
  selector: 'app-buchhaltung',
  imports: [RouterLink, KeinZugriff, ReactiveFormsModule, Dialog, Feld],
  templateUrl: './buchhaltung.html',
  styleUrl: './buchhaltung.scss',
})
export class Buchhaltung {
  private readonly svc = inject(BuchhaltungService);
  private readonly auth = inject(AuthService);

  protected readonly pageSize = 20;
  protected readonly segments: Segment[] = [
    { value: null, label: 'Alle' },
    { value: 'OFFEN', label: 'Offen' },
    { value: 'TEILZAHLUNG', label: 'Teilzahlung' },
    { value: 'BEZAHLT', label: 'Bezahlt' },
  ];

  protected readonly query = signal('');
  protected readonly status = signal<PaymentStatus | null>(null);
  protected readonly page = signal(1);
  protected readonly state = signal<ViewState>({ kind: 'loading' });

  protected readonly skeletons = Array.from({ length: 6 });

  private readonly searchInput$ = new Subject<string>();
  private reqId = 0;

  private readonly dateFmt = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  });

  protected readonly totalPages = computed(() => {
    const s = this.state();
    if (s.kind !== 'ready') return 1;
    return Math.max(1, Math.ceil(s.data.total / s.data.page_size));
  });

  protected readonly resultSummary = computed(() => {
    const s = this.state();
    if (s.kind === 'loading') return 'Offene Posten werden geladen.';
    if (s.kind === 'forbidden') return 'Keine Berechtigung für die Buchhaltung.';
    if (s.kind === 'error') return 'Offene Posten konnten nicht geladen werden.';
    const t = s.data.total;
    if (t === 0) return 'Keine Rechnungen für diese Auswahl.';
    return `${t} ${t === 1 ? 'Rechnung' : 'Rechnungen'} gefunden, Seite ${s.data.page} von ${this.totalPages()}.`;
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

  selectSegment(value: PaymentStatus | null): void {
    if (this.status() === value) return;
    this.status.set(value);
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

  // ---- DATEV-Export -------------------------------------------------------
  protected readonly datevOffen = signal(false);
  protected readonly datevBusy = signal(false);
  protected readonly datevFehler = signal<string | null>(null);
  protected readonly datevForm = new FormGroup({
    von: new FormControl<string>('', { nonNullable: true }),
    bis: new FormControl<string>('', { nonNullable: true }),
  });

  /** Aktion nur zeigen, wenn das Recht besteht (der Server lehnt sonst mit 403 ab). */
  protected darfExportieren(): boolean {
    return this.auth.darf('invoicing', 'LESEN');
  }

  datevOeffnen(): void {
    // Vorbelegung: laufendes Kalenderjahr bis heute (Belegdatum-TTMM verlangt
    // einen Zeitraum innerhalb eines Jahres).
    const heute = new Date();
    const iso = (d: Date) => d.toISOString().slice(0, 10);
    this.datevForm.setValue({
      von: `${heute.getFullYear()}-01-01`,
      bis: iso(heute),
    });
    this.datevFehler.set(null);
    this.datevOffen.set(true);
  }

  datevSchliessen(): void {
    if (this.datevBusy()) return;
    this.datevOffen.set(false);
  }

  datevHerunterladen(): void {
    if (this.datevBusy()) return;
    const von = this.datevForm.controls.von.value;
    const bis = this.datevForm.controls.bis.value;
    if (!von || !bis) {
      this.datevFehler.set('Bitte einen Zeitraum (von und bis) wählen.');
      return;
    }
    this.datevBusy.set(true);
    this.datevFehler.set(null);
    this.svc.datevExport(von, bis).subscribe({
      next: (blob) => {
        csvDownloadAusloesen(blob, `EXTF_Buchungsstapel_${von}_${bis}.csv`);
        this.datevBusy.set(false);
        this.datevOffen.set(false);
      },
      error: (err) => {
        this.datevBusy.set(false);
        void this.datevFehlerAnzeigen(err);
      },
    });
  }

  /** Bei responseType 'blob' ist der 422-Fehlerkörper ein Blob — als Text lesen
   * und die Servermeldung (detail) herausziehen. */
  private async datevFehlerAnzeigen(err: unknown): Promise<void> {
    const blob = (err as { error?: unknown })?.error;
    if (blob instanceof Blob) {
      try {
        const detail = JSON.parse(await blob.text())?.detail;
        if (typeof detail === 'string') {
          this.datevFehler.set(detail);
          return;
        }
      } catch {
        /* kein JSON-Körper → generische Meldung unten */
      }
    }
    this.datevFehler.set(
      fehlerDetail(err) ?? 'Der DATEV-Export konnte nicht erstellt werden.',
    );
  }

  private fetch(): void {
    const id = ++this.reqId;
    this.state.set({ kind: 'loading' });
    this.svc
      .listOpenItems({
        page: this.page(),
        page_size: this.pageSize,
        q: this.query(),
        payment_status: this.status(),
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
  statusLabel(s: PaymentStatus): string {
    return paymentStatusLabel(s);
  }
  statusClass(s: PaymentStatus): string {
    return paymentStatusClass(s);
  }
  euro(v: string | null): string {
    return euro(v);
  }
  typeLabel(t: string): string {
    return invoiceTypeLabel(t);
  }
  isCredit(t: string): boolean {
    return t === 'GUTSCHRIFT' || t === 'STORNO';
  }
  d(iso: string | null): string {
    if (!iso) return '—';
    return this.dateFmt.format(new Date(iso));
  }
}
