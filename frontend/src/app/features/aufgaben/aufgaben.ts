import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Observable, Subject, debounceTime, distinctUntilChanged } from 'rxjs';
import { AufgabeService } from '../../core/aufgabe.service';
import { AuthService } from '../../core/auth.service';
import { Task, TaskCreate, TaskPage, TaskStatus } from '../../core/aufgabe.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerDetail, fehlerState, istVerboten } from '../../shared/http-fehler';
import { Dialog } from '../../shared/dialog/dialog';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { Feld } from '../../shared/formular/feld';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: TaskPage }
  | VerbotenState
  | { kind: 'error' };

type Segment = { value: TaskStatus | null; label: string };
type Meldung = { art: 'erfolg' | 'fehler'; text: string };

@Component({
  selector: 'app-aufgaben',
  imports: [RouterLink, ReactiveFormsModule, KeinZugriff, Dialog, Bestaetigung, Feld],
  templateUrl: './aufgaben.html',
  styleUrl: './aufgaben.scss',
})
export class Aufgaben {
  private readonly svc = inject(AufgabeService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  protected readonly pageSize = 20;
  protected readonly segments: Segment[] = [
    { value: null, label: 'Alle' },
    { value: 'OFFEN', label: 'Offen' },
    { value: 'ERLEDIGT', label: 'Erledigt' },
  ];

  protected readonly query = signal('');
  protected readonly status = signal<TaskStatus | null>(null);
  protected readonly page = signal(1);
  protected readonly state = signal<ViewState>({ kind: 'loading' });

  protected readonly skeletons = Array.from({ length: 6 });

  // --- Rechte (nur UI-Sichtbarkeit; der Server setzt sie durch) -----------
  protected readonly darfAnlegen = computed(() => this.auth.darf('workflow', 'ANLEGEN'));
  protected readonly darfAendern = computed(() => this.auth.darf('workflow', 'AENDERN'));

  // --- Anlage-Dialog ------------------------------------------------------
  protected readonly neuOffen = signal(false);
  protected readonly neuLaedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);
  protected readonly neuForm = this.fb.group({
    title: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(200)],
    }),
    description: this.fb.control('', { nonNullable: true }),
    due_date: this.fb.control('', { nonNullable: true }),
  });

  // --- Zeilen-Aktionen ----------------------------------------------------
  protected readonly meldung = signal<Meldung | null>(null);
  protected readonly aktionBusyId = signal<string | null>(null);
  protected readonly verwerfenTask = signal<Task | null>(null);
  protected readonly verwerfenLaedt = signal(false);

  private readonly searchInput$ = new Subject<string>();
  private reqId = 0;

  protected readonly totalPages = computed(() => {
    const s = this.state();
    if (s.kind !== 'ready') return 1;
    return Math.max(1, Math.ceil(s.data.total / s.data.page_size));
  });

  protected readonly resultSummary = computed(() => {
    const s = this.state();
    if (s.kind === 'loading') return 'Aufgaben werden geladen.';
    if (s.kind === 'forbidden') return 'Keine Berechtigung für die Aufgaben.';
    if (s.kind === 'error') return 'Aufgaben konnten nicht geladen werden.';
    const t = s.data.total;
    if (t === 0) return 'Keine Aufgaben gefunden.';
    return `${t} ${t === 1 ? 'Aufgabe' : 'Aufgaben'} gefunden, Seite ${s.data.page} von ${this.totalPages()}.`;
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

  selectSegment(value: TaskStatus | null): void {
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
    this.neuForm.reset({ title: '', description: '', due_date: '' });
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
    const payload: TaskCreate = {
      title: v.title.trim(),
      description: v.description.trim() || null,
      due_date: v.due_date || null,
    };

    this.neuLaedt.set(true);
    this.svc.create(payload).subscribe({
      next: () => {
        this.neuLaedt.set(false);
        this.neuOffen.set(false);
        this.meldung.set({ art: 'erfolg', text: `Aufgabe „${payload.title}“ wurde angelegt.` });
        this.page.set(1);
        this.fetch();
      },
      error: (err) => {
        this.neuLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.neuForm).formular);
      },
    });
  }

  // ---- Zeilen-Aktionen ----------------------------------------------------
  erledigen(t: Task): void {
    this.aktion(this.svc.complete(t.id), t.id, 'Aufgabe als erledigt markiert.');
  }

  wiederOeffnen(t: Task): void {
    this.aktion(this.svc.reopen(t.id), t.id, 'Aufgabe wieder geöffnet.');
  }

  verwerfenFragen(t: Task): void {
    this.verwerfenTask.set(t);
  }

  verwerfenAbbrechen(): void {
    if (!this.verwerfenLaedt()) this.verwerfenTask.set(null);
  }

  verwerfenBestaetigen(): void {
    const t = this.verwerfenTask();
    if (!t) return;
    this.verwerfenLaedt.set(true);
    this.svc.discard(t.id).subscribe({
      next: () => {
        this.verwerfenLaedt.set(false);
        this.verwerfenTask.set(null);
        this.meldung.set({ art: 'erfolg', text: 'Aufgabe verworfen.' });
        this.fetch();
      },
      error: (err) => {
        this.verwerfenLaedt.set(false);
        this.verwerfenTask.set(null);
        this.aktionsFehler(err);
      },
    });
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  private aktion(obs: Observable<Task>, id: string, erfolg: string): void {
    if (this.aktionBusyId()) return;
    this.aktionBusyId.set(id);
    this.meldung.set(null);
    obs.subscribe({
      next: () => {
        this.aktionBusyId.set(null);
        this.meldung.set({ art: 'erfolg', text: erfolg });
        this.fetch();
      },
      error: (err) => {
        this.aktionBusyId.set(null);
        this.aktionsFehler(err);
      },
    });
  }

  private aktionsFehler(err: unknown): void {
    const text = istVerboten(err)
      ? (fehlerDetail(err) ?? 'Keine Berechtigung für diese Aktion.')
      : (fehlerDetail(err) ?? 'Die Aktion ist fehlgeschlagen. Bitte erneut versuchen.');
    this.meldung.set({ art: 'fehler', text });
  }

  // ---- Darstellungshelfer -------------------------------------------------
  statusLabel(s: TaskStatus): string {
    switch (s) {
      case 'OFFEN':
        return 'Offen';
      case 'ERLEDIGT':
        return 'Erledigt';
      case 'VERWORFEN':
        return 'Verworfen';
    }
  }

  statusClass(s: TaskStatus): string {
    if (s === 'ERLEDIGT') return 'stamp--positive';
    if (s === 'VERWORFEN') return 'stamp--warn';
    return '';
  }
}
