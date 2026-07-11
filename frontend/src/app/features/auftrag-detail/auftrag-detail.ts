import { Component, computed, effect, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { map } from 'rxjs';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { AuftragService } from '../../core/auftrag.service';
import { EinsatzService } from '../../core/einsatz.service';
import { PartyService } from '../../core/party.service';
import { AuthService } from '../../core/auth.service';
import {
  Kundenhistorie,
  OrderPriority,
  WorkOrderDetail,
  WorkOrderPartyCreate,
  WorkOrderStatus,
  workOrderStatusClass,
  workOrderStatusLabel,
} from '../../core/auftrag.model';
import { ServiceJob, ServiceJobStatus, serviceJobStatusLabel } from '../../core/einsatz.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { Dateien } from '../../shared/dateien/dateien';
import { ZielFilter } from '../../core/datei.model';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';
import { Dialog } from '../../shared/dialog/dialog';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { ReferenzWahl, RefSuche } from '../../shared/formular/referenz-wahl';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import { deZuApiDezimal, dezimalValidator } from '../../shared/formular/dezimal';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: WorkOrderDetail }
  | VerbotenState
  | { kind: 'error' };

type Meldung = { art: 'erfolg' | 'fehler'; text: string };
type DialogArt = 'beteiligter' | 'status' | 'nachweis' | 'verantwortung';

// Kandidaten für den allgemeinen Statuswechsel (FREIGEGEBEN läuft über die
// gesonderte Freigabe mit Bestätigung und eigenem Recht).
const STATUS_KANDIDATEN: WorkOrderStatus[] = [
  'FREIGABE_AUSSTEHEND',
  'IN_PLANUNG',
  'IN_AUSFUEHRUNG',
  'TECHNISCH_ABGESCHLOSSEN',
  'KAUFMAENNISCH_GEPRUEFT',
  'ABGERECHNET',
  'STORNIERT',
];

