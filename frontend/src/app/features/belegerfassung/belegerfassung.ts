import { Component, ElementRef, computed, inject, signal, viewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import {
  FormArray,
  FormBuilder,
  FormGroup,
  ReactiveFormsModule,
  Validators,
} from '@angular/forms';
import { Subject, debounceTime, distinctUntilChanged, map } from 'rxjs';
import { BelegerfassungService } from '../../core/belegerfassung.service';
import { PartyService } from '../../core/party.service';
import { AuthService } from '../../core/auth.service';
import {
  CostCenter,
  LedgerAccount,
  ReceiptCreate,
  ReceiptLineInput,
  ReceiptPage,
  ReceiptStatus,
  euro,
  receiptStatusClass,
  receiptStatusLabel,
} from '../../core/belegerfassung.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { ReferenzWahl, RefSuche } from '../../shared/formular/referenz-wahl';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';
import { deZuApiDezimal, dezimalValidator } from '../../shared/formular/dezimal';

type Meldung = { art: 'erfolg' | 'fehler'; text: string };

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: ReceiptPage }
  | VerbotenState
  | { kind: 'error' };

type Segment = { value: ReceiptStatus | null; label: string };

const TAX_CODE_OPTIONEN: FeldOption[] = [
  { wert: 'DE_19', label: 'USt 19 %' },
  { wert: 'DE_7', label: 'USt 7 %' },
  { wert: 'DE_0', label: 'Steuerfrei (0 %)' },
  { wert: 'DE_13B', label: '§13b UStG (Reverse Charge)' },
];

/**
 * Belegerfassung — Eingangsbelege (Schema `accounting`). Liste mit Suche,
 * Statusfilter und Pagination; Anlege-Dialog mit Positionszeilen. Die
 * Belegnummer (EB-00001) vergibt die DB, die Summen rechnet der Server — der
 * Editor nimmt bewusst keine Summe vorweg.
 */
@Component({
  selector: 'app-belegerfassung',
  imports: [RouterLink, KeinZugriff, ReactiveFormsModule, Dialog, Feld, ReferenzWahl],
  templateUrl: './belegerfassung.html',
  styleUrl: './belegerfassung.scss',
})
export class Belegerfassung {
  private readonly svc = inject(BelegerfassungService);
  private readonly partySvc = inject(PartyService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  protected readonly pageSize = 20;
  protected readonly segments: Segment[] = [
    { value: null, label: 'Alle' },
    { value: 'ERFASST', label: 'Erfasst' },
    { value: 'GEPRUEFT', label: 'Geprüft' },
    { value: 'FREIGEGEBEN', label: 'Freigegeben' },
    { value: 'GEBUCHT', label: 'Gebucht' },
    { value: 'ABGELEHNT', label: 'Abgelehnt' },
  ];

  protected readonly query = signal('');
  protected readonly status = signal<ReceiptStatus | null>(null);
  protected readonly page = signal(1);
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly skeletons = Array.from({ length: 6 });

  protected readonly taxCodeOptionen = TAX_CODE_OPTIONEN;

  // --- Anlegen -------------------------------------------------------------
  protected readonly darfAnlegen = computed(() => this.auth.darf('accounting', 'ANLEGEN'));
  protected readonly meldung = signal<Meldung | null>(null);
  protected readonly neuOffen = signal(false);
  protected readonly neuLaedt = signal(false);
  private readonly fehlerBanner = viewChild<ElementRef<HTMLElement>>('fehlerBanner');
  protected readonly formularMeldung = signal<string | null>(null);

  // Kontierungs-Stammdaten für die Positions-Selects (nur aktive).
  private readonly ledgers = signal<LedgerAccount[]>([]);
  private readonly costCenters = signal<CostCenter[]>([]);

  protected readonly ledgerOptionen = computed<FeldOption[]>(() =>
    this.ledgers().map((a) => ({ wert: a.id, label: `${a.account_number} — ${a.label}` })),
  );
  protected readonly costCenterOptionen = computed<FeldOption[]>(() =>
    this.costCenters().map((c) => ({ wert: c.id, label: `${c.code} — ${c.label}` })),
  );

  /** Lieferantensuche (Pflicht) über den Kontaktstamm. */
  protected readonly lieferantSuche: RefSuche = (q) =>
    this.partySvc.list({ page: 1, page_size: 20, q }).pipe(
      map((p) => p.items.map((o) => ({ id: o.id, label: o.display_name }))),
    );

  protected readonly neuForm = this.fb.group({
    supplier_party_id: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    receipt_date: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    received_date: this.fb.control('', { nonNullable: true }),
    due_date: this.fb.control('', { nonNullable: true }),
    supplier_invoice_number: this.fb.control('', { nonNullable: true }),
    currency: this.fb.control('EUR', { nonNullable: true }),
    notes: this.fb.control('', { nonNullable: true }),
    lines: this.fb.array<FormGroup>([]),
  });

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
    if (s.kind === 'loading') return 'Eingangsbelege werden geladen.';
    if (s.kind === 'forbidden') return 'Keine Berechtigung für die Belegerfassung.';
    if (s.kind === 'error') return 'Eingangsbelege konnten nicht geladen werden.';
    const t = s.data.total;
    if (t === 0) return 'Keine Belege für diese Auswahl.';
    return `${t} ${t === 1 ? 'Beleg' : 'Belege'} gefunden, Seite ${s.data.page} von ${this.totalPages()}.`;
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
    this.kontierungLaden();
  }

