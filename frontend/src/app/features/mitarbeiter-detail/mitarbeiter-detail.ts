import { Component, computed, effect, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Observable } from 'rxjs';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { Dialog } from '../../shared/dialog/dialog';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { Attest } from '../../shared/attest/attest';
import { Feld } from '../../shared/formular/feld';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';
import { deZuApiDezimal, dezimalValidator } from '../../shared/formular/dezimal';
import { VerbotenState, fehlerDetail, fehlerState, istVerboten } from '../../shared/http-fehler';
import { MitarbeiterService } from '../../core/mitarbeiter.service';
import { AuthService } from '../../core/auth.service';
import { PlanungStammdatenService } from '../../core/planung-stammdaten.service';
import {
  MitarbeiterQualifikation,
  Qualifikation,
} from '../../core/einsatz.model';
import {
  ABSENCE_TYPES,
  Absence,
  AbsenceStatus,
  AbsenceType,
  Contract,
  ContractStatus,
  EmployeeDetail,
  EmployeeStatus,
  VacationAccount,
  WEEKDAYS,
  absenceStatusClass,
  absenceStatusLabel,
  absenceTypeLabel,
  contractStatusClass,
  contractStatusLabel,
  employeeStatusClass,
  employeeStatusLabel,
} from '../../core/mitarbeiter.model';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: EmployeeDetail }
  | VerbotenState
  | { kind: 'error' };

type Meldung = { art: 'erfolg' | 'fehler'; text: string };

