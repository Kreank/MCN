import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Subject, debounceTime, distinctUntilChanged, map } from 'rxjs';
import { MitarbeiterService } from '../../core/mitarbeiter.service';
import { EinsatzService } from '../../core/einsatz.service';
import { ArtikelService } from '../../core/artikel.service';
import { AuthService } from '../../core/auth.service';
import {
  EmployeeCreate,
  EmployeePage,
  EmployeeStatus,
  employeeStatusClass,
  employeeStatusLabel,
} from '../../core/mitarbeiter.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { ReferenzWahl, RefSuche } from '../../shared/formular/referenz-wahl';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import { erforderlichGetrimmt } from '../../shared/formular/text';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: EmployeePage }
  | VerbotenState
  | { kind: 'error' };

type Segment = { value: EmployeeStatus | null; label: string };
type Meldung = { art: 'erfolg' | 'fehler'; text: string };

@Component({
  selector: 'app-mitarbeiter',
  imports: [RouterLink, KeinZugriff, ReactiveFormsModule, Dialog, Feld, ReferenzWahl],
  templateUrl: './mitarbeiter.html',
  styleUrl: './mitarbeiter.scss',
})
export class Mitarbeiter {
  private readonly svc = inject(MitarbeiterService);
  private readonly planungSvc = inject(EinsatzService);
  private readonly artikelSvc = inject(ArtikelService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  protected readonly pageSize = 20;
  protected readonly segments: Segment[] = [
    { value: null, label: 'Alle' },
    { value: 'AKTIV', label: 'Aktiv' },
    { value: 'INAKTIV', label: 'Inaktiv' },
    { value: 'AUSGETRETEN', label: 'Ausgetreten' },
  ];

  protected readonly query = signal('');
  protected readonly status = signal<EmployeeStatus | null>(null);
  protected readonly page = signal(1);
  protected readonly state = signal<ViewState>({ kind: 'loading' });

  protected readonly skeletons = Array.from({ length: 6 });

  // --- Rechte (nur UI-Sichtbarkeit; der Server setzt sie durch) -----------
  protected readonly darfAnlegen = computed(() => this.auth.darf('hr', 'ANLEGEN'));

  // --- Anlage-Dialog ------------------------------------------------------
  protected readonly meldung = signal<Meldung | null>(null);
  protected readonly neuOffen = signal(false);
  protected readonly neuLaedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);
  /** Lohngruppen als Auswahl; leer, falls kein pricing-LESEN-Recht (siehe Hinweis). */
  protected readonly lohngruppen = signal<FeldOption[]>([]);
  protected readonly lohngruppenGeladen = signal(false);
  /**
   * Anlage-Formular (Befund F1).
   *
   * Vorher: `party_id` war Pflicht und kam aus einer Suche über den
   * **Kontaktstamm** — man musste den Mitarbeiter also erst als Kunde anlegen
   * und fand ihn dann in derselben Trefferliste wie die Kundschaft. Kein
   * einziges Namensfeld.
   *
   * Jetzt: Vor- und Nachname direkt; die Person entsteht im Hintergrund.
   * Der Nachname ist Pflicht (wie überall seit 0125), der Vorname nicht.
   */
  protected readonly neuForm = this.fb.group({
    app_user_id: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    salutation: this.fb.control('', { nonNullable: true }),
    first_name: this.fb.control('', { nonNullable: true }),
    last_name: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, erforderlichGetrimmt],
    }),
    birth_date: this.fb.control('', { nonNullable: true }),
    hired_on: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    wage_group_id: this.fb.control('', { nonNullable: true }),
    notes: this.fb.control('', { nonNullable: true }),
  });

  /** Benutzersuche (aktive app_user) für app_user_id. */
  protected readonly benutzerSuche: RefSuche = (q) =>
    this.planungSvc.listUsers(q).pipe(
      map((users) => users.map((u) => ({ id: u.id, label: u.display_name }))),
    );


  private readonly searchInput$ = new Subject<string>();
  private reqId = 0;

  private readonly dateFmt = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  });
  private readonly rateFmt = new Intl.NumberFormat('de-DE', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

  protected readonly totalPages = computed(() => {
    const s = this.state();
    if (s.kind !== 'ready') return 1;
    return Math.max(1, Math.ceil(s.data.total / s.data.page_size));
  });

  protected readonly resultSummary = computed(() => {
    const s = this.state();
    if (s.kind === 'loading') return 'Mitarbeiter werden geladen.';
    if (s.kind === 'forbidden') return 'Keine Berechtigung für den Personalbereich.';
    if (s.kind === 'error') return 'Mitarbeiter konnten nicht geladen werden.';
    const t = s.data.total;
    if (t === 0) return 'Keine Mitarbeiter gefunden.';
    return `${t} Mitarbeiter gefunden, Seite ${s.data.page} von ${this.totalPages()}.`;
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

  selectSegment(value: EmployeeStatus | null): void {
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

  // ---- Anlegen ------------------------------------------------------------
  neuOeffnen(): void {
    this.neuForm.reset({
      app_user_id: '',
      salutation: '',
      first_name: '',
      last_name: '',
      birth_date: '',
      hired_on: '',
      wage_group_id: '',
      notes: '',
    });
    this.formularMeldung.set(null);
    this.neuOffen.set(true);
    if (!this.lohngruppenGeladen()) this.ladeLohngruppen();
  }

  neuSchliessen(): void {
    if (!this.neuLaedt()) this.neuOffen.set(false);
  }

  private ladeLohngruppen(): void {
    this.lohngruppenGeladen.set(true);
    this.artikelSvc.listWageGroups().subscribe({
      next: (wg) =>
        this.lohngruppen.set(
          wg.map((g) => ({ wert: g.id, label: `${g.name} · ${this.rate(g.hourly_rate)}` })),
        ),
      // 403 (kein pricing-LESEN) oder Netzfehler: Liste bleibt leer; Lohngruppe
      // ist optional. Der Hinweis im Dialog nennt das.
      error: () => this.lohngruppen.set([]),
    });
  }

  neuAbsenden(): void {
    if (this.neuLaedt()) return;
    serverFehlerZuruecksetzen(this.neuForm);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.neuForm);
    if (this.neuForm.invalid) return;

    const v = this.neuForm.getRawValue();
    const payload: EmployeeCreate = {
      app_user_id: v.app_user_id,
      // Kein `party_id` mehr: Die Person entsteht serverseitig aus diesen
      // Feldern (Befund F1). Leerer Vorname wird zu null — „nicht erhoben"
      // ist NULL, nicht Leerstring.
      salutation: v.salutation.trim() || null,
      first_name: v.first_name.trim() || null,
      last_name: v.last_name.trim(),
      birth_date: v.birth_date || null,
      hired_on: v.hired_on,
      wage_group_id: v.wage_group_id || null,
      notes: v.notes.trim() || null,
    };

    this.neuLaedt.set(true);
    this.svc.createEmployee(payload).subscribe({
      next: (e) => {
        this.neuLaedt.set(false);
        this.neuOffen.set(false);
        this.meldung.set({
          art: 'erfolg',
          text: `Mitarbeiter „${e.display_name}“ (Nr. ${e.employee_number}) wurde angelegt.`,
        });
        this.page.set(1);
        this.status.set(null);
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
  statusLabel(s: EmployeeStatus): string {
    return employeeStatusLabel(s);
  }
  statusClass(s: EmployeeStatus): string {
    return employeeStatusClass(s);
  }
  d(iso: string | null): string {
    if (!iso) return '—';
    return this.dateFmt.format(new Date(iso));
  }
  /** Lohngruppe inkl. Stundensatz (Decimal-String → nur zur Anzeige gerechnet). */
  rate(value: string): string {
    return `${this.rateFmt.format(Number(value))} €/h`;
  }
}
