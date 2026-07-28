import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Subject, debounceTime, distinctUntilChanged, map } from 'rxjs';
import { WartungService } from '../../core/wartung.service';
import { AnlageService } from '../../core/anlage.service';
import { Anlage, artLabel } from '../../core/anlage.model';
import { PropertyService } from '../../core/property.service';
import { PartyService } from '../../core/party.service';
import { ProjektService } from '../../core/projekt.service';
import { AuthService } from '../../core/auth.service';
import {
  ContractCreate,
  ContractPage,
  ContractStatus,
  DueAction,
  IntervalKind,
  contractStatusClass,
  contractStatusLabel,
  dueActionLabel,
  intervalKindLabel,
} from '../../core/wartung.model';
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
import { WartungNav } from '../wartung-nav/wartung-nav';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: ContractPage }
  | VerbotenState
  | { kind: 'error' };

type Segment = { value: ContractStatus | null; label: string };
type Meldung = { art: 'erfolg' | 'fehler'; text: string };

@Component({
  selector: 'app-wartung',
  imports: [
    RouterLink,
    KeinZugriff,
    ReactiveFormsModule,
    Dialog,
    Feld,
    ReferenzWahl,
    WartungNav,
  ],
  templateUrl: './wartung.html',
  styleUrl: './wartung.scss',
})
export class Wartung {
  private readonly svc = inject(WartungService);
  private readonly propertySvc = inject(PropertyService);
  private readonly anlageSvc = inject(AnlageService);
  private readonly partySvc = inject(PartyService);
  private readonly projektSvc = inject(ProjektService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  protected readonly darfAnlegen = computed(() => this.auth.darf('maintenance', 'ANLEGEN'));

  protected readonly intervalle: FeldOption[] = [
    { wert: 'JAEHRLICH', label: 'Jährlich' },
    { wert: 'MONATLICH', label: 'Monatlich' },
    { wert: 'WOECHENTLICH', label: 'Wöchentlich' },
    { wert: 'TAGE', label: 'Alle N Tage' },
    { wert: 'FESTES_DATUM', label: 'Festes Datum' },
  ];
  protected readonly aktionen: FeldOption[] = [
    { wert: 'PROJEKT', label: 'Projekt anlegen' },
    { wert: 'AUFTRAG', label: 'Auftrag anlegen' },
    { wert: 'AUFGABE', label: 'Aufgabe anlegen' },
    { wert: 'BENACHRICHTIGUNG', label: 'Benachrichtigung' },
  ];

  protected readonly meldung = signal<Meldung | null>(null);
  /** Anlagen der gewählten Liegenschaft — Auswahl im Anlege-Dialog (0135). */
  protected readonly anlagen = signal<Anlage[]>([]);
  /**
   * Spiegel des Liegenschaftsfelds als Signal. Das Template darf `control.value`
   * nicht direkt lesen: Die App läuft **zonenlos**, ein FormControl ist kein
   * Signal, und der Block würde nur zufällig aktualisiert.
   */
  protected readonly gewaehlteLiegenschaft = signal('');
  protected readonly anlagenLaedt = signal(false);
  protected readonly gewaehlteAnlagen = signal<ReadonlySet<string>>(new Set());
  private anlagenReq = 0;
  protected readonly neuOffen = signal(false);
  protected readonly neuLaedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);
  /** Steuert die bedingten Felder (nur TAGE braucht interval_days, nur FESTES_DATUM fixed_date). */
  protected readonly intervalKind = signal<string>('JAEHRLICH');

