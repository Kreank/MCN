import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import {
  FormArray,
  FormBuilder,
  FormGroup,
  ReactiveFormsModule,
  Validators,
} from '@angular/forms';
import { map } from 'rxjs';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { BelegerfassungService } from '../../core/belegerfassung.service';
import { PartyService } from '../../core/party.service';
import { AuthService } from '../../core/auth.service';
import {
  CostCenter,
  LedgerAccount,
  ReceiptDetail,
  ReceiptLineInput,
  ReceiptStatus,
  ReceiptUpdate,
  euro,
  menge,
  receiptStatusClass,
  receiptStatusLabel,
} from '../../core/belegerfassung.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerDetail, fehlerState, istVerboten } from '../../shared/http-fehler';
import { Dialog } from '../../shared/dialog/dialog';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { ReferenzWahl, RefSuche } from '../../shared/formular/referenz-wahl';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';
import { apiZuDeEingabe, deZuApiDezimal, dezimalValidator } from '../../shared/formular/dezimal';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: ReceiptDetail }
  | VerbotenState
  | { kind: 'error' };

type Meldung = { art: 'erfolg' | 'fehler'; text: string };

/** Ein Statuswechsel als Bestätigungsvorlage. */
interface StatusAktion {
  key: string;
  label: string;
  to_status: ReceiptStatus;
  primary: boolean;
  gefahr: boolean;
  begruendung: boolean;
  titel: string;
  text: string;
}

const A_PRUEFEN: StatusAktion = {
  key: 'pruefen',
  label: 'Als geprüft markieren',
  to_status: 'GEPRUEFT',
  primary: true,
  gefahr: false,
  begruendung: false,
  titel: 'Beleg prüfen?',
  text: 'Der Beleg wird als geprüft markiert und kann anschließend freigegeben werden. Bearbeiten bleibt weiterhin möglich.',
};
const A_FREIGEBEN: StatusAktion = {
  key: 'freigeben',
  label: 'Verbindlich freigeben',
  to_status: 'FREIGEGEBEN',
  primary: true,
  gefahr: true,
  begruendung: false,
  titel: 'Beleg freigeben?',
  text: 'Der Beleg wird freigegeben und damit eingefroren — Kopf und Positionen lassen sich danach nicht mehr ändern.',
};
const A_BUCHEN: StatusAktion = {
  key: 'buchen',
  label: 'Verbindlich buchen',
  to_status: 'GEBUCHT',
  primary: true,
  gefahr: true,
  begruendung: false,
  titel: 'Beleg buchen?',
  text: 'Der Beleg wird gebucht. Das ist der Endzustand; weitere Statuswechsel sind danach nicht mehr möglich.',
};
const A_ZURUECK: StatusAktion = {
  key: 'zuruecksetzen',
  label: 'Freigabe zurücknehmen',
  to_status: 'GEPRUEFT',
  primary: false,
  gefahr: false,
  begruendung: false,
  titel: 'Freigabe zurücknehmen?',
  text: 'Der Beleg kehrt in den Status „Geprüft" zurück und wird wieder bearbeitbar.',
};
const A_ABLEHNEN: StatusAktion = {
  key: 'ablehnen',
  label: 'Beleg ablehnen',
  to_status: 'ABGELEHNT',
  primary: false,
  gefahr: true,
  begruendung: true,
  titel: 'Beleg ablehnen?',
  text: 'Der Beleg wird abgelehnt. Das ist ein Endzustand und begründungspflichtig.',
};

/**
 * Detail-Mappe eines Eingangsbelegs (Schema `accounting`): Positionen mit
 * Kontierung, Statusverlauf und Übersicht, dazu Editor und Statusaktionen.
 *
 * Fachliche Tore setzt der Server durch (422): ab FREIGEGEBEN ist der Beleg
 * eingefroren; die Freigabe verlangt für jede Position ein Buchungskonto;
 * FREIGEGEBEN/GEBUCHT brauchen das Recht accounting/FREIGEBEN. Die UI blendet
 * Aktionen nach `darf(...)` aus und weist vor der Freigabe auf unkontierte
 * Positionen hin — durchgesetzt wird aber serverseitig.
 */