  onSearch(value: string): void {
    this.searchInput$.next(value);
  }

  selectSegment(value: ReceiptStatus | null): void {
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

  private fetch(): void {
    const id = ++this.reqId;
    this.state.set({ kind: 'loading' });
    this.svc
      .listReceipts({
        page: this.page(),
        page_size: this.pageSize,
        q: this.query(),
        status: this.status(),
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

  /** Aktive Konten/Kostenstellen für die Kontierungs-Selects (best effort). */
  private kontierungLaden(): void {
    this.svc.listLedgerAccounts(false).subscribe({
      next: (a) => this.ledgers.set(a),
      error: () => this.ledgers.set([]),
    });
    this.svc.listCostCenters(false).subscribe({
      next: (c) => this.costCenters.set(c),
      error: () => this.costCenters.set([]),
    });
  }

  // ---- Anlegen ------------------------------------------------------------
  get lines(): FormArray<FormGroup> {
    return this.neuForm.controls.lines;
  }

  zeilen(): FormGroup[] {
    return this.lines.controls;
  }

  private zeileGruppe(): FormGroup {
    return this.fb.group({
      description: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
      quantity: this.fb.control('', {
        nonNullable: true,
        validators: [Validators.required, dezimalValidator],
      }),
      unit: this.fb.control('', { nonNullable: true }),
      unit_price: this.fb.control('', {
        nonNullable: true,
        validators: [Validators.required, dezimalValidator],
      }),
      tax_code: this.fb.control('DE_19', { nonNullable: true }),
      ledger_account_id: this.fb.control('', { nonNullable: true }),
      cost_center_id: this.fb.control('', { nonNullable: true }),
    });
  }

  zeileHinzufuegen(): void {
    this.lines.push(this.zeileGruppe());
  }

  zeileEntfernen(i: number): void {
    this.lines.removeAt(i);
  }

  neuOeffnen(): void {
    this.neuForm.reset({
      supplier_party_id: '',
      receipt_date: '',
      received_date: '',
      due_date: '',
      supplier_invoice_number: '',
      currency: 'EUR',
      notes: '',
    });
    this.lines.clear();
    this.zeileHinzufuegen();
    this.formularMeldung.set(null);
    this.neuOffen.set(true);
  }

  neuSchliessen(): void {
    if (!this.neuLaedt()) this.neuOffen.set(false);
  }

  neuAbsenden(): void {
    if (this.neuLaedt()) return;
    serverFehlerZuruecksetzen(this.neuForm);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.neuForm);
    if (this.neuForm.invalid) return;
    if (this.lines.length === 0) {
      this.formularMeldung.set('Bitte mindestens eine Position erfassen.');
      return;
    }

    const v = this.neuForm.getRawValue();
    const lines: ReceiptLineInput[] = this.lines.controls.map((g) => ({
      description: String(g.controls['description'].value ?? '').trim(),
      quantity: deZuApiDezimal(g.controls['quantity'].value),
      unit_price: deZuApiDezimal(g.controls['unit_price'].value),
      tax_code: String(g.controls['tax_code'].value),
      unit: String(g.controls['unit'].value ?? '').trim() || null,
      ledger_account_id: g.controls['ledger_account_id'].value || null,
      cost_center_id: g.controls['cost_center_id'].value || null,
    }));

    const payload: ReceiptCreate = {
      supplier_party_id: v.supplier_party_id,
      receipt_date: v.receipt_date,
      received_date: v.received_date || null,
      due_date: v.due_date || null,
      supplier_invoice_number: v.supplier_invoice_number.trim() || null,
      currency: v.currency.trim() || 'EUR',
      notes: v.notes.trim() || null,
      lines,
    };

    this.neuLaedt.set(true);
    this.svc.createReceipt(payload).subscribe({
      next: (beleg) => {
        this.neuLaedt.set(false);
        this.neuOffen.set(false);
        this.meldung.set({
          art: 'erfolg',
          text: `Eingangsbeleg ${beleg.receipt_number} angelegt (brutto ${this.euro(beleg.gross_total)}, vom Server berechnet).`,
        });
        this.status.set(null);
        this.query.set('');
        this.page.set(1);
        this.fetch();
      },
      error: (err) => {
        this.neuLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.neuForm).formular);
        this.fehlerZeigen();
      },
    });
  }

  /** Die Fehlermeldung steht oben im Dialog, der Absendeknopf unten — ohne
   * Scrollen bliebe sie ungesehen. `role="alert"` deckt nur Screenreader ab. */
  private fehlerZeigen(): void {
    queueMicrotask(() => {
      const el = this.fehlerBanner()?.nativeElement;
      if (!el) return;
      const ruhig = matchMedia('(prefers-reduced-motion: reduce)').matches;
      el.scrollIntoView({ block: 'nearest', behavior: ruhig ? 'auto' : 'smooth' });
      el.focus({ preventScroll: true });
    });
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  // ---- Darstellungshelfer -------------------------------------------------
  euro(v: string | null): string {
    return euro(v);
  }
  statusLabel(s: ReceiptStatus): string {
    return receiptStatusLabel(s);
  }
  statusClass(s: ReceiptStatus): string {
    return receiptStatusClass(s);
  }
  d(iso: string | null): string {
    if (!iso) return '—';
    return this.dateFmt.format(new Date(iso));
  }
}