@Component({
  selector: 'app-auftrag-detail',
  imports: [
    Mappe,
    RouterLink,
    KeinZugriff,
    Dateien,
    ReactiveFormsModule,
    Dialog,
    Bestaetigung,
    Feld,
    ReferenzWahl,
  ],
  templateUrl: './auftrag-detail.html',
  styleUrl: './auftrag-detail.scss',
})
export class AuftragDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(AuftragService);
  private readonly einsatzSvc = inject(EinsatzService);
  private readonly partySvc = inject(PartyService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  protected readonly darfAendern = computed(() => this.auth.darf('workflow', 'AENDERN'));
  protected readonly darfFreigeben = computed(() => this.auth.darf('workflow', 'FREIGEBEN'));

  protected readonly tab = signal('uebersicht');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  protected readonly tabs: MappeTab[] = [
    { id: 'uebersicht', label: 'Übersicht' },
    { id: 'beteiligte', label: 'Beteiligte' },
    { id: 'termine', label: 'Termine' },
    { id: 'verlauf', label: 'Verlauf' },
    { id: 'dateien', label: 'Dateien' },
  ];

  // --- Termine-Tab (Einsätze des Auftrags + Kundenhistorie) -----------------
  // Lazy: erst beim Öffnen des Reiters laden; je Auftrag einmal.
  protected readonly termineLaden = signal(false);
  protected readonly termineFehler = signal(false);
  protected readonly termine = signal<ServiceJob[]>([]);
  protected readonly historie = signal<Kundenhistorie | null>(null);
  private termineFuer: string | null = null;
  private termineReqId = 0;

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  /** Stabile Zielreferenz fuer den Dateien-Tab (nur bei Auftragswechsel neu). */
  protected readonly dateienZiel = computed<ZielFilter>(() => ({
    work_order_id: this.daten()?.id ?? '',
  }));

  // --- Schreibaktionen -----------------------------------------------------
  protected readonly meldung = signal<Meldung | null>(null);
  protected readonly dialogOffen = signal<DialogArt | null>(null);
  protected readonly dialogLaedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);
  protected readonly freigabeOffen = signal(false);
  protected readonly freigabeLaedt = signal(false);

  protected readonly rollen: FeldOption[] = [
    { wert: 'PRINCIPAL', label: 'Auftraggeber' },
    { wert: 'REPRESENTATIVE', label: 'Vertretung' },
    { wert: 'SERVICE_RECIPIENT', label: 'Leistungsempfänger' },
    { wert: 'OCCUPANT', label: 'Nutzer' },
    { wert: 'COST_BEARER', label: 'Kostenträger' },
    { wert: 'INVOICE_DEBTOR', label: 'Rechnungsschuldner' },
    { wert: 'INVOICE_RECIPIENT', label: 'Rechnungsempfänger' },
    { wert: 'ON_SITE_CONTACT', label: 'Ansprechpartner vor Ort' },
  ];
  protected readonly scopes: FeldOption[] = [
    { wert: 'UNKNOWN', label: 'Ungeklärt' },
    { wert: 'COMMON_PROPERTY', label: 'Gemeinschaftseigentum' },
    { wert: 'PRIVATE_UNIT', label: 'Sondereigentum' },
    { wert: 'MIXED', label: 'Gemischt' },
  ];

  /** Statusoptionen (ohne aktuellen Status und ohne FREIGEGEBEN). */
  protected readonly statusOptionen = computed<FeldOption[]>(() => {
    const cur = this.daten()?.status;
    return STATUS_KANDIDATEN.filter((s) => s !== cur).map((s) => ({
      wert: s,
      label: workOrderStatusLabel(s),
    }));
  });

  /** Freigabe ist nur vor der Freigabe sinnvoll. */
  protected readonly freigabeMoeglich = computed(() => {
    const s = this.daten()?.status;
    return s === 'ENTWURF' || s === 'FREIGABE_AUSSTEHEND';
  });

  protected readonly partySuche: RefSuche = (q) =>
    this.partySvc.list({ page: 1, page_size: 20, q }).pipe(
      map((p) => p.items.map((x) => ({ id: x.id, label: x.display_name }))),
    );

  protected readonly beteiligterForm = this.fb.group({
    party_id: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    role: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    is_primary: this.fb.control(false, { nonNullable: true }),
    allocation_percent: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
  });
  protected readonly statusForm = this.fb.group({
    to_status: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    reason: this.fb.control('', { nonNullable: true }),
  });
  protected readonly nachweisForm = this.fb.group({
    reference: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
  });
  protected readonly verantwortungForm = this.fb.group({
    scope: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
  });

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.tab.set('uebersicht');
      this.termineFuer = null;
      this.termine.set([]);
      this.historie.set(null);
      if (!id) {
        this.state.set({ kind: 'error' });
        return;
      }
      this.load(id);
    });

    // Termine-Reiter erst beim Öffnen laden (je Auftrag einmal).
    effect(() => {
      const d = this.daten();
      if (this.tab() === 'termine' && d && this.termineFuer !== d.id) {
        this.termineFuer = d.id;
        this.ladeTermine(d.id);
      }
    });
  }

  private ladeTermine(id: string): void {
    // Generations-Guard: eine verspätete Antwort eines zuvor geöffneten Auftrags
    // darf die aktuellen Signale nicht überschreiben.
    const rid = ++this.termineReqId;
    this.termine.set([]);
    this.historie.set(null);
    this.termineLaden.set(true);
    this.termineFehler.set(false);
    this.einsatzSvc.list({ page: 1, page_size: 100, work_order_id: id }).subscribe({
      next: (p) => {
        if (rid !== this.termineReqId) return;
        this.termine.set(p.items);
        this.termineLaden.set(false);
      },
      error: () => {
        if (rid !== this.termineReqId) return;
        this.termineLaden.set(false);
        this.termineFehler.set(true);
      },
    });
    this.svc.kundenhistorie(id).subscribe({
      next: (h) => {
        if (rid === this.termineReqId) this.historie.set(h);
      },
      error: () => {},
    });
  }

  retry(): void {
    const id = this.route.snapshot.paramMap.get('id');
    if (id) this.load(id);
  }

  private load(id: string): void {
    const rid = ++this.reqId;
    this.state.set({ kind: 'loading' });
    this.svc.get(id).subscribe({
      next: (data) => {
        if (rid === this.reqId) this.state.set({ kind: 'ready', data });
      },
      error: (err) => {
        if (rid === this.reqId) this.state.set(fehlerState(err));
      },
    });
  }

  // ---- Schreibaktionen ----------------------------------------------------
  dialogOeffnen(art: DialogArt): void {
    this.formularMeldung.set(null);
    switch (art) {
      case 'beteiligter':
        this.beteiligterForm.reset({
          party_id: '',
          role: '',
          is_primary: false,
          allocation_percent: '',
        });
        break;
      case 'status':
        this.statusForm.reset({ to_status: '', reason: '' });
        break;
      case 'nachweis':
        this.nachweisForm.reset({ reference: this.daten()?.order_evidence_reference ?? '' });
        break;
      case 'verantwortung':
        this.verantwortungForm.reset({ scope: this.daten()?.responsibility_scope ?? '' });
        break;
    }
    this.dialogOffen.set(art);
  }

  dialogSchliessen(): void {
    if (this.dialogLaedt()) return;
    this.dialogOffen.set(null);
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  private aktualisieren(d: WorkOrderDetail): void {
    // Antwort ist der frische Auftrag — direkt übernehmen (kein zweiter GET).
    // reqId erhöhen, damit ein noch laufender load() das nicht überschreibt.
    ++this.reqId;
    this.state.set({ kind: 'ready', data: d });
  }

  private nichtBereit(form: Parameters<typeof serverFehlerZuruecksetzen>[0]): boolean {
    if (this.dialogLaedt()) return true;
    serverFehlerZuruecksetzen(form);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(form);
    return form.invalid;
  }

  beteiligterAbsenden(): void {
    const d = this.daten();
    if (!d || this.nichtBereit(this.beteiligterForm)) return;
    const v = this.beteiligterForm.getRawValue();
    const alloc = deZuApiDezimal(v.allocation_percent);
    const payload: WorkOrderPartyCreate = {
      party_id: v.party_id,
      role: v.role,
      is_primary: v.is_primary,
      allocation_percent: alloc || null,
    };
    this.dialogLaedt.set(true);
    this.svc.addParty(d.id, payload).subscribe({
      next: (res) => {
        this.dialogLaedt.set(false);
        this.dialogOffen.set(null);
        this.aktualisieren(res);
        this.meldung.set({ art: 'erfolg', text: 'Beteiligter hinzugefügt.' });
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.beteiligterForm).formular);
      },
    });
  }

  statusAbsenden(): void {
    const d = this.daten();
    if (!d || this.nichtBereit(this.statusForm)) return;
    const v = this.statusForm.getRawValue();
    this.dialogLaedt.set(true);
    this.svc
      .advanceStatus(d.id, {
        to_status: v.to_status as WorkOrderStatus,
        reason: v.reason.trim() || null,
      })
      .subscribe({
        next: (res) => {
          this.dialogLaedt.set(false);
          this.dialogOffen.set(null);
          this.aktualisieren(res);
          this.meldung.set({ art: 'erfolg', text: 'Status geändert.' });
        },
        error: (err) => {
          this.dialogLaedt.set(false);
          this.formularMeldung.set(apiFehlerZuweisen(err, this.statusForm).formular);
        },
      });
  }

  nachweisAbsenden(): void {
    const d = this.daten();
    if (!d || this.nichtBereit(this.nachweisForm)) return;
    const v = this.nachweisForm.getRawValue();
    this.dialogLaedt.set(true);
    this.svc.setEvidence(d.id, { reference: v.reference.trim() }).subscribe({
      next: (res) => {
        this.dialogLaedt.set(false);
        this.dialogOffen.set(null);
        this.aktualisieren(res);
        this.meldung.set({ art: 'erfolg', text: 'Beauftragungsnachweis gesetzt.' });
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.nachweisForm).formular);
      },
    });
  }

  verantwortungAbsenden(): void {
    const d = this.daten();
    if (!d || this.nichtBereit(this.verantwortungForm)) return;
    const v = this.verantwortungForm.getRawValue();
    this.dialogLaedt.set(true);
    this.svc.confirmResponsibility(d.id, { scope: v.scope }).subscribe({
      next: (res) => {
        this.dialogLaedt.set(false);
        this.dialogOffen.set(null);
        this.aktualisieren(res);
        this.meldung.set({ art: 'erfolg', text: 'Verantwortungsbereich bestätigt.' });
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.verantwortungForm).formular);
      },
    });
  }

  // --- Freigabe (Tor mit Folgen → Bestätigung, eigenes Recht FREIGEBEN) -----
  freigabeFragen(): void {
    this.freigabeOffen.set(true);
  }
  freigabeAbbrechen(): void {
    if (!this.freigabeLaedt()) this.freigabeOffen.set(false);
  }
  freigabeBestaetigen(grund: string | null): void {
    const d = this.daten();
    if (!d) return;
    this.freigabeLaedt.set(true);
    this.svc.advanceStatus(d.id, { to_status: 'FREIGEGEBEN', reason: grund }).subscribe({
      next: (res) => {
        this.freigabeLaedt.set(false);
        this.freigabeOffen.set(false);
        this.aktualisieren(res);
        this.meldung.set({ art: 'erfolg', text: 'Auftrag freigegeben.' });
      },
      error: (err) => {
        this.freigabeLaedt.set(false);
        this.freigabeOffen.set(false);
        const text = apiFehlerZuweisen(err, this.statusForm).formular;
        this.meldung.set({ art: 'fehler', text: text ?? 'Freigabe fehlgeschlagen.' });
      },
    });
  }

  // ---- Darstellungshelfer -------------------------------------------------
  private readonly terminFmt = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
  });
  /** Geplanter Termin-Zeitpunkt eines Einsatzes (oder „ohne Termin"). */
  terminZeit(iso: string | null): string {
    return iso ? this.terminFmt.format(new Date(iso)) : 'ohne Termin';
  }
  jobStatusLabel(s: ServiceJobStatus): string {
    return serviceJobStatusLabel(s);
  }

  statusLabel(s: WorkOrderStatus): string {
    return workOrderStatusLabel(s);
  }
  statusClass(s: WorkOrderStatus): string {
    return workOrderStatusClass(s);
  }
  // Auch für Verlaufseinträge (String-Status).
  statusLabelStr(s: string | null): string {
    if (s === null) return 'Anlage';
    return workOrderStatusLabel(s as WorkOrderStatus);
  }

  priorityLabel(p: OrderPriority): string {
    const map: Record<OrderPriority, string> = {
      NORMAL: 'Normal',
      DRINGEND: 'Dringend',
      NOTFALL: 'Notfall',
    };
    return map[p] ?? p;
  }
  priorityClass(p: OrderPriority): string {
    return p === 'NORMAL' ? '' : 'stamp--warn';
  }

  scopeLabel(s: string): string {
    const map: Record<string, string> = {
      UNKNOWN: 'Ungeklärt',
      COMMON_PROPERTY: 'Gemeinschaftseigentum',
      PRIVATE_UNIT: 'Sondereigentum',
      MIXED: 'Gemischt',
    };
    return map[s] ?? s;
  }

  roleLabel(r: string): string {
    const map: Record<string, string> = {
      PRINCIPAL: 'Auftraggeber',
      REPRESENTATIVE: 'Vertretung',
      SERVICE_RECIPIENT: 'Leistungsempfänger',
      OCCUPANT: 'Nutzer',
      COST_BEARER: 'Kostenträger',
      INVOICE_DEBTOR: 'Rechnungsschuldner',
      INVOICE_RECIPIENT: 'Rechnungsempfänger',
      REPORTER: 'Melder',
      ON_SITE_CONTACT: 'Ansprechpartner vor Ort',
    };
    return map[r] ?? r;
  }
}