@Component({
  selector: 'app-beleg-eingang-detail',
  imports: [
    Mappe,
    RouterLink,
    KeinZugriff,
    Bestaetigung,
    ReactiveFormsModule,
    Dialog,
    Feld,
    ReferenzWahl,
  ],
  templateUrl: './beleg-eingang-detail.html',
  styleUrl: './beleg-eingang-detail.scss',
})
export class BelegEingangDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(BelegerfassungService);
  private readonly partySvc = inject(PartyService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  protected readonly darfAendern = computed(() => this.auth.darf('accounting', 'AENDERN'));
  protected readonly darfFreigeben = computed(() => this.auth.darf('accounting', 'FREIGEBEN'));

  protected readonly tab = signal('positionen');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  protected readonly taxCodeOptionen: FeldOption[] = [
    { wert: 'DE_19', label: 'USt 19 %' },
    { wert: 'DE_7', label: 'USt 7 %' },
    { wert: 'DE_0', label: 'Steuerfrei (0 %)' },
    { wert: 'DE_13B', label: '§13b UStG (Reverse Charge)' },
  ];

  protected readonly tabs: MappeTab[] = [
    { id: 'positionen', label: 'Positionen' },
    { id: 'verlauf', label: 'Verlauf' },
    { id: 'uebersicht', label: 'Übersicht' },
  ];

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  /** Bearbeitbar nur im Entwurf (ERFASST/GEPRUEFT) — ab FREIGEGEBEN friert die DB ein. */
  protected readonly bearbeitbar = computed(() => {
    const s = this.daten()?.status;
    return s === 'ERFASST' || s === 'GEPRUEFT';
  });

  /** Positionsnummern ohne Buchungskonto (blockieren die Freigabe). */
  protected readonly unkontierte = computed(() => {
    const d = this.daten();
    if (!d) return [] as number[];
    return d.lines.filter((l) => !l.ledger_account_id).map((l) => l.position_number);
  });

  /** Verfügbare Statusaktionen für den aktuellen Status (rechtegefiltert). */
  protected readonly aktionen = computed<StatusAktion[]>(() => {
    const d = this.daten();
    if (!d) return [];
    const list: StatusAktion[] = [];
    switch (d.status) {
      case 'ERFASST':
        if (this.darfAendern()) list.push(A_PRUEFEN, A_ABLEHNEN);
        break;
      case 'GEPRUEFT':
        if (this.darfFreigeben()) list.push(A_FREIGEBEN);
        if (this.darfAendern()) list.push(A_ABLEHNEN);
        break;
      case 'FREIGEGEBEN':
        if (this.darfFreigeben()) list.push(A_BUCHEN);
        if (this.darfAendern()) list.push(A_ZURUECK);
        break;
    }
    return list;
  });

  // --- Statusaktion (Bestätigung) -----------------------------------------
  protected readonly meldung = signal<Meldung | null>(null);
  protected readonly aktion = signal<StatusAktion | null>(null);
  protected readonly aktionLaedt = signal(false);

  /** Freigabe-Text bekommt bei unkontierten Positionen eine Warnung angehängt. */
  protected readonly aktionText = computed(() => {
    const a = this.aktion();
    if (!a) return '';
    if (a.to_status === 'FREIGEGEBEN') {
      const u = this.unkontierte();
      if (u.length) {
        const wort = u.length === 1 ? 'Position' : 'Positionen';
        const ist = u.length === 1 ? 'ist' : 'sind';
        return `${a.text} Achtung: ${wort} ${u.join(', ')} ${ist} noch nicht kontiert — die Freigabe verlangt für jede Position ein Buchungskonto und wird sonst abgewiesen.`;
      }
    }
    return a.text;
  });

  // --- Editor --------------------------------------------------------------
  protected readonly editOffen = signal(false);
  protected readonly editLaedt = signal(false);
  protected readonly editMeldung = signal<string | null>(null);

  private readonly ledgers = signal<LedgerAccount[]>([]);
  private readonly costCenters = signal<CostCenter[]>([]);
  protected readonly ledgerOptionen = computed<FeldOption[]>(() =>
    this.ledgers().map((a) => ({ wert: a.id, label: `${a.account_number} — ${a.label}` })),
  );
  protected readonly costCenterOptionen = computed<FeldOption[]>(() =>
    this.costCenters().map((c) => ({ wert: c.id, label: `${c.code} — ${c.label}` })),
  );

  protected readonly lieferantSuche: RefSuche = (q) =>
    this.partySvc.list({ page: 1, page_size: 20, q }).pipe(
      map((p) => p.items.map((o) => ({ id: o.id, label: o.display_name }))),
    );

  protected readonly editForm = this.fb.group({
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

  private readonly dateFmt = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.tab.set('positionen');
      if (!id) {
        this.state.set({ kind: 'error' });
        return;
      }
      this.load(id);
    });
    this.kontierungLaden();
  }

  retry(): void {
    const id = this.route.snapshot.paramMap.get('id');
    if (id) this.load(id);
  }

  private load(id: string): void {
    const rid = ++this.reqId;
    this.state.set({ kind: 'loading' });
    this.svc.getReceipt(id).subscribe({
      next: (data) => {
        if (rid === this.reqId) this.state.set({ kind: 'ready', data });
      },
      error: (err) => {
        if (rid === this.reqId) this.state.set(fehlerState(err));
      },
    });
  }

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

  private aktualisieren(d: ReceiptDetail): void {
    // Antwort ist der frische Beleg — direkt übernehmen. reqId erhöhen, damit ein
    // noch laufender load() das nicht überschreibt.
    ++this.reqId;
    this.state.set({ kind: 'ready', data: d });
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  // ---- Statusaktionen -----------------------------------------------------
  aktionStarten(a: StatusAktion): void {
    this.meldung.set(null);
    this.aktion.set(a);
  }

  aktionAbbrechen(): void {
    if (!this.aktionLaedt()) this.aktion.set(null);
  }

  aktionBestaetigen(grund: string | null): void {
    const d = this.daten();
    const a = this.aktion();
    if (!d || !a || this.aktionLaedt()) return;
    this.aktionLaedt.set(true);
    this.svc.advanceStatus(d.id, { to_status: a.to_status, reason: grund }).subscribe({
      next: (res) => {
        this.aktionLaedt.set(false);
        this.aktion.set(null);
        this.aktualisieren(res);
        this.meldung.set({
          art: 'erfolg',
          text: `Status geändert: ${receiptStatusLabel(res.status)}.`,
        });
      },
      error: (err) => {
        this.aktionLaedt.set(false);
        this.aktion.set(null);
        this.meldung.set({ art: 'fehler', text: this.fehlerText(err) });
      },
    });
  }

  // ---- Editor -------------------------------------------------------------
  get lines(): FormArray<FormGroup> {
    return this.editForm.controls.lines;
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

  bearbeitenOeffnen(): void {
    const d = this.daten();
    if (!d || !this.bearbeitbar()) return;
    this.editForm.reset({
      supplier_party_id: d.supplier_party_id,
      receipt_date: d.receipt_date,
      received_date: d.received_date ?? '',
      due_date: d.due_date ?? '',
      supplier_invoice_number: d.supplier_invoice_number ?? '',
      currency: d.currency ?? 'EUR',
      notes: d.notes ?? '',
    });
    this.lines.clear();
    for (const l of d.lines) {
      const g = this.zeileGruppe();
      g.reset({
        description: l.description,
        quantity: apiZuDeEingabe(l.quantity),
        unit: l.unit ?? '',
        unit_price: apiZuDeEingabe(l.unit_price, 2),
        tax_code: l.tax_code,
        ledger_account_id: l.ledger_account_id ?? '',
        cost_center_id: l.cost_center_id ?? '',
      });
      this.lines.push(g);
    }
    if (this.lines.length === 0) this.zeileHinzufuegen();
    this.editMeldung.set(null);
    this.editOffen.set(true);
  }

  bearbeitenSchliessen(): void {
    if (!this.editLaedt()) this.editOffen.set(false);
  }

  bearbeitenAbsenden(): void {
    const d = this.daten();
    if (!d || this.editLaedt()) return;
    serverFehlerZuruecksetzen(this.editForm);
    this.editMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.editForm);
    if (this.editForm.invalid) return;
    if (this.lines.length === 0) {
      this.editMeldung.set('Bitte mindestens eine Position erfassen.');
      return;
    }

    const v = this.editForm.getRawValue();
    const lines: ReceiptLineInput[] = this.lines.controls.map((g) => ({
      description: String(g.controls['description'].value ?? '').trim(),
      quantity: deZuApiDezimal(g.controls['quantity'].value),
      unit_price: deZuApiDezimal(g.controls['unit_price'].value),
      tax_code: String(g.controls['tax_code'].value),
      unit: String(g.controls['unit'].value ?? '').trim() || null,
      ledger_account_id: g.controls['ledger_account_id'].value || null,
      cost_center_id: g.controls['cost_center_id'].value || null,
    }));

    const payload: ReceiptUpdate = {
      supplier_party_id: v.supplier_party_id,
      receipt_date: v.receipt_date,
      received_date: v.received_date || null,
      due_date: v.due_date || null,
      supplier_invoice_number: v.supplier_invoice_number.trim() || null,
      currency: v.currency.trim() || 'EUR',
      notes: v.notes.trim() || null,
      lines,
    };

    this.editLaedt.set(true);
    this.svc.updateReceipt(d.id, payload).subscribe({
      next: (res) => {
        this.editLaedt.set(false);
        this.editOffen.set(false);
        this.aktualisieren(res);
        this.meldung.set({
          art: 'erfolg',
          text: `Beleg gespeichert (brutto ${this.euro(res.gross_total)}, vom Server berechnet).`,
        });
      },
      error: (err) => {
        this.editLaedt.set(false);
        this.editMeldung.set(apiFehlerZuweisen(err, this.editForm).formular);
      },
    });
  }

  private fehlerText(err: unknown): string {
    if (istVerboten(err)) return fehlerDetail(err) ?? 'Keine Berechtigung für diese Aktion.';
    return fehlerDetail(err) ?? 'Die Aktion ist fehlgeschlagen. Bitte erneut versuchen.';
  }

  // ---- Darstellungshelfer -------------------------------------------------
  euro(v: string | null): string {
    return euro(v);
  }
  menge(qty: string | null, unit: string | null): string {
    return menge(qty, unit);
  }
  statusLabel(s: string | null): string {
    return receiptStatusLabel(s);
  }
  statusClass(s: string): string {
    return receiptStatusClass(s);
  }
  taxLabel(code: string, rate: string): string {
    const pct = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 2 }).format(Number(rate));
    return `${code} · ${pct} %`;
  }
  zeitpunkt(iso: string): string {
    return this.dateFmt.format(new Date(iso));
  }
}
