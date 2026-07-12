import { DatePipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
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
import { BelegService } from '../../core/beleg.service';
import { PropertyService } from '../../core/property.service';
import { ProjektService } from '../../core/projekt.service';
import { AuftragService } from '../../core/auftrag.service';
import { AuthService } from '../../core/auth.service';
import {
  AnrechenbarerAbschlag,
  InvoiceCreate,
  InvoicePage,
  InvoiceStatus,
  InvoiceType,
  LineType,
  QuoteCreate,
  QuoteLineInput,
  QuotePage,
  QuoteStatus,
} from '../../core/beleg.model';
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

type Modus = 'angebote' | 'rechnungen';
type Meldung = { art: 'erfolg' | 'fehler'; text: string };

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; modus: 'angebote'; data: QuotePage }
  | { kind: 'ready'; modus: 'rechnungen'; data: InvoicePage }
  | VerbotenState
  | { kind: 'error' };

const TEXT_LINE_TYPES: LineType[] = ['TEXT', 'ZWISCHENSUMME'];

@Component({
  selector: 'app-dokumente',
  imports: [
    DatePipe,
    RouterLink,
    KeinZugriff,
    ReactiveFormsModule,
    Dialog,
    Feld,
    ReferenzWahl,
  ],
  templateUrl: './dokumente.html',
  styleUrl: './dokumente.scss',
})
export class Dokumente {
  private readonly svc = inject(BelegService);
  private readonly propertySvc = inject(PropertyService);
  private readonly projektSvc = inject(ProjektService);
  private readonly auftragSvc = inject(AuftragService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  protected readonly pageSize = 20;
  protected readonly modus = signal<Modus>('angebote');

  protected readonly query = signal('');
  protected readonly page = signal(1);
  protected readonly state = signal<ViewState>({ kind: 'loading' });

  protected readonly skeletons = Array.from({ length: 6 });

  // --- Anlegen (GoBD-Belege) ----------------------------------------------
  protected readonly darfAnlegen = computed(() => this.auth.darf('invoicing', 'ANLEGEN'));
  protected readonly meldung = signal<Meldung | null>(null);
  /** Offener Anlage-Dialog ('angebot' | 'rechnung') oder null. */
  protected readonly neuArt = signal<'angebot' | 'rechnung' | null>(null);
  protected readonly neuLaedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);

  protected readonly lineTypeOptionen: FeldOption[] = [
    { wert: 'MATERIAL', label: 'Material' },
    { wert: 'ARBEITSZEIT', label: 'Arbeitszeit' },
    { wert: 'PAUSCHALE', label: 'Pauschale' },
    { wert: 'FREMDLEISTUNG', label: 'Fremdleistung' },
    { wert: 'FAHRT', label: 'Fahrt' },
    { wert: 'ZUSCHLAG', label: 'Zuschlag' },
    { wert: 'TEXT', label: 'Textzeile' },
    { wert: 'ZWISCHENSUMME', label: 'Zwischensumme' },
  ];
  protected readonly taxCodeOptionen: FeldOption[] = [
    { wert: 'DE_19', label: 'USt 19 %' },
    { wert: 'DE_7', label: 'USt 7 %' },
    { wert: 'DE_0', label: 'Steuerfrei (0 %)' },
    { wert: 'DE_13B', label: '§13b UStG (Reverse Charge)' },
  ];
  protected readonly invoiceTypeOptionen: FeldOption[] = [
    { wert: 'RECHNUNG', label: 'Rechnung' },
    { wert: 'ABSCHLAGSRECHNUNG', label: 'Abschlagsrechnung' },
    { wert: 'TEILRECHNUNG', label: 'Teilrechnung' },
    { wert: 'SCHLUSSRECHNUNG', label: 'Schlussrechnung' },
  ];

  /** Liegenschaftssuche (Pflicht-Objektbezug des Belegs). */
  protected readonly liegenschaftSuche: RefSuche = (q) =>
    this.propertySvc.list({ page: 1, page_size: 20, q }).pipe(
      map((p) =>
        p.items.map((o) => ({ id: o.id, label: o.name, sub: `${o.property_number} · ${o.city}` })),
      ),
    );
  /** Projektsuche (optionaler Bezug). */
  protected readonly projektSuche: RefSuche = (q) =>
    this.projektSvc.list({ page: 1, page_size: 20, q }).pipe(
      map((p) =>
        p.items.map((o) => ({ id: o.id, label: o.name, sub: o.project_number })),
      ),
    );
  /** Auftragssuche (optionaler Bezug der Rechnung). */
  protected readonly auftragSuche: RefSuche = (q) =>
    this.auftragSvc.list({ page: 1, page_size: 20, q }).pipe(
      map((p) =>
        p.items.map((o) => ({ id: o.id, label: o.title, sub: o.order_number })),
      ),
    );

