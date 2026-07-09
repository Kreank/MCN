import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Subject, debounceTime, distinctUntilChanged, map } from 'rxjs';
import { EinsatzService } from '../../core/einsatz.service';
import { AuftragService } from '../../core/auftrag.service';
import { PartyService } from '../../core/party.service';
import { AuthService } from '../../core/auth.service';
import {
  ServiceJobCreate,
  ServiceJobPage,
  ServiceJobStatus,
  serviceJobStatusClass,
  serviceJobStatusLabel,
} from '../../core/einsatz.model';
import { PlanungNav } from '../planung-nav/planung-nav';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld } from '../../shared/formular/feld';
import { ReferenzWahl, RefSuche } from '../../shared/formular/referenz-wahl';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: ServiceJobPage }
  | VerbotenState
  | { kind: 'error' };

type Segment = { value: ServiceJobStatus | null; label: string };
type Meldung = { art: 'erfolg' | 'fehler'; text: string };

@Component({
  selector: 'app-einsaetze',
  imports: [RouterLink, PlanungNav, KeinZugriff, ReactiveFormsModule, Dialog, Feld, ReferenzWahl],
  templateUrl: './einsaetze.html',
  styleUrl: './einsaetze.scss',
})
export class Einsaetze {
  private readonly svc = inject(EinsatzService);
  private readonly auftragSvc = inject(AuftragService);
  private readonly partySvc = inject(PartyService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  /** Einsätze anlegen ist Dispositionssache: Recht ANLEGEN mit Scope ALLE. */
  protected readonly darfAnlegen = computed(() => {
    const u = this.auth.user();
    return (
      u?.permissions.some(
        (p) => p.module === 'workflow' && p.action === 'ANLEGEN' && p.row_scope === 'ALLE',
      ) ?? false
    );
  });

  protected readonly meldung = signal<Meldung | null>(null);
  protected readonly neuOffen = signal(false);
  protected readonly neuLaedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);
  protected readonly neuForm = this.fb.group({
    work_order_id: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    scheduled_start: this.fb.control('', { nonNullable: true }),
    scheduled_end: this.fb.control('', { nonNullable: true }),
    on_site_contact_party_id: this.fb.control('', { nonNullable: true }),
    access_instructions: this.fb.control('', { nonNullable: true }),
  });

  protected readonly auftragSuche: RefSuche = (q) =>
    this.auftragSvc.list({ page: 1, page_size: 20, q }).pipe(
      map((p) => p.items.map((o) => ({ id: o.id, label: o.title, sub: o.order_number }))),
    );
  protected readonly partySuche: RefSuche = (q) =>
    this.partySvc.list({ page: 1, page_size: 20, q }).pipe(
      map((p) => p.items.map((x) => ({ id: x.id, label: x.display_name }))),
    );

  protected readonly pageSize = 20;
  protected readonly segments: Segment[] = [
    { value: null, label: 'Alle' },
    { value: 'GEPLANT', label: 'Geplant' },
    { value: 'VOR_ORT', label: 'Vor Ort' },
    { value: 'ABGESCHLOSSEN', label: 'Abgeschlossen' },
  ];

  protected readonly query = signal('');
  protected readonly status = signal<ServiceJobStatus | null>(null);
  protected readonly page = signal(1);
  protected readonly state = signal<ViewState>({ kind: 'loading' });

  protected readonly skeletons = Array.from({ length: 6 });

  private readonly searchInput$ = new Subject<string>();
  private reqId = 0;

  private readonly dateFmt = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });

  protected readonly totalPages = computed(() => {
    const s = this.state();
    if (s.kind !== 'ready') return 1;
    return Math.max(1, Math.ceil(s.data.total / s.data.page_size));
  });

  protected readonly resultSummary = computed(() => {
    const s = this.state();
    if (s.kind === 'loading') return 'Einsätze werden geladen.';
    if (s.kind === 'forbidden') return 'Keine Berechtigung für die Einsätze.';
    if (s.kind === 'error') return 'Einsätze konnten nicht geladen werden.';
    const t = s.data.total;
    if (t === 0) return 'Keine Einsätze gefunden.';
    return `${t} ${t === 1 ? 'Einsatz' : 'Einsätze'} gefunden, Seite ${s.data.page} von ${this.totalPages()}.`;
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

  selectSegment(value: ServiceJobStatus | null): void {
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
      work_order_id: '',
      scheduled_start: '',
      scheduled_end: '',
      on_site_contact_party_id: '',
      access_instructions: '',
    });
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
    const payload: ServiceJobCreate = {
      work_order_id: v.work_order_id,
      scheduled_start: v.scheduled_start || null,
      scheduled_end: v.scheduled_end || null,
      on_site_contact_party_id: v.on_site_contact_party_id || null,
      access_instructions: v.access_instructions.trim() || null,
    };

    this.neuLaedt.set(true);
    this.svc.create(payload).subscribe({
      next: () => {
        this.neuLaedt.set(false);
        this.neuOffen.set(false);
        this.meldung.set({ art: 'erfolg', text: 'Einsatz angelegt.' });
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
  statusLabel(s: ServiceJobStatus): string {
    return serviceJobStatusLabel(s);
  }
  statusClass(s: ServiceJobStatus): string {
    return serviceJobStatusClass(s);
  }
  planLabel(iso: string | null): string {
    if (!iso) return 'ungeplant';
    return this.dateFmt.format(new Date(iso));
  }
}
