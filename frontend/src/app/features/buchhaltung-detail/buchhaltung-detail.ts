import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { Dialog } from '../../shared/dialog/dialog';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { Feld } from '../../shared/formular/feld';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';
import { deZuApiDezimal, dezimalValidator } from '../../shared/formular/dezimal';
import { VerbotenState, fehlerDetail, fehlerState, istVerboten } from '../../shared/http-fehler';
import { BuchhaltungService } from '../../core/buchhaltung.service';
import { BelegService } from '../../core/beleg.service';
import { AuthService } from '../../core/auth.service';
import { QuoteLine } from '../../core/beleg.model';
import {
  OpenItemDetail,
  PAYMENT_TYPES,
  PaymentStatus,
  euro,
  invoiceTypeLabel,
  paymentStatusClass,
  paymentStatusLabel,
  paymentTypeLabel,
} from '../../core/buchhaltung.model';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: OpenItemDetail }
  | VerbotenState
  | { kind: 'error' };

type Meldung = { art: 'erfolg' | 'fehler'; text: string };

@Component({
  selector: 'app-buchhaltung-detail',
  imports: [
    Mappe,
    RouterLink,
    KeinZugriff,
    Dialog,
    Bestaetigung,
    Feld,
    ReactiveFormsModule,
  ],
  templateUrl: './buchhaltung-detail.html',
  styleUrl: './buchhaltung-detail.scss',
})
export class BuchhaltungDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(BuchhaltungService);
  private readonly belege = inject(BelegService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  protected readonly tab = signal('uebersicht');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  protected readonly tabs: MappeTab[] = [
    { id: 'uebersicht', label: 'Übersicht' },
    { id: 'zahlungen', label: 'Zahlungen' },
    { id: 'mahnverlauf', label: 'Mahnverlauf' },
  ];

  protected readonly paymentTypes = PAYMENT_TYPES;

  // --- Rechte --------------------------------------------------------------
  protected readonly darfZahlung = computed(() => this.auth.darf('invoicing', 'AENDERN'));
  protected readonly darfMahnung = computed(() => this.auth.darf('invoicing', 'VERSENDEN'));
  protected readonly darfStornieren = computed(() => this.auth.darf('invoicing', 'STORNIEREN'));

  protected readonly meldung = signal<Meldung | null>(null);

  /** Ein bereits vorhandener Stornobeleg schließt weiteres Stornieren aus. */
  protected readonly hatStorno = computed(() =>
    (this.daten()?.credit_notes ?? []).some((c) => c.invoice_type === 'STORNO'),
  );

  // --- Zahlung erfassen ----------------------------------------------------
  protected readonly zahlungOffen = signal(false);
  protected readonly zahlungLaedt = signal(false);
  protected readonly zahlungMeldung = signal<string | null>(null);
  protected readonly zahlungForm = this.fb.group({
    amount: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, dezimalValidator],
    }),
    paid_at: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    payment_type: this.fb.control('ZAHLUNG', { nonNullable: true }),
    external_reference: this.fb.control('', { nonNullable: true }),
  });

  // --- Mahnung erzeugen ----------------------------------------------------
  protected readonly mahnungOffen = signal(false);
  protected readonly mahnungLaedt = signal(false);
  protected readonly mahnungMeldung = signal<string | null>(null);
  protected readonly mahnungForm = this.fb.group({
    issued_at: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    note: this.fb.control('', { nonNullable: true }),
  });
  /** Nächste (lückenlose) Mahnstufe = aktuelle + 1; die DB erzwingt max+1. */
  protected readonly naechsteStufe = computed(() => (this.daten()?.dunning_level ?? 0) + 1);

  // --- Storno --------------------------------------------------------------
  protected readonly stornoOffen = signal(false);
  protected readonly stornoLaedt = signal(false);

  // --- Rechnungskorrektur --------------------------------------------------
  protected readonly korrekturOffen = signal(false);
  protected readonly korrekturLaedt = signal(false);
  protected readonly korrekturMeldung = signal<string | null>(null);
  protected readonly korrekturLinesLaden = signal(false);
  protected readonly korrekturLines = signal<QuoteLine[]>([]);
  protected readonly korrekturAuswahl = signal<Set<number>>(new Set());

  private readonly dateFmt = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  });

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
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
    this.svc.getOpenItem(id).subscribe({
      next: (data) => {
        if (rid === this.reqId) this.state.set({ kind: 'ready', data });
      },
      error: (err) => {
        if (rid === this.reqId) this.state.set(fehlerState(err));
      },
    });
  }

  private neuLaden(): void {
    const id = this.daten()?.id ?? this.route.snapshot.paramMap.get('id');
    if (id) this.load(id);
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  private aktionsFehler(err: unknown): string {
    if (istVerboten(err)) return fehlerDetail(err) ?? 'Keine Berechtigung für diese Aktion.';
    return fehlerDetail(err) ?? 'Die Aktion ist fehlgeschlagen. Bitte erneut versuchen.';
  }

  // ---- Zahlung erfassen ---------------------------------------------------
  zahlungOeffnen(): void {
    this.zahlungForm.reset({
      amount: '',
      paid_at: this.heute(),
      payment_type: 'ZAHLUNG',
      external_reference: '',
    });
    this.zahlungMeldung.set(null);
    this.meldung.set(null);
    this.zahlungOffen.set(true);
  }

  zahlungSchliessen(): void {
    if (!this.zahlungLaedt()) this.zahlungOffen.set(false);
  }

  zahlungAbsenden(): void {
    const d = this.daten();
    if (!d || this.zahlungLaedt()) return;
    serverFehlerZuruecksetzen(this.zahlungForm);
    this.zahlungMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.zahlungForm);
    if (this.zahlungForm.invalid) return;

    const v = this.zahlungForm.getRawValue();
    this.zahlungLaedt.set(true);
    this.svc
      .recordPayment(d.id, {
        amount: deZuApiDezimal(v.amount),
        paid_at: v.paid_at,
        payment_type: v.payment_type,
        external_reference: v.external_reference.trim() || null,
      })
      .subscribe({
        next: () => {
          this.zahlungLaedt.set(false);
          this.zahlungOffen.set(false);
          this.meldung.set({ art: 'erfolg', text: 'Zahlung wurde erfasst.' });
          this.neuLaden();
        },
        error: (err) => {
          this.zahlungLaedt.set(false);
          this.zahlungMeldung.set(apiFehlerZuweisen(err, this.zahlungForm).formular);
        },
      });
  }

  // ---- Mahnung erzeugen ---------------------------------------------------
  mahnungOeffnen(): void {
    this.mahnungForm.reset({ issued_at: this.heute(), note: '' });
    this.mahnungMeldung.set(null);
    this.meldung.set(null);
    this.mahnungOffen.set(true);
  }

  mahnungSchliessen(): void {
    if (!this.mahnungLaedt()) this.mahnungOffen.set(false);
  }

  mahnungAbsenden(): void {
    const d = this.daten();
    if (!d || this.mahnungLaedt()) return;
    serverFehlerZuruecksetzen(this.mahnungForm);
    this.mahnungMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.mahnungForm);
    if (this.mahnungForm.invalid) return;

    const v = this.mahnungForm.getRawValue();
    this.mahnungLaedt.set(true);
    this.svc
      .issueDunning(d.id, {
        level: this.naechsteStufe(),
        issued_at: v.issued_at,
        note: v.note.trim() || null,
      })
      .subscribe({
        next: (notice) => {
          this.mahnungLaedt.set(false);
          this.mahnungOffen.set(false);
          this.meldung.set({
            art: 'erfolg',
            text: `Mahnung erzeugt: ${notice.level}. Stufe (${notice.label}).`,
          });
          this.neuLaden();
        },
        error: (err) => {
          this.mahnungLaedt.set(false);
          this.mahnungMeldung.set(apiFehlerZuweisen(err, this.mahnungForm).formular);
        },
      });
  }

  // ---- Storno -------------------------------------------------------------
  stornoFragen(): void {
    this.meldung.set(null);
    this.stornoOffen.set(true);
  }

  stornoAbbrechen(): void {
    if (!this.stornoLaedt()) this.stornoOffen.set(false);
  }

  stornoBestaetigen(): void {
    const d = this.daten();
    if (!d || this.stornoLaedt()) return;
    this.stornoLaedt.set(true);
    this.svc.cancelInvoice(d.id).subscribe({
      next: (credit) => {
        this.stornoLaedt.set(false);
        this.stornoOffen.set(false);
        this.meldung.set({
          art: 'erfolg',
          text: `Stornobeleg ${credit.invoice_number ?? '—'} wurde erzeugt.`,
        });
        this.neuLaden();
      },
      error: (err) => {
        this.stornoLaedt.set(false);
        this.stornoOffen.set(false);
        this.meldung.set({ art: 'fehler', text: this.aktionsFehler(err) });
      },
    });
  }

  // ---- Rechnungskorrektur -------------------------------------------------
  korrekturOeffnen(): void {
    const d = this.daten();
    if (!d) return;
    this.korrekturMeldung.set(null);
    this.meldung.set(null);
    this.korrekturAuswahl.set(new Set());
    this.korrekturLines.set([]);
    this.korrekturOffen.set(true);
    // Positionen liegen nicht im offenen Posten — aus dem Rechnungsbeleg holen.
    this.korrekturLinesLaden.set(true);
    this.belege.getInvoice(d.id).subscribe({
      next: (inv) => {
        this.korrekturLinesLaden.set(false);
        this.korrekturLines.set(inv.lines);
      },
      error: () => {
        this.korrekturLinesLaden.set(false);
        this.korrekturMeldung.set('Die Positionen konnten nicht geladen werden.');
      },
    });
  }

  korrekturSchliessen(): void {
    if (!this.korrekturLaedt()) this.korrekturOffen.set(false);
  }

  korrekturUmschalten(position: number, checked: boolean): void {
    const next = new Set(this.korrekturAuswahl());
    if (checked) next.add(position);
    else next.delete(position);
    this.korrekturAuswahl.set(next);
  }

  korrekturAbsenden(): void {
    const d = this.daten();
    if (!d || this.korrekturLaedt()) return;
    const positions = [...this.korrekturAuswahl()].sort((a, b) => a - b);
    if (positions.length === 0) {
      this.korrekturMeldung.set('Bitte mindestens eine Position wählen.');
      return;
    }
    this.korrekturMeldung.set(null);
    this.korrekturLaedt.set(true);
    this.svc.correctInvoice(d.id, { positions }).subscribe({
      next: (credit) => {
        this.korrekturLaedt.set(false);
        this.korrekturOffen.set(false);
        this.meldung.set({
          art: 'erfolg',
          text: `Rechnungskorrektur ${credit.invoice_number ?? '—'} (Gutschrift) wurde erzeugt.`,
        });
        this.neuLaden();
      },
      error: (err) => {
        this.korrekturLaedt.set(false);
        this.korrekturMeldung.set(this.aktionsFehler(err));
      },
    });
  }

  private heute(): string {
    return new Date().toISOString().slice(0, 10);
  }

  // ---- Darstellungshelfer -------------------------------------------------
  statusLabel(s: PaymentStatus): string {
    return paymentStatusLabel(s);
  }
  statusClass(s: PaymentStatus): string {
    return paymentStatusClass(s);
  }
  typeLabel(t: string): string {
    return invoiceTypeLabel(t);
  }
  paymentTypeLabel(t: string): string {
    return paymentTypeLabel(t);
  }
  euro(v: string | null): string {
    return euro(v);
  }
  d(iso: string | null): string {
    if (!iso) return '—';
    return this.dateFmt.format(new Date(iso));
  }
}
