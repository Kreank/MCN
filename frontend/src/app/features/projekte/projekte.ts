import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Subject, debounceTime, distinctUntilChanged, map } from 'rxjs';
import { ProjektService } from '../../core/projekt.service';
import { PropertyService } from '../../core/property.service';
import { AuthService } from '../../core/auth.service';
import { Project, ProjectCreate, ProjectPage, ProjectStatus } from '../../core/projekt.model';
import { ProjekteNav } from '../projekte-nav/projekte-nav';
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
  | { kind: 'ready'; data: ProjectPage }
  | VerbotenState
  | { kind: 'error' };

type Segment = { value: ProjectStatus | null; label: string };
type Meldung = { art: 'erfolg' | 'fehler'; text: string };

@Component({
  selector: 'app-projekte',
  imports: [RouterLink, ReactiveFormsModule, ProjekteNav, KeinZugriff, Dialog, Feld, ReferenzWahl],
  templateUrl: './projekte.html',
  styleUrl: './projekte.scss',
})
export class Projekte {
  private readonly svc = inject(ProjektService);
  private readonly propertySvc = inject(PropertyService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  protected readonly pageSize = 20;
  protected readonly segments: Segment[] = [
    { value: null, label: 'Alle' },
    { value: 'OPEN', label: 'Offen' },
    { value: 'CLOSED', label: 'Geschlossen' },
  ];

  protected readonly query = signal('');
  protected readonly status = signal<ProjectStatus | null>(null);
  protected readonly page = signal(1);
  protected readonly state = signal<ViewState>({ kind: 'loading' });

  protected readonly skeletons = Array.from({ length: 6 });

  protected readonly darfAnlegen = computed(() => this.auth.darf('workflow', 'ANLEGEN'));

  // --- Anlage-Dialog ------------------------------------------------------
  protected readonly meldung = signal<Meldung | null>(null);
  protected readonly neuOffen = signal(false);
  protected readonly neuLaedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);
  protected readonly neuForm = this.fb.group({
    name: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(200)],
    }),
    property_id: this.fb.control('', { nonNullable: true }),
    start_date: this.fb.control('', { nonNullable: true }),
    target_end_date: this.fb.control('', { nonNullable: true }),
  });

  /** Liegenschaftssuche für den optionalen Objektbezug. */
  protected readonly liegenschaftSuche: RefSuche = (q) =>
    this.propertySvc.list({ page: 1, page_size: 20, q }).pipe(
      map((p) =>
        p.items.map((o) => ({ id: o.id, label: o.name, sub: `${o.property_number} · ${o.city}` })),
      ),
    );

  private readonly searchInput$ = new Subject<string>();
  private reqId = 0;

  protected readonly totalPages = computed(() => {
    const s = this.state();
    if (s.kind !== 'ready') return 1;
    return Math.max(1, Math.ceil(s.data.total / s.data.page_size));
  });

  protected readonly resultSummary = computed(() => {
    const s = this.state();
    if (s.kind === 'loading') return 'Projekte werden geladen.';
    if (s.kind === 'forbidden') return 'Keine Berechtigung für die Projekte.';
    if (s.kind === 'error') return 'Projekte konnten nicht geladen werden.';
    const t = s.data.total;
    if (t === 0) return 'Keine Projekte gefunden.';
    return `${t} ${t === 1 ? 'Projekt' : 'Projekte'} gefunden, Seite ${s.data.page} von ${this.totalPages()}.`;
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

  selectSegment(value: ProjectStatus | null): void {
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
    this.neuForm.reset({ name: '', property_id: '', start_date: '', target_end_date: '' });
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
    const payload: ProjectCreate = {
      name: v.name.trim(),
      property_ids: v.property_id ? [v.property_id] : [],
      start_date: v.start_date || null,
      target_end_date: v.target_end_date || null,
    };

    this.neuLaedt.set(true);
    this.svc.create(payload).subscribe({
      next: () => {
        this.neuLaedt.set(false);
        this.neuOffen.set(false);
        this.meldung.set({ art: 'erfolg', text: `Projekt „${payload.name}“ wurde angelegt.` });
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
  monogram(p: Project): string {
    const parts = p.name.trim().split(/\s+/).filter(Boolean);
    if (parts.length === 0) return '–';
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }

  statusLabel(s: ProjectStatus): string {
    return s === 'OPEN' ? 'Offen' : 'Geschlossen';
  }

  statusClass(s: ProjectStatus): string {
    return s === 'OPEN' ? 'stamp--positive' : '';
  }
}
