import { Component, ElementRef, computed, inject, signal, viewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Observable, Subject, debounceTime, distinctUntilChanged, map } from 'rxjs';
import { AufgabeService } from '../../core/aufgabe.service';
import { ProjektService } from '../../core/projekt.service';
import { PartyService } from '../../core/party.service';
import { AuthService } from '../../core/auth.service';
import { Task, TaskCreate, TaskPage, TaskStatus, TaskUpdate } from '../../core/aufgabe.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerDetail, fehlerState, istVerboten } from '../../shared/http-fehler';
import { Dialog } from '../../shared/dialog/dialog';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { Feld } from '../../shared/formular/feld';
import { ReferenzWahl, RefSuche } from '../../shared/formular/referenz-wahl';
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
  imports: [RouterLink, ReactiveFormsModule, KeinZugriff, Dialog, Bestaetigung, Feld, ReferenzWahl],
  templateUrl: './aufgaben.html',
  styleUrl: './aufgaben.scss',
})
export class Aufgaben {
  private readonly svc = inject(AufgabeService);
  private readonly projektSvc = inject(ProjektService);
  private readonly partySvc = inject(PartyService);
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
  /**
   * Darf der Akteur eine Aufgabe einer ANDEREN Person zuweisen? Nur bei
   * row_scope 'ALLE' im Modul workflow. Wer nur 'EIGENE' hat (Monteur), sieht
   * die Zuweisungs-Auswahl gar nicht — der Server erzwingt ihn ohnehin als
   * Eigentümer. Reine Kosmetik; die Wahrheit liegt beim Server (403).
   */
  protected readonly darfZuweisen = computed(() => this.workflowScope('LESEN') === 'ALLE');

  private workflowScope(action: string): string | null {
    const u = this.auth.user();
    const p = u?.permissions.find((x) => x.module === 'workflow' && x.action === action);
    return p?.row_scope ?? null;
  }

  // --- Referenz-Suchen (Serversuche für die Combobox) ---------------------
  protected readonly projektSuche: RefSuche = (q) =>
    this.projektSvc.list({ page: 1, page_size: 20, q }).pipe(
      map((p) => p.items.map((o) => ({ id: o.id, label: o.name, sub: o.project_number }))),
    );
  protected readonly parteiSuche: RefSuche = (q) =>
    this.partySvc.list({ page: 1, page_size: 20, q }).pipe(
      map((p) => p.items.map((o) => ({ id: o.id, label: o.display_name }))),
    );
  protected readonly nutzerSuche: RefSuche = (q) =>
    this.svc.listAssignableUsers(q).pipe(
      map((users) => users.map((u) => ({ id: u.id, label: u.display_name }))),
    );