  protected readonly neuForm = this.fb.group({
    title: this.fb.control('', { nonNullable: true }),
    property_id: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    project_id: this.fb.control('', { nonNullable: true }),
    work_order_id: this.fb.control('', { nonNullable: true }),
    invoice_type: this.fb.control('RECHNUNG', { nonNullable: true }),
    quote_date: this.fb.control('', { nonNullable: true }),
    valid_until_date: this.fb.control('', { nonNullable: true }),
    invoice_date: this.fb.control('', { nonNullable: true }),
    due_date: this.fb.control('', { nonNullable: true }),
    lines: this.fb.array<FormGroup>([]),
  });

  // --- Anrechnung der Abschläge (nur SCHLUSSRECHNUNG) ----------------------
  // Die Schlussrechnung zieht die bereits gestellten Abschläge desselben Auftrags
  // ab. Die anrechenbaren Belege liefert der Server (veröffentlicht, nicht
  // storniert, noch nicht in einer veröffentlichten Schlussrechnung); der
  // Regelfall ist „alle anrechnen", deshalb sind sie vorausgewählt. Die
  // negativen Anrechnungspositionen erzeugt der Server — hier wird nichts
  // gerechnet (der Server ist die verbindliche Rechenstelle).
  protected readonly abschlaege = signal<AnrechenbarerAbschlag[]>([]);
  protected readonly abschlaegeLaedt = signal(false);
  protected readonly gewaehlteAbschlaege = signal<string[]>([]);
  protected readonly istSchlussrechnung = signal(false);
  private abschlagReqId = 0;

