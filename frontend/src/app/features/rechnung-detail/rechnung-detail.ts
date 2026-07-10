import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { map } from 'rxjs';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { BelegService } from '../../core/beleg.service';
import { PartyService } from '../../core/party.service';
import { AuthService } from '../../core/auth.service';
import {
  InvoiceDetail,
  InvoicePartyCreate,
  InvoiceStatus,
  InvoiceType,
  LineType,
} from '../../core/beleg.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { Dateien } from '../../shared/dateien/dateien';
import { ZielFilter } from '../../core/datei.model';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { ReferenzWahl, RefSuche } from '../../shared/formular/referenz-wahl';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';
import { VerbotenState, fehlerDetail, fehlerState, istVerboten } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: InvoiceDetail }
  | VerbotenState
  | { kind: 'error' };

type Meldung = { art: 'erfolg' | 'fehler'; text: string };

@Component({
  selector: 'app-rechnung-detail',
  imports: [Mappe, RouterLink, KeinZugriff, Bestaetigung, Dateien, ReactiveFormsModule, Dialog, Feld, ReferenzWahl],
  templateUrl: './rechnung-detail.html',
  styleUrl: './rechnung-detail.scss',
})
export class RechnungDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(BelegService);
  private readonly partySvc = inject(PartyService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  protected readonly tab = signal('positionen');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  // --- Veröffentlichen (unumkehrbar) --------------------------------------
  protected readonly darfFreigeben = computed(() => this.auth.darf('invoicing', 'FREIGEBEN'));
  protected readonly meldung = signal<Meldung | null>(null);
  protected readonly publishOffen = signal(false);
  protected readonly publishLaedt = signal(false);

  /** Nur Entwürfe lassen sich veröffentlichen (Server setzt die Tore durch). */
  protected readonly kannVeroeffentlichen = computed(() => this.daten()?.status === 'ENTWURF');

  // --- Beteiligten hinzufügen (nur im Entwurf) ----------------------------
  protected readonly darfAendern = computed(() => this.auth.darf('invoicing', 'AENDERN'));
  /** Beteiligte lassen sich nur am Entwurf ergänzen (Server erzwingt es). */
  protected readonly kannBeteiligen = computed(() => this.daten()?.status === 'ENTWURF');
  protected readonly beteiligtOffen = signal(false);
  protected readonly beteiligtLaedt = signal(false);
  protected readonly beteiligtMeldung = signal<string | null>(null);
  protected readonly rollen: FeldOption[] = [
    { wert: 'INVOICE_DEBTOR', label: 'Rechnungsschuldner' },
    { wert: 'INVOICE_RECIPIENT', label: 'Rechnungsempfänger' },
    { wert: 'REPRESENTATIVE', label: 'Vertretung' },
    { wert: 'COST_BEARER', label: 'Kostenträger' },
  ];
  protected readonly beteiligtForm = this.fb.group({
    party_id: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    role: this.fb.control('INVOICE_DEBTOR', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    is_primary: this.fb.control(false, { nonNullable: true }),
  });

  /** Kontaktsuche (Personen und Organisationen) für den Beteiligten. */
  protected readonly kontaktSuche: RefSuche = (q) =>
    this.partySvc.list({ page: 1, page_size: 20, q }).pipe(
      map((p) => p.items.map((o) => ({ id: o.id, label: o.display_name }))),
    );

  protected readonly tabs: MappeTab[] = [
    { id: 'positionen', label: 'Positionen' },
    { id: 'beteiligte', label: 'Beteiligte' },
    { id: 'uebersicht', label: 'Übersicht' },
    { id: 'dateien', label: 'Dateien' },
  ];

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  /** Stabile Zielreferenz fuer den Dateien-Tab (nur bei Rechnungswechsel neu). */
  protected readonly dateienZiel = computed<ZielFilter>(() => ({
    invoice_id: this.daten()?.id ?? '',
  }));

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
  }

  retry(): void {
    const id = this.route.snapshot.paramMap.get('id');
    if (id) this.load(id);
  }

  private load(id: string): void {
    const rid = ++this.reqId;
    this.state.set({ kind: 'loading' });
    this.svc.getInvoice(id).subscribe({
      next: (data) => {
        if (rid === this.reqId) this.state.set({ kind: 'ready', data });
      },
      error: (err) => {
        if (rid === this.reqId) this.state.set(fehlerState(err));
      },
    });
  }

  // ---- Veröffentlichen ----------------------------------------------------
  publishFragen(): void {
    this.meldung.set(null);
    this.publishOffen.set(true);
  }

  publishAbbrechen(): void {
    if (!this.publishLaedt()) this.publishOffen.set(false);
  }

  publishBestaetigen(): void {
    const d = this.daten();
    if (!d || this.publishLaedt()) return;
    this.publishLaedt.set(true);
    this.svc.publishInvoice(d.id).subscribe({
      next: (aktualisiert) => {
        this.publishLaedt.set(false);
        this.publishOffen.set(false);
        this.state.set({ kind: 'ready', data: aktualisiert });
        this.meldung.set({
          art: 'erfolg',
          text: `Rechnung veröffentlicht. Belegnummer ${aktualisiert.invoice_number ?? '—'} wurde vergeben.`,
        });
      },
      error: (err) => {
        this.publishLaedt.set(false);
        this.publishOffen.set(false);
        // Die DB-Tore liefern präzise 422-Meldungen — wörtlich zeigen.
        this.meldung.set({ art: 'fehler', text: this.fehlerText(err) });
      },
    });
  }

  // ---- Beteiligten hinzufügen ---------------------------------------------
  beteiligtOeffnen(): void {
    this.beteiligtForm.reset({ party_id: '', role: 'INVOICE_DEBTOR', is_primary: false });
    this.beteiligtMeldung.set(null);
    this.meldung.set(null);
    this.beteiligtOffen.set(true);
  }

  beteiligtSchliessen(): void {
    if (!this.beteiligtLaedt()) this.beteiligtOffen.set(false);
  }

  beteiligtAbsenden(): void {
    const d = this.daten();
    if (!d || this.beteiligtLaedt()) return;
    serverFehlerZuruecksetzen(this.beteiligtForm);
    this.beteiligtMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.beteiligtForm);
    if (this.beteiligtForm.invalid) return;

    const v = this.beteiligtForm.getRawValue();
    const payload: InvoicePartyCreate = {
      party_id: v.party_id,
      role: v.role,
      is_primary: v.is_primary,
    };
    this.beteiligtLaedt.set(true);
    this.svc.addInvoiceParty(d.id, payload).subscribe({
      next: (aktualisiert) => {
        this.beteiligtLaedt.set(false);
        this.beteiligtOffen.set(false);
        this.state.set({ kind: 'ready', data: aktualisiert });
        this.meldung.set({ art: 'erfolg', text: 'Beteiligter wurde ergänzt.' });
      },
      error: (err) => {
        this.beteiligtLaedt.set(false);
        this.beteiligtMeldung.set(apiFehlerZuweisen(err, this.beteiligtForm).formular);
      },
    });
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  private fehlerText(err: unknown): string {
    if (istVerboten(err)) return fehlerDetail(err) ?? 'Keine Berechtigung für diese Aktion.';
    return fehlerDetail(err) ?? 'Die Aktion ist fehlgeschlagen. Bitte erneut versuchen.';
  }

  // ---- Darstellungshelfer -------------------------------------------------
  euro(amount: string | null): string {
    if (amount === null) return '—';
    return new Intl.NumberFormat('de-DE', {
      style: 'currency',
      currency: 'EUR',
    }).format(Number(amount));
  }

  menge(qty: string | null, unit: string | null): string {
    if (qty === null) return '';
    const n = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 3 }).format(
      Number(qty),
    );
    return unit ? `${n} ${unit}` : n;
  }

  typeLabel(t: InvoiceType): string {
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

  statusLabel(s: InvoiceStatus): string {
    return s === 'VEROEFFENTLICHT' ? 'Veröffentlicht' : 'Entwurf';
  }
  statusClass(s: InvoiceStatus): string {
    return s === 'VEROEFFENTLICHT' ? 'stamp--positive' : '';
  }

  lineTypeLabel(t: LineType): string {
    const map: Record<LineType, string> = {
      MATERIAL: 'Material',
      ARBEITSZEIT: 'Arbeitszeit',
      PAUSCHALE: 'Pauschale',
      FREMDLEISTUNG: 'Fremdleistung',
      FAHRT: 'Fahrt',
      ZUSCHLAG: 'Zuschlag',
      TEXT: 'Text',
      ZWISCHENSUMME: 'Zwischensumme',
    };
    return map[t] ?? t;
  }
  isText(t: LineType): boolean {
    return t === 'TEXT' || t === 'ZWISCHENSUMME';
  }

  roleLabel(r: string): string {
    const map: Record<string, string> = {
      INVOICE_DEBTOR: 'Rechnungsschuldner',
      INVOICE_RECIPIENT: 'Rechnungsempfänger',
      REPRESENTATIVE: 'Vertretung',
      COST_BEARER: 'Kostenträger',
    };
    return map[r] ?? r;
  }
  // Kurzform des Inhalts-Hashes (GoBD-Beleg-Fingerabdruck) für die Anzeige.
  hashKurz(h: string | null): string {
    return h ? h.slice(0, 12) + '…' : '—';
  }

  /** URL der on-the-fly gerenderten PDF-Ausfertigung (nur veröffentlicht). */
  pdfUrl(id: string): string {
    return `/api/invoicing/invoices/${id}/pdf`;
  }
}