@Component({
  selector: 'app-mitarbeiter-detail',
  imports: [
    Mappe,
    RouterLink,
    KeinZugriff,
    Dialog,
    Bestaetigung,
    Feld,
    Attest,
    ReactiveFormsModule,
  ],
  templateUrl: './mitarbeiter-detail.html',
  styleUrl: './mitarbeiter-detail.scss',
})
export class MitarbeiterDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(MitarbeiterService);
  private readonly planungSvc = inject(PlanungStammdatenService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  protected readonly weekdays = WEEKDAYS;
  protected readonly absenceTypes = ABSENCE_TYPES;
  protected readonly statusOptionen = [
    { wert: 'AKTIV', label: 'Aktiv' },
    { wert: 'INAKTIV', label: 'Inaktiv' },
    { wert: 'AUSGETRETEN', label: 'Ausgetreten (final)' },
  ];
  protected readonly tab = signal('persoenliches');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  protected readonly tabs: MappeTab[] = [
    { id: 'persoenliches', label: 'Persönliches' },
    { id: 'vertrag', label: 'Vertrag' },
    { id: 'abwesenheiten', label: 'Abwesenheiten' },
    { id: 'urlaub', label: 'Urlaub' },
    { id: 'qualifikationen', label: 'Qualifikationen' },
  ];

  // --- Rechte --------------------------------------------------------------
  protected readonly darfAnlegen = computed(() => this.auth.darf('hr', 'ANLEGEN'));
  protected readonly darfAendern = computed(() => this.auth.darf('hr', 'AENDERN'));
  protected readonly darfFreigeben = computed(() => this.auth.darf('hr', 'FREIGEBEN'));

  protected readonly meldung = signal<Meldung | null>(null);
  protected readonly aktionBusyId = signal<string | null>(null);

  // --- Status ändern -------------------------------------------------------
  protected readonly statusOffen = signal(false);
  protected readonly statusLaedt = signal(false);
  protected readonly statusMeldung = signal<string | null>(null);
  protected readonly statusForm = this.fb.group({
    status: this.fb.control<EmployeeStatus>('AKTIV', { nonNullable: true }),
    left_on: this.fb.control('', { nonNullable: true }),
  });
  /** Methode statt computed: liest einen FormControl-Wert (kein Signal), muss
   *  bei jeder Change-Detection frisch ausgewertet werden. */
  statusIstAustritt(): boolean {
    return this.statusForm.controls.status.value === 'AUSGETRETEN';
  }

  // --- Vertrag anlegen -----------------------------------------------------
  protected readonly vertragOffen = signal(false);
  protected readonly vertragLaedt = signal(false);
  protected readonly vertragMeldung = signal<string | null>(null);
  protected readonly vertragForm = this.fb.group({
    valid_from: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    valid_to: this.fb.control('', { nonNullable: true }),
    vacation_days_per_year: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, dezimalValidator],
    }),
    hours_monday: this.fb.control('0', { nonNullable: true, validators: [dezimalValidator] }),
    hours_tuesday: this.fb.control('0', { nonNullable: true, validators: [dezimalValidator] }),
    hours_wednesday: this.fb.control('0', { nonNullable: true, validators: [dezimalValidator] }),
    hours_thursday: this.fb.control('0', { nonNullable: true, validators: [dezimalValidator] }),
    hours_friday: this.fb.control('0', { nonNullable: true, validators: [dezimalValidator] }),
    hours_saturday: this.fb.control('0', { nonNullable: true, validators: [dezimalValidator] }),
    hours_sunday: this.fb.control('0', { nonNullable: true, validators: [dezimalValidator] }),
    notes: this.fb.control('', { nonNullable: true }),
  });

  // --- Vertrag kündigen ----------------------------------------------------
  protected readonly kuendigenContract = signal<Contract | null>(null);
  protected readonly kuendigenLaedt = signal(false);
  protected readonly kuendigenMeldung = signal<string | null>(null);
  protected readonly kuendigenForm = this.fb.group({
    valid_to: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    reason: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
  });

  // --- Abwesenheit anlegen -------------------------------------------------
  protected readonly abwOffen = signal(false);
  protected readonly abwLaedt = signal(false);
  protected readonly abwMeldung = signal<string | null>(null);
  protected readonly abwForm = this.fb.group({
    absence_type: this.fb.control<AbsenceType>('URLAUB', { nonNullable: true }),
    start_date: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    end_date: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    half_day_start: this.fb.control(false, { nonNullable: true }),
    half_day_end: this.fb.control(false, { nonNullable: true }),
    reason: this.fb.control('', { nonNullable: true }),
  });

  // --- Abwesenheit ablehnen (begründungspflichtig) -------------------------
  protected readonly ablehnenAbsence = signal<Absence | null>(null);
  protected readonly ablehnenLaedt = signal(false);

  // --- Urlaubskonto setzen -------------------------------------------------
  protected readonly urlaubOffen = signal(false);
  protected readonly urlaubLaedt = signal(false);
  protected readonly urlaubMeldung = signal<string | null>(null);
  protected readonly urlaubForm = this.fb.group({
    year: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    entitlement_days: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, dezimalValidator],
    }),
    carryover_days: this.fb.control('0', { nonNullable: true, validators: [dezimalValidator] }),
    adjustment_days: this.fb.control('0', { nonNullable: true, validators: [dezimalValidator] }),
    adjustment_reason: this.fb.control('', { nonNullable: true }),
  });

  private readonly dateFmt = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  });
  private readonly numFmt = new Intl.NumberFormat('de-DE', {
    maximumFractionDigits: 2,
  });
  private readonly rateFmt = new Intl.NumberFormat('de-DE', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.tab.set('persoenliches');
      this.qualNachweise.set([]);
      if (!id) {
        this.state.set({ kind: 'error' });
        return;
      }
      this.load(id);
    });

    // Qualifikationen erst laden, wenn der Reiter geöffnet wird (wie die übrigen
    // Nachlade-Tabs) — sie hängen an zwei zusätzlichen Abfragen.
    effect(() => {
      if (this.tab() === 'qualifikationen' && this.daten()) {
        this.qualLaden();
      }
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

  private heute(): string {
    return new Date().toISOString().slice(0, 10);
  }

  // ---- Status ändern ------------------------------------------------------
  statusOeffnen(): void {
    const d = this.daten();
    if (!d) return;
    this.statusForm.reset({ status: d.status, left_on: d.left_on ?? '' });
    this.statusMeldung.set(null);
    this.meldung.set(null);
    this.statusOffen.set(true);
  }

  statusSchliessen(): void {
    if (!this.statusLaedt()) this.statusOffen.set(false);
  }

  statusAbsenden(): void {
    const d = this.daten();
    if (!d || this.statusLaedt()) return;
    serverFehlerZuruecksetzen(this.statusForm);
    this.statusMeldung.set(null);
    const v = this.statusForm.getRawValue();
    if (v.status === 'AUSGETRETEN' && !v.left_on) {
      this.statusMeldung.set('Für den Austritt ist ein Austrittsdatum erforderlich.');
      return;
    }
    this.statusLaedt.set(true);
    this.svc
      .setStatus(d.id, {
        status: v.status,
        left_on: v.status === 'AUSGETRETEN' ? v.left_on : null,
      })
      .subscribe({
        next: () => {
          this.statusLaedt.set(false);
          this.statusOffen.set(false);
          this.meldung.set({ art: 'erfolg', text: 'Status wurde geändert.' });
          this.neuLaden();
        },
        error: (err) => {
          this.statusLaedt.set(false);
          this.statusMeldung.set(apiFehlerZuweisen(err, this.statusForm).formular);
        },
      });
  }

  // ---- Vertrag anlegen ----------------------------------------------------
  vertragOeffnen(): void {
    this.vertragForm.reset({
      valid_from: this.heute(),
      valid_to: '',
      vacation_days_per_year: '',
      hours_monday: '8',
      hours_tuesday: '8',
      hours_wednesday: '8',
      hours_thursday: '8',
      hours_friday: '8',
      hours_saturday: '0',
      hours_sunday: '0',
      notes: '',
    });
    this.vertragMeldung.set(null);
    this.meldung.set(null);
    this.vertragOffen.set(true);
  }

  vertragSchliessen(): void {
    if (!this.vertragLaedt()) this.vertragOffen.set(false);
  }

  vertragAbsenden(): void {
    const d = this.daten();
    if (!d || this.vertragLaedt()) return;
    serverFehlerZuruecksetzen(this.vertragForm);
    this.vertragMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.vertragForm);
    if (this.vertragForm.invalid) return;

    const v = this.vertragForm.getRawValue();
    this.vertragLaedt.set(true);
    this.svc
      .createContract(d.id, {
        valid_from: v.valid_from,
        valid_to: v.valid_to || null,
        vacation_days_per_year: deZuApiDezimal(v.vacation_days_per_year),
        hours_monday: deZuApiDezimal(v.hours_monday),
        hours_tuesday: deZuApiDezimal(v.hours_tuesday),
        hours_wednesday: deZuApiDezimal(v.hours_wednesday),
        hours_thursday: deZuApiDezimal(v.hours_thursday),
        hours_friday: deZuApiDezimal(v.hours_friday),
        hours_saturday: deZuApiDezimal(v.hours_saturday),
        hours_sunday: deZuApiDezimal(v.hours_sunday),
        notes: v.notes.trim() || null,
      })
      .subscribe({
        next: () => {
          this.vertragLaedt.set(false);
          this.vertragOffen.set(false);
          this.meldung.set({ art: 'erfolg', text: 'Arbeitsvertrag wurde angelegt.' });
          this.neuLaden();
        },
        error: (err) => {
          this.vertragLaedt.set(false);
          this.vertragMeldung.set(apiFehlerZuweisen(err, this.vertragForm).formular);
        },
      });
  }

  // ---- Vertrag kündigen (begründungspflichtig) ----------------------------
  kuendigenOeffnen(c: Contract): void {
    this.kuendigenForm.reset({ valid_to: this.heute(), reason: '' });
    this.kuendigenMeldung.set(null);
    this.meldung.set(null);
    this.kuendigenContract.set(c);
  }

  kuendigenSchliessen(): void {
    if (!this.kuendigenLaedt()) this.kuendigenContract.set(null);
  }

  kuendigenAbsenden(): void {
    const c = this.kuendigenContract();
    if (!c || this.kuendigenLaedt()) return;
    serverFehlerZuruecksetzen(this.kuendigenForm);
    this.kuendigenMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.kuendigenForm);
    if (this.kuendigenForm.invalid) return;

    const v = this.kuendigenForm.getRawValue();
    this.kuendigenLaedt.set(true);
    this.svc
      .terminateContract(c.id, { valid_to: v.valid_to, reason: v.reason.trim() })
      .subscribe({
        next: () => {
          this.kuendigenLaedt.set(false);
          this.kuendigenContract.set(null);
          this.meldung.set({ art: 'erfolg', text: 'Vertrag wurde gekündigt.' });
          this.neuLaden();
        },
        error: (err) => {
          this.kuendigenLaedt.set(false);
          this.kuendigenMeldung.set(apiFehlerZuweisen(err, this.kuendigenForm).formular);
        },
      });
  }

  // ---- Abwesenheit anlegen ------------------------------------------------
  abwOeffnen(): void {
    this.abwForm.reset({
      absence_type: 'URLAUB',
      start_date: this.heute(),
      end_date: this.heute(),
      half_day_start: false,
      half_day_end: false,
      reason: '',
    });
    this.abwMeldung.set(null);
    this.meldung.set(null);
    this.abwOffen.set(true);
  }

  abwSchliessen(): void {
    if (!this.abwLaedt()) this.abwOffen.set(false);
  }

  abwAbsenden(): void {
    const d = this.daten();
    if (!d || this.abwLaedt()) return;
    serverFehlerZuruecksetzen(this.abwForm);
    this.abwMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.abwForm);
    if (this.abwForm.invalid) return;

    const v = this.abwForm.getRawValue();
    this.abwLaedt.set(true);
    // days_count rechnet der Server aus dem Vertrag — nie im Frontend senden.
    this.svc
      .createAbsence(d.id, {
        absence_type: v.absence_type,
        start_date: v.start_date,
        end_date: v.end_date,
        half_day_start: v.half_day_start,
        half_day_end: v.half_day_end,
        reason: v.reason.trim() || null,
      })
      .subscribe({
        next: () => {
          this.abwLaedt.set(false);
          this.abwOffen.set(false);
          this.meldung.set({ art: 'erfolg', text: 'Abwesenheit wurde angelegt (Entwurf).' });
          this.neuLaden();
        },
        error: (err) => {
          this.abwLaedt.set(false);
          this.abwMeldung.set(apiFehlerZuweisen(err, this.abwForm).formular);
        },
      });
  }

  // ---- Abwesenheits-Workflow ----------------------------------------------

  /**
   * Nimmt diese Abwesenheit noch eine Arbeitsunfähigkeitsbescheinigung an?
   * Eine verworfene (abgelehnt/zurückgezogen) nicht: Der Antrag ist
   * gegenstandslos, ein Gesundheitsdatum daran wäre eine Verarbeitung ohne
   * Zweck — die DB verbietet den Anhang (Migration 0072).
   */
  attestOffen(a: Absence): boolean {
    return a.status !== 'ABGELEHNT' && a.status !== 'ZURUECKGEZOGEN';
  }

  einreichen(a: Absence): void {
    this.absenceAktion(a.id, this.svc.submitAbsence(a.id), 'Antrag eingereicht.');
  }

  zurueckziehen(a: Absence): void {
    this.absenceAktion(a.id, this.svc.withdrawAbsence(a.id), 'Antrag zurückgezogen.');
  }

  genehmigen(a: Absence): void {
    this.absenceAktion(
      a.id,
      this.svc.approveAbsence(a.id, { note: null }),
      'Antrag genehmigt.',
    );
  }

  private absenceAktion(id: string, obs: Observable<Absence>, erfolg: string): void {
    if (this.aktionBusyId()) return;
    this.aktionBusyId.set(id);
    this.meldung.set(null);
    obs.subscribe({
      next: () => {
        this.aktionBusyId.set(null);
        this.meldung.set({ art: 'erfolg', text: erfolg });
        this.neuLaden();
      },
      error: (err) => {
        this.aktionBusyId.set(null);
        this.meldung.set({ art: 'fehler', text: this.aktionsFehler(err) });
      },
    });
  }

  // ---- Abwesenheit ablehnen (begründungspflichtig) ------------------------
  ablehnenFragen(a: Absence): void {
    this.meldung.set(null);
    this.ablehnenAbsence.set(a);
  }

  ablehnenAbbrechen(): void {
    if (!this.ablehnenLaedt()) this.ablehnenAbsence.set(null);
  }

  ablehnenBestaetigen(begruendung: string | null): void {
    const a = this.ablehnenAbsence();
    if (!a || this.ablehnenLaedt()) return;
    this.ablehnenLaedt.set(true);
    this.svc.rejectAbsence(a.id, { note: begruendung }).subscribe({
      next: () => {
        this.ablehnenLaedt.set(false);
        this.ablehnenAbsence.set(null);
        this.meldung.set({ art: 'erfolg', text: 'Antrag abgelehnt.' });
        this.neuLaden();
      },
      error: (err) => {
        this.ablehnenLaedt.set(false);
        this.ablehnenAbsence.set(null);
        this.meldung.set({ art: 'fehler', text: this.aktionsFehler(err) });
      },
    });
  }

  // ---- Urlaubskonto setzen ------------------------------------------------
  urlaubOeffnen(): void {
    const konto = this.daten()?.vacation_account;
    this.urlaubForm.reset({
      year: String(konto?.year ?? new Date().getFullYear()),
      entitlement_days: konto?.entitlement_days ?? '',
      carryover_days: konto?.carryover_days ?? '0',
      adjustment_days: konto?.adjustment_days ?? '0',
      adjustment_reason: konto?.adjustment_reason ?? '',
    });
    this.urlaubMeldung.set(null);
    this.meldung.set(null);
    this.urlaubOffen.set(true);
  }

  urlaubSchliessen(): void {
    if (!this.urlaubLaedt()) this.urlaubOffen.set(false);
  }

  urlaubAbsenden(): void {
    const d = this.daten();
    if (!d || this.urlaubLaedt()) return;
    serverFehlerZuruecksetzen(this.urlaubForm);
    this.urlaubMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.urlaubForm);
    if (this.urlaubForm.invalid) return;

    const v = this.urlaubForm.getRawValue();
    const adjustment = deZuApiDezimal(v.adjustment_days);
    const reason = v.adjustment_reason.trim();
    // Server-CHECK: eine Anpassung ungleich 0 verlangt eine Begründung.
    if (adjustment !== '' && Number(adjustment) !== 0 && !reason) {
      this.urlaubMeldung.set('Eine Anpassung ungleich 0 erfordert eine Begründung.');
      return;
    }
    const jahr = Number(v.year);
    if (!Number.isInteger(jahr)) {
      this.urlaubMeldung.set('Bitte ein gültiges Jahr angeben.');
      return;
    }
    this.urlaubLaedt.set(true);
    this.svc
      .setVacationBudget(d.id, {
        year: jahr,
        entitlement_days: deZuApiDezimal(v.entitlement_days),
        carryover_days: deZuApiDezimal(v.carryover_days),
        adjustment_days: adjustment,
        adjustment_reason: reason || null,
      })
      .subscribe({
        next: () => {
          this.urlaubLaedt.set(false);
          this.urlaubOffen.set(false);
          this.meldung.set({ art: 'erfolg', text: 'Urlaubskonto wurde gesetzt.' });
          this.neuLaden();
        },
        error: (err) => {
          this.urlaubLaedt.set(false);
          this.urlaubMeldung.set(apiFehlerZuweisen(err, this.urlaubForm).formular);
        },
      });
  }

  // ---- Darstellungshelfer -------------------------------------------------
  statusLabel(s: EmployeeStatus): string {
    return employeeStatusLabel(s);
  }
  statusClass(s: EmployeeStatus): string {
    return employeeStatusClass(s);
  }
  contractStatusLabel(s: ContractStatus): string {
    return contractStatusLabel(s);
  }
  contractStatusClass(s: ContractStatus): string {
    return contractStatusClass(s);
  }
  absenceStatusLabel(s: AbsenceStatus): string {
    return absenceStatusLabel(s);
  }
  absenceStatusClass(s: AbsenceStatus): string {
    return absenceStatusClass(s);
  }
  absenceTypeLabel(t: AbsenceType): string {
    return absenceTypeLabel(t);
  }

  d(iso: string | null): string {
    if (!iso) return '—';
    return this.dateFmt.format(new Date(iso));
  }
  /** Decimal-String → deutsche Anzeige ohne Einheit. */
  num(value: string): string {
    return this.numFmt.format(Number(value));
  }
  rate(value: string): string {
    return `${this.rateFmt.format(Number(value))} €/h`;
  }

  /** Höchste Tages-Sollstunde eines Vertrags — Bezugsgröße für die Balken. */
  maxWeekdayHours(c: Contract): number {
    const values = this.weekdays.map((w) => Number(c[w.key]));
    const max = Math.max(...values, 0);
    return max > 0 ? max : 1;
  }
  weekdayHours(c: Contract, key: keyof Contract): string {
    return this.num(c[key] as string);
  }
  weekdayPercent(c: Contract, key: keyof Contract): number {
    const v = Number(c[key]);
    return Math.round((v / this.maxWeekdayHours(c)) * 100);
  }

  /** Urlaub: verbraucht/Summe als Prozent für die Fortschrittsdarstellung. */
  vacationPercent(a: VacationAccount): number {
    const total = Number(a.total_days);
    if (total <= 0) return 0;
    const used = Number(a.used_days);
    return Math.min(100, Math.max(0, Math.round((used / total) * 100)));
  }
  vacationAria(a: VacationAccount): string {
    return `Urlaub ${this.num(a.used_days)} von ${this.num(a.total_days)} Tagen verbraucht, ${this.num(a.remaining_days)} Tage Rest.`;
  }

  // ===================== Qualifikationen (Migration 0078) =================
  // Der NACHWEIS ist ein Personaldatum und hängt am `hr`-Recht. Der Disponent
  // sieht auf der Plantafel nur die FOLGE („X hat keinen Nachweis für …"), nicht
  // die Akte — dieselbe Grenze wie bei der Abwesenheitsart (DSGVO).
  protected readonly qualNachweise = signal<MitarbeiterQualifikation[]>([]);
  protected readonly qualKatalog = signal<Qualifikation[]>([]);
  protected readonly qualDialogOffen = signal(false);
  protected readonly qualLaedt = signal(false);
  protected readonly qualMeldung = signal<string | null>(null);
  protected readonly qualEntfernen = signal<MitarbeiterQualifikation | null>(null);

  protected readonly qualForm = this.fb.group({
    qualification_id: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    valid_from: this.fb.control('', { nonNullable: true }),
    valid_until: this.fb.control('', { nonNullable: true }),
    evidence_note: this.fb.control('', { nonNullable: true }),
  });

  protected readonly qualOptionen = computed(() =>
    this.qualKatalog().map((q) => ({
      wert: q.id,
      label: q.expires ? q.label + ' (läuft ab)' : q.label,
    })),
  );

  /** Verlangt die aktuell gewählte Qualifikation ein Gültig-bis? */
  qualBrauchtAblauf(): boolean {
    const id = this.qualForm.controls.qualification_id.value;
    return !!this.qualKatalog().find((q) => q.id === id)?.expires;
  }

  qualLaden(): void {
    const id = this.daten()?.id;
    if (!id) return;
    this.planungSvc.mitarbeiterQualifikationen(id).subscribe({
      next: (n) => this.qualNachweise.set(n),
      error: () => this.qualNachweise.set([]),
    });
    this.planungSvc.listQualifikationen().subscribe({
      next: (k) => this.qualKatalog.set(k),
      error: () => this.qualKatalog.set([]),
    });
  }

  qualNeu(): void {
    this.qualForm.reset({
      qualification_id: '', valid_from: '', valid_until: '', evidence_note: '',
    });
    this.qualMeldung.set(null);
    this.qualDialogOffen.set(true);
  }

  qualBearbeiten(n: MitarbeiterQualifikation): void {
    this.qualForm.reset({
      qualification_id: n.qualification.id,
      valid_from: n.valid_from ?? '',
      valid_until: n.valid_until ?? '',
      evidence_note: n.evidence_note ?? '',
    });
    this.qualMeldung.set(null);
    this.qualDialogOffen.set(true);
  }

  qualDialogSchliessen(): void {
    if (!this.qualLaedt()) this.qualDialogOffen.set(false);
  }

  qualAbsenden(): void {
    const id = this.daten()?.id;
    if (!id || this.qualLaedt()) return;
    this.qualForm.markAllAsTouched();
    if (this.qualForm.invalid) return;
    const v = this.qualForm.getRawValue();
    this.qualLaedt.set(true);
    this.qualMeldung.set(null);
    this.planungSvc
      .setMitarbeiterQualifikation(id, {
        qualification_id: v.qualification_id,
        valid_from: v.valid_from || null,
        valid_until: v.valid_until || null,
        evidence_note: v.evidence_note.trim() || null,
      })
      .subscribe({
        next: () => {
          this.qualLaedt.set(false);
          this.qualDialogOffen.set(false);
          this.qualLaden();
        },
        error: (err) => {
          this.qualLaedt.set(false);
          this.qualMeldung.set(
            fehlerDetail(err) ?? 'Der Nachweis ließ sich nicht speichern.',
          );
        },
      });
  }

  qualEntfernenFragen(n: MitarbeiterQualifikation): void {
    this.qualEntfernen.set(n);
  }
  qualEntfernenAbbrechen(): void {
    if (!this.qualLaedt()) this.qualEntfernen.set(null);
  }
  qualEntfernenBestaetigen(): void {
    const n = this.qualEntfernen();
    const id = this.daten()?.id;
    if (!n || !id || this.qualLaedt()) return;
    this.qualLaedt.set(true);
    this.planungSvc.removeMitarbeiterQualifikation(id, n.qualification.id).subscribe({
      next: () => {
        this.qualLaedt.set(false);
        this.qualEntfernen.set(null);
        this.qualLaden();
      },
      error: () => {
        this.qualLaedt.set(false);
        this.qualEntfernen.set(null);
      },
    });
  }
}