  protected readonly anrechnungBrutto = computed(() => {
    const gewaehlt = new Set(this.gewaehlteAbschlaege());
    return this.abschlaege()
      .filter((a) => gewaehlt.has(a.id))
      .reduce((s, a) => s + Number(a.gross_total ?? 0), 0);
  });

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
    if (s.kind === 'forbidden') return 'Keine Berechtigung für die Dokumente.';
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
    // Belegart oder Auftrag geändert → anrechenbare Abschläge neu ermitteln.
    this.neuForm.controls.invoice_type.valueChanges
      .pipe(takeUntilDestroyed())
      .subscribe(() => this.abschlaegeLaden());
    this.neuForm.controls.work_order_id.valueChanges
      .pipe(takeUntilDestroyed())
      .subscribe(() => this.abschlaegeLaden());
    this.fetch();
  }

  private abschlaegeLaden(): void {
    const typ = this.neuForm.controls.invoice_type.value;
    const auftrag = this.neuForm.controls.work_order_id.value;
    const istSr = this.neuArt() === 'rechnung' && typ === 'SCHLUSSRECHNUNG';
    this.istSchlussrechnung.set(istSr);
    const id = ++this.abschlagReqId;
    this.abschlaege.set([]);
    this.gewaehlteAbschlaege.set([]);
    if (!istSr || !auftrag) {
      this.abschlaegeLaedt.set(false);
      return;
    }
    this.abschlaegeLaedt.set(true);
    this.svc.anrechenbareAbschlaege(auftrag).subscribe({
      next: (liste) => {
        if (id !== this.abschlagReqId) return;
        this.abschlaege.set(liste);
        // Vorauswahl: alle. Wer schlussrechnet, rechnet die offenen Abschläge
        // desselben Auftrags an; Ausnahmen hakt man ab.
        this.gewaehlteAbschlaege.set(liste.map((a) => a.id));
        this.abschlaegeLaedt.set(false);
      },
      error: () => {
        if (id !== this.abschlagReqId) return;
        this.abschlaege.set([]);
        this.abschlaegeLaedt.set(false);
      },
    });
  }

  abschlagGewaehlt(id: string): boolean {
    return this.gewaehlteAbschlaege().includes(id);
  }

  abschlagUmschalten(id: string, gewaehlt: boolean): void {
    this.gewaehlteAbschlaege.update((ids) =>
      gewaehlt ? [...new Set([...ids, id])] : ids.filter((x) => x !== id),
    );
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
        error: (err) => {
          if (id === this.reqId) this.state.set(fehlerState(err));
        },
      });
    } else {
      this.svc.listInvoices(query).subscribe({
        next: (data) => {
          if (id === this.reqId) this.state.set({ kind: 'ready', modus, data });
        },
        error: (err) => {
          if (id === this.reqId) this.state.set(fehlerState(err));
        },
      });
    }
  }

  // ---- Anlegen ------------------------------------------------------------
  /** FormArray der Positionen (typisiert für den Zugriff im Template). */
  get lines(): FormArray<FormGroup> {
    return this.neuForm.controls.lines;
  }

  zeilen(): FormGroup[] {
    return this.lines.controls;
  }

  private zeileGruppe(): FormGroup {
    return this.fb.group({
      line_type: this.fb.control('MATERIAL', { nonNullable: true }),
      description: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
      quantity: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
      unit: this.fb.control('', { nonNullable: true }),
      unit_price: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
      tax_code: this.fb.control('DE_19', { nonNullable: true }),
    });
  }

  zeileHinzufuegen(): void {
    this.lines.push(this.zeileGruppe());
  }

  zeileEntfernen(i: number): void {
    this.lines.removeAt(i);
  }

  istTextZeile(g: FormGroup): boolean {
    return TEXT_LINE_TYPES.includes(g.controls['line_type'].value as LineType);
  }

  neuOeffnen(art: 'angebot' | 'rechnung'): void {
    this.neuForm.reset({
      title: '',
      property_id: '',
      project_id: '',
      work_order_id: '',
      invoice_type: 'RECHNUNG',
      quote_date: '',
      valid_until_date: '',
      invoice_date: '',
      due_date: '',
    });
    // Titel ist nur für Angebote ein Pflichtfeld (die Rechnung trägt keinen).
    const titel = this.neuForm.controls.title;
    if (art === 'angebot') titel.setValidators([Validators.required, Validators.maxLength(200)]);
    else titel.clearValidators();
    titel.updateValueAndValidity();
    this.lines.clear();
    this.zeileHinzufuegen();
    this.formularMeldung.set(null);
    this.neuArt.set(art);
    this.abschlaegeLaden();
  }

  neuSchliessen(): void {
    if (!this.neuLaedt()) this.neuArt.set(null);
  }

  /** Pflichtfelder je Position prüfen (Nicht-Text-Zeilen: Menge/Preis/Steuer). */
  private zeilenPruefen(): boolean {
    let ok = true;
    for (const g of this.lines.controls) {
      if (this.istTextZeile(g)) continue;
      for (const feld of ['quantity', 'unit_price']) {
        const c = g.controls[feld];
        if (!String(c.value ?? '').trim()) {
          c.setErrors({ ...(c.errors ?? {}), required: true });
          c.markAsTouched();
          ok = false;
        }
      }
    }
    return ok;
  }

  neuAbsenden(): void {
    const art = this.neuArt();
    if (!art || this.neuLaedt()) return;
    serverFehlerZuruecksetzen(this.neuForm);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.neuForm);
    const zeilenOk = this.zeilenPruefen();
    if (this.neuForm.invalid || !zeilenOk) return;
    if (this.lines.length === 0) {
      this.formularMeldung.set('Bitte mindestens eine Position erfassen.');
      return;
    }

    const v = this.neuForm.getRawValue();
    const lines: QuoteLineInput[] = this.lines.controls.map((g) => {
      const lt = g.controls['line_type'].value as LineType;
      const desc = String(g.controls['description'].value ?? '').trim();
      if (TEXT_LINE_TYPES.includes(lt)) {
        return { line_type: lt, description: desc };
      }
      return {
        line_type: lt,
        description: desc,
        quantity: deZuApiDezimal(g.controls['quantity'].value),
        unit: String(g.controls['unit'].value ?? '').trim() || null,
        unit_price: deZuApiDezimal(g.controls['unit_price'].value),
        tax_code: g.controls['tax_code'].value as string,
      };
    });

    this.neuLaedt.set(true);
    if (art === 'angebot') {
      const payload: QuoteCreate = {
        property_id: v.property_id,
        title: v.title.trim(),
        project_id: v.project_id || null,
        quote_date: v.quote_date || null,
        valid_until_date: v.valid_until_date || null,
        lines,
      };
      this.svc.createQuote(payload).subscribe({
        next: (q) => {
          this.neuLaedt.set(false);
          this.neuArt.set(null);
          this.meldung.set({
            art: 'erfolg',
            text: `Angebotsentwurf angelegt (brutto ${this.euro(q.gross_total)}, vom Server berechnet).`,
          });
          this.modus.set('angebote');
          this.query.set('');
          this.page.set(1);
          this.fetch();
        },
        error: (err) => {
          this.neuLaedt.set(false);
          this.formularMeldung.set(apiFehlerZuweisen(err, this.neuForm).formular);
        },
      });
    } else {
      const payload: InvoiceCreate = {
        property_id: v.property_id,
        invoice_type: v.invoice_type as InvoiceType,
        project_id: v.project_id || null,
        work_order_id: v.work_order_id || null,
        invoice_date: v.invoice_date || null,
        due_date: v.due_date || null,
        lines,
        // Nur die Schlussrechnung rechnet an; sonst lehnt der Server ab (422).
        ...(v.invoice_type === 'SCHLUSSRECHNUNG'
          ? { advance_invoice_ids: this.gewaehlteAbschlaege() }
          : {}),
      };
      this.svc.createInvoice(payload).subscribe({
        next: (inv) => {
          this.neuLaedt.set(false);
          this.neuArt.set(null);
          this.meldung.set({
            art: 'erfolg',
            text: `Rechnungsentwurf angelegt (brutto ${this.euro(inv.gross_total)}, vom Server berechnet).`,
          });
          this.modus.set('rechnungen');
          this.query.set('');
          this.page.set(1);
          this.fetch();
        },
        error: (err) => {
          this.neuLaedt.set(false);
          this.formularMeldung.set(apiFehlerZuweisen(err, this.neuForm).formular);
        },
      });
    }
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
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