  protected readonly neuForm = this.fb.group({
    property_id: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    name: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(200)],
    }),
    start_date: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    interval_kind: this.fb.control('JAEHRLICH', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    interval_days: this.fb.control('', { nonNullable: true }),
    fixed_date: this.fb.control('', { nonNullable: true }),
    due_action: this.fb.control('AUFTRAG', { nonNullable: true, validators: [Validators.required] }),
    party_id: this.fb.control('', { nonNullable: true }),
    project_id: this.fb.control('', { nonNullable: true }),
    lead_time_days: this.fb.control('', { nonNullable: true }),
    notes: this.fb.control('', { nonNullable: true }),
  });

  protected readonly liegenschaftSuche: RefSuche = (q) =>
    this.propertySvc.list({ page: 1, page_size: 20, q }).pipe(
      map((p) => p.items.map((o) => ({ id: o.id, label: o.name, sub: `${o.property_number} · ${o.city}` }))),
    );
  protected readonly partySuche: RefSuche = (q) =>
    this.partySvc.list({ page: 1, page_size: 20, q }).pipe(
      map((p) => p.items.map((x) => ({ id: x.id, label: x.display_name }))),
    );
  protected readonly projektSuche: RefSuche = (q) =>
    this.projektSvc.list({ page: 1, page_size: 20, q }).pipe(
      map((p) => p.items.map((x) => ({ id: x.id, label: x.name, sub: x.project_number }))),
    );

  protected readonly pageSize = 20;
  protected readonly segments: Segment[] = [
    { value: null, label: 'Alle' },
    { value: 'AKTIV', label: 'Aktiv' },
    { value: 'INAKTIV', label: 'Inaktiv' },
    { value: 'ARCHIVIERT', label: 'Archiviert' },
  ];

  protected readonly query = signal('');
  protected readonly status = signal<ContractStatus | null>(null);
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
    if (s.kind === 'loading') return 'Wartungsverträge werden geladen.';
    if (s.kind === 'forbidden') return 'Keine Berechtigung für die Wartungsverträge.';
    if (s.kind === 'error') return 'Wartungsverträge konnten nicht geladen werden.';
    const t = s.data.total;
    if (t === 0) return 'Keine Wartungsverträge gefunden.';
    return `${t} ${t === 1 ? 'Vertrag' : 'Verträge'} gefunden, Seite ${s.data.page} von ${this.totalPages()}.`;
  });

  constructor() {
    this.searchInput$
      .pipe(debounceTime(300), distinctUntilChanged(), takeUntilDestroyed())
      .subscribe((v) => {
        this.query.set(v);
        this.page.set(1);
        this.fetch();
      });
    this.neuForm.controls.interval_kind.valueChanges
      .pipe(takeUntilDestroyed())
      .subscribe((k) => this.intervalKind.set(k));
    // Anlagenauswahl folgt der gewählten Liegenschaft. Bewusst ein
    // valueChanges-Abo und kein `computed`: Ein FormControl ist kein Signal —
    // ein computed darauf feuert nie (Fund aus dem Anruf-Slice).
    this.neuForm.controls.property_id.valueChanges
      .pipe(takeUntilDestroyed())
      .subscribe((id) => this.anlagenLaden(id));
    this.fetch();
  }

  onSearch(value: string): void {
    this.searchInput$.next(value);
  }

  selectSegment(value: ContractStatus | null): void {
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
      .list({
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

  // ---- Abgedeckte Anlagen (Migration 0135) --------------------------------
  /**
   * Anlagen der gewählten Liegenschaft nachladen. Fehlt das Recht `property`
   * oder scheitert der Aufruf, bleibt die Liste leer — der Vertrag lässt sich
   * dann trotzdem anlegen und gilt eben objektweit. Ein Ladefehler hier darf
   * das Anlegen nicht blockieren.
   */
  private anlagenLaden(propertyId: string | null | undefined): void {
    this.gewaehlteAnlagen.set(new Set());
    this.gewaehlteLiegenschaft.set(propertyId ?? '');
    if (!propertyId) {
      this.anlagen.set([]);
      return;
    }
    const rid = ++this.anlagenReq;
    this.anlagenLaedt.set(true);
    this.anlageSvc.list(propertyId).subscribe({
      next: (liste) => {
        if (rid !== this.anlagenReq) return;
        this.anlagen.set(liste);
        this.anlagenLaedt.set(false);
      },
      error: () => {
        if (rid !== this.anlagenReq) return;
        this.anlagen.set([]);
        this.anlagenLaedt.set(false);
      },
    });
  }

  protected anlageUmschalten(id: string): void {
    this.gewaehlteAnlagen.update((alt) => {
      const neu = new Set(alt);
      if (neu.has(id)) neu.delete(id);
      else neu.add(id);
      return neu;
    });
  }

  protected anlageGewaehlt(id: string): boolean {
    return this.gewaehlteAnlagen().has(id);
  }

  /** Standort einer Anlage in einer Zeile — für die Auswahlliste. */
  protected anlageOrt(a: Anlage): string {
    const teile = [a.building_label, a.unit_label, a.unit_storey, a.location_note];
    return teile.filter(Boolean).join(' · ');
  }

  protected readonly anlageArt = artLabel;

  // ---- Anlegen ------------------------------------------------------------
  neuOeffnen(): void {
    this.anlagen.set([]);
    this.gewaehlteAnlagen.set(new Set());
    this.gewaehlteLiegenschaft.set('');
    this.neuForm.reset({
      property_id: '',
      name: '',
      start_date: '',
      interval_kind: 'JAEHRLICH',
      interval_days: '',
      fixed_date: '',
      due_action: 'AUFTRAG',
      party_id: '',
      project_id: '',
      lead_time_days: '',
      notes: '',
    });
    this.intervalKind.set('JAEHRLICH');
    this.formularMeldung.set(null);
    this.neuOffen.set(true);
  }

  neuSchliessen(): void {
    if (this.neuLaedt()) return;
    this.neuOffen.set(false);
  }

  neuAbsenden(): void {
    if (this.neuLaedt()) return;
    serverFehlerZuruecksetzen(this.neuForm);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.neuForm);
    if (this.neuForm.invalid) return;

    const v = this.neuForm.getRawValue();
    const ganzzahl = (s: string): number | null => {
      const t = s.trim();
      if (!t) return null;
      const n = parseInt(t, 10);
      return Number.isNaN(n) ? null : n;
    };
    const payload: ContractCreate = {
      property_id: v.property_id,
      name: v.name.trim(),
      start_date: v.start_date,
      interval_kind: v.interval_kind as IntervalKind,
      due_action: v.due_action as DueAction,
      interval_days: v.interval_kind === 'TAGE' ? ganzzahl(v.interval_days) : null,
      fixed_date: v.interval_kind === 'FESTES_DATUM' ? v.fixed_date || null : null,
      party_id: v.party_id || null,
      project_id: v.project_id || null,
      lead_time_days: ganzzahl(v.lead_time_days),
      notes: v.notes.trim() || null,
      // Leer = gilt fürs ganze Objekt (bisheriges Verhalten, 0135).
      asset_ids: [...this.gewaehlteAnlagen()],
    };

    this.neuLaedt.set(true);
    this.svc.create(payload).subscribe({
      next: () => {
        this.neuLaedt.set(false);
        this.neuOffen.set(false);
        this.meldung.set({ art: 'erfolg', text: `Wartungsvertrag „${payload.name}“ angelegt.` });
        this.page.set(1);
        this.fetch();
      },
      error: (err) => {
        this.neuLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.neuForm).formular);
      },
    });
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  // ---- Darstellungshelfer -------------------------------------------------
  statusLabel(s: ContractStatus): string {
    return contractStatusLabel(s);
  }
  statusClass(s: ContractStatus): string {
    return contractStatusClass(s);
  }
  intervalLabel(k: IntervalKind, days: number | null): string {
    return intervalKindLabel(k, days);
  }
  actionLabel(a: DueAction): string {
    return dueActionLabel(a);
  }
  d(iso: string | null): string {
    if (!iso) return '—';
    return this.dateFmt.format(new Date(iso));
  }
}