  // --- Anlage-/Bearbeiten-Dialog ------------------------------------------
  /** null = zu, 'neu' = anlegen, sonst die ID der bearbeiteten Aufgabe. */
  protected readonly dialogModus = signal<'neu' | string | null>(null);
  /** Die gerade bearbeitete Aufgabe (für die Anzeige der aktuellen Bezüge). */
  protected readonly bearbeitenTask = signal<Task | null>(null);
  protected readonly dialogLaedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);
  /** aria-live-Bestätigung nach „Speichern und neu" (Dialog bleibt offen). */
  protected readonly neuNochmalMeldung = signal<string | null>(null);
  protected readonly form = this.fb.group({
    title: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(200)],
    }),
    description: this.fb.control('', { nonNullable: true }),
    due_date: this.fb.control('', { nonNullable: true }),
    assigned_to_user_id: this.fb.control('', { nonNullable: true }),
    project_id: this.fb.control('', { nonNullable: true }),
    party_id: this.fb.control('', { nonNullable: true }),
  });

  private readonly dialogForm = viewChild<ElementRef<HTMLElement>>('dialogForm');

  protected readonly istBearbeiten = computed(() => {
    const m = this.dialogModus();
    return m !== null && m !== 'neu';
  });
  protected readonly dialogTitel = computed(() =>
    this.istBearbeiten() ? 'Aufgabe bearbeiten' : 'Neue Aufgabe',
  );

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

  // ---- Dialog öffnen/schließen -------------------------------------------
  private formLeeren(): void {
    this.form.reset({
      title: '',
      description: '',
      due_date: '',
      assigned_to_user_id: '',
      project_id: '',
      party_id: '',
    });
  }

  neuOeffnen(): void {
    this.formLeeren();
    this.formularMeldung.set(null);
    this.neuNochmalMeldung.set(null);
    this.bearbeitenTask.set(null);
    this.dialogModus.set('neu');
    this.titelFokus();
  }

  bearbeitenOeffnen(t: Task): void {
    // Nur die direkt sichtbaren Felder werden vorbelegt. Die Referenz-Comboboxen
    // bleiben LEER: eine leere Combobox = „nicht ändern" (das Feld wird dann gar
    // nicht mitgeschickt, der Server behält den Bezug via exclude_unset). Der
    // aktuelle Bezug wird als Kontextzeile angezeigt; eine Auswahl ersetzt ihn.
    this.form.reset({
      title: t.title,
      description: t.description ?? '',
      due_date: t.due_date ?? '',
      assigned_to_user_id: '',
      project_id: '',
      party_id: '',
    });
    this.formularMeldung.set(null);
    this.neuNochmalMeldung.set(null);
    this.bearbeitenTask.set(t);
    this.dialogModus.set(t.id);
  }

  dialogSchliessen(): void {
    if (this.dialogLaedt()) return;
    this.dialogModus.set(null);
    this.bearbeitenTask.set(null);
  }

  private titelFokus(): void {
    queueMicrotask(() => {
      const feld = this.dialogForm()?.nativeElement.querySelector<HTMLInputElement>('input');
      feld?.focus();
    });
  }

  /** Anlage-Payload: leere optionale Felder werden zu null (= kein Bezug). */
  private createPayload(): TaskCreate {
    const v = this.form.getRawValue();
    const payload: TaskCreate = {
      title: v.title.trim(),
      description: v.description.trim() || null,
      due_date: v.due_date || null,
      project_id: v.project_id || null,
      party_id: v.party_id || null,
    };
    // Zuweisung nur mitschicken, wenn der Akteur fremd zuweisen darf. Sonst
    // erzwingt der Server ohnehin den Akteur als Eigentümer.
    if (this.darfZuweisen()) {
      payload.assigned_to_user_id = v.assigned_to_user_id || null;
    }
    return payload;
  }

  /**
   * Bearbeiten-Payload: die sichtbaren Felder (Titel/Beschreibung/Fälligkeit)
   * werden immer geschickt. Referenzen NUR, wenn in der Combobox etwas gewählt
   * wurde — eine leere Combobox lässt den bestehenden Bezug unangetastet
   * (Server: exclude_unset). So ersetzt eine Auswahl den Bezug, ohne dass ein
   * versehentlich leeres Feld ihn löscht.
   */
  private updatePayload(): TaskUpdate {
    const v = this.form.getRawValue();
    const payload: TaskUpdate = {
      title: v.title.trim(),
      description: v.description.trim() || null,
      due_date: v.due_date || null,
    };
    if (v.project_id) payload.project_id = v.project_id;
    if (v.party_id) payload.party_id = v.party_id;
    if (this.darfZuweisen() && v.assigned_to_user_id) {
      payload.assigned_to_user_id = v.assigned_to_user_id;
    }
    return payload;
  }

  private formGueltig(): boolean {
    serverFehlerZuruecksetzen(this.form);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.form);
    return this.form.valid;
  }

  // ---- Anlegen ------------------------------------------------------------
  /** Speichert und schließt den Dialog. */
  neuAbsenden(): void {
    this.anlegen(false);
  }

  /** Speichert und lässt den Dialog für die nächste Aufgabe offen. */
  neuUndWeiter(): void {
    this.anlegen(true);
  }

  private anlegen(undWeiter: boolean): void {
    if (this.dialogLaedt() || !this.formGueltig()) return;
    const payload = this.createPayload();

    this.dialogLaedt.set(true);
    this.svc.create(payload).subscribe({
      next: () => {
        this.dialogLaedt.set(false);
        this.page.set(1);
        this.fetch();
        if (undWeiter) {
          this.formLeeren();
          this.neuNochmalMeldung.set(`Aufgabe „${payload.title}“ angelegt. Nächste erfassen.`);
          this.titelFokus();
        } else {
          this.dialogModus.set(null);
          this.meldung.set({ art: 'erfolg', text: `Aufgabe „${payload.title}“ wurde angelegt.` });
        }
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.form).formular);
      },
    });
  }

  // ---- Bearbeiten ---------------------------------------------------------
  bearbeitenAbsenden(): void {
    const id = this.dialogModus();
    if (id === null || id === 'neu' || this.dialogLaedt() || !this.formGueltig()) return;
    const payload = this.updatePayload();

    this.dialogLaedt.set(true);
    this.svc.update(id, payload).subscribe({
      next: () => {
        this.dialogLaedt.set(false);
        this.dialogModus.set(null);
        this.bearbeitenTask.set(null);
        this.meldung.set({ art: 'erfolg', text: `Aufgabe „${payload.title}“ wurde gespeichert.` });
        this.fetch();
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.form).formular);
      },
    });
  }

  dialogAbsenden(): void {
    if (this.istBearbeiten()) this.bearbeitenAbsenden();
    else this.neuAbsenden();
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
