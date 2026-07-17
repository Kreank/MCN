import { Component, computed, effect, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { map } from 'rxjs';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { ProjektService } from '../../core/projekt.service';
import { AufgabeService } from '../../core/aufgabe.service';
import { AuftragService } from '../../core/auftrag.service';
import { PartyService } from '../../core/party.service';
import { AuthService } from '../../core/auth.service';
import { Task, TaskStatus } from '../../core/aufgabe.model';
import {
  WorkOrder,
  WorkOrderCreate,
  WorkOrderStatus,
  workOrderStatusClass,
  workOrderStatusLabel,
} from '../../core/auftrag.model';
import {
  CasePriority,
  Checklist,
  ChecklistCreate,
  LogCategory,
  LogEntry,
  LogEntryCreate,
  ProjectDetail,
  ProjectStatus,
  ServiceCaseCreate,
  ServiceCaseStatus,
} from '../../core/projekt.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { Dateien } from '../../shared/dateien/dateien';
import { Belege, BelegKontext } from '../../shared/belege/belege';
import { ZielFilter } from '../../core/datei.model';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { ReferenzWahl, RefSuche } from '../../shared/formular/referenz-wahl';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';

type Meldung = { art: 'erfolg' | 'fehler'; text: string };
type DialogArt = 'log' | 'checkliste' | 'vorgang' | 'auftrag';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: ProjectDetail }
  | VerbotenState
  | { kind: 'error' };

type TasksState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; items: Task[] }
  | VerbotenState
  | { kind: 'error' };

type LazyState<T> =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; items: T[] }
  | VerbotenState
  | { kind: 'error' };

@Component({
  selector: 'app-projekt-detail',
  imports: [Mappe, RouterLink, KeinZugriff, Dateien, Belege, ReactiveFormsModule, Dialog, Feld, ReferenzWahl],
  templateUrl: './projekt-detail.html',
  styleUrl: './projekt-detail.scss',
})
export class ProjektDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(ProjektService);
  private readonly aufgabeSvc = inject(AufgabeService);
  private readonly auftragSvc = inject(AuftragService);
  private readonly partySvc = inject(PartyService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  /**
   * `darfAlle`, nicht `darf`: Die vier Aktionen dieser Mappe (Vorgang, Auftrag,
   * Logeintrag, Checkliste) laufen ausnahmslos über fail-closed-Endpunkte
   * (`permissions.require` in `projekt.py` / `auftrag.py`). Ein Konto mit
   * row_scope EIGENE bekommt dort 403 — der Monteur trägt `workflow/ANLEGEN`
   * und `workflow/AENDERN` nur für seine eigenen Zeilen (Aufgaben, Berichte,
   * Zeit-/Materialbuchung). Er liest die Projektmappe, er bespielt sie nicht.
   */
  protected readonly darfAnlegen = computed(() => this.auth.darfAlle('workflow', 'ANLEGEN'));
  protected readonly darfAendern = computed(() => this.auth.darfAlle('workflow', 'AENDERN'));

  protected readonly tab = signal('uebersicht');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly tasksState = signal<TasksState>({ kind: 'idle' });
  protected readonly ordersState = signal<LazyState<WorkOrder>>({ kind: 'idle' });
  protected readonly logState = signal<LazyState<LogEntry>>({ kind: 'idle' });
  protected readonly checklistsState = signal<LazyState<Checklist>>({ kind: 'idle' });
  private reqId = 0;

  protected readonly tabs: MappeTab[] = [
    { id: 'uebersicht', label: 'Übersicht' },
    { id: 'liegenschaften', label: 'Liegenschaften' },
    { id: 'vorgaenge', label: 'Vorgänge' },
    { id: 'auftraege', label: 'Aufträge' },
    { id: 'aufgaben', label: 'Aufgaben' },
    { id: 'logbuch', label: 'Logbuch' },
    { id: 'checklisten', label: 'Checklisten' },
    { id: 'dokumente', label: 'Dokumente' },
    { id: 'dateien', label: 'Dateien' },
  ];

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  /** Stabile Zielreferenz fuer den Dateien-Tab (nur bei Projektwechsel neu). */
  protected readonly dateienZiel = computed<ZielFilter>(() => ({
    project_id: this.daten()?.id ?? '',
  }));

  /** Stabiler Beleg-Kontext für den Dokumente-Tab (Belege dieses Projekts). */
  protected readonly belegKontext = computed<BelegKontext>(() => ({
    project_id: this.daten()?.id ?? '',
  }));

  // --- Schreibaktionen (Dialoge) ------------------------------------------
  protected readonly meldung = signal<Meldung | null>(null);
  protected readonly dialogOffen = signal<DialogArt | null>(null);
  protected readonly dialogLaedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);

  protected readonly kategorien: FeldOption[] = [
    { wert: 'NOTIZ', label: 'Notiz' },
    { wert: 'ANRUF', label: 'Anruf' },
    { wert: 'ABSPRACHE', label: 'Absprache' },
    { wert: 'ENTSCHEIDUNG', label: 'Entscheidung' },
  ];
  protected readonly prioritaeten: FeldOption[] = [
    { wert: 'NORMAL', label: 'Normal' },
    { wert: 'DRINGEND', label: 'Dringend' },
    { wert: 'NOTFALL', label: 'Notfall' },
  ];

  /** Liegenschaften des Projekts als Select-Optionen (für Vorgang/Auftrag). */
  protected readonly liegenschaftOptionen = computed<FeldOption[]>(() => {
    const d = this.daten();
    if (!d) return [];
    return d.properties.map((o) => ({ wert: o.id, label: `${o.name} · ${o.city}` }));
  });

  /** Kontaktsuche für den optionalen Melder eines Vorgangs. */
  protected readonly partySuche: RefSuche = (q) =>
    this.partySvc.list({ page: 1, page_size: 20, q }).pipe(
      map((p) => p.items.map((x) => ({ id: x.id, label: x.display_name }))),
    );

  // Freies Notizfeld (Projekte-7): Inline-Editor in der Übersicht, getrennt vom
  // Logbuch. Wird bei jedem Projekt-Load frisch aus internal_note befüllt.
  protected readonly notizForm = this.fb.group({
    internal_note: this.fb.control('', { nonNullable: true }),
  });
  protected readonly notizLaedt = signal(false);
  /** True, sobald der Textstand vom gespeicherten internal_note abweicht. */
  protected readonly notizGeaendert = computed(() => {
    const d = this.daten();
    if (!d) return false;
    return this.notizEntwurf() !== (d.internal_note ?? '');
  });
  // Reaktiver Spiegel des Formularwerts (für notizGeaendert), gepflegt über valueChanges.
  private readonly notizEntwurf = signal('');

  protected readonly logForm = this.fb.group({
    entry: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    category: this.fb.control('NOTIZ', { nonNullable: true, validators: [Validators.required] }),
  });
  protected readonly checklistForm = this.fb.group({
    name: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(200)],
    }),
    items: this.fb.control('', { nonNullable: true }),
  });
  protected readonly vorgangForm = this.fb.group({
    property_id: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    subject: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    priority: this.fb.control('NORMAL', { nonNullable: true, validators: [Validators.required] }),
    description: this.fb.control('', { nonNullable: true }),
    reported_by_party_id: this.fb.control('', { nonNullable: true }),
  });
  protected readonly auftragForm = this.fb.group({
    property_id: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    title: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(200)],
    }),
    priority: this.fb.control('NORMAL', { nonNullable: true, validators: [Validators.required] }),
    desired_date: this.fb.control('', { nonNullable: true }),
    customer_reference: this.fb.control('', { nonNullable: true }),
    description: this.fb.control('', { nonNullable: true }),
    is_emergency: this.fb.control(false, { nonNullable: true }),
  });

  constructor() {
    this.notizForm.controls.internal_note.valueChanges
      .pipe(takeUntilDestroyed())
      .subscribe((v) => this.notizEntwurf.set(v));

    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.tab.set('uebersicht');
      this.tasksState.set({ kind: 'idle' });
      this.ordersState.set({ kind: 'idle' });
      this.logState.set({ kind: 'idle' });
      this.checklistsState.set({ kind: 'idle' });
      if (!id) {
        this.state.set({ kind: 'error' });
        return;
      }
      this.load(id);
    });

    // Cockpit-Tabs (Aufgaben/Logbuch/Checklisten) erst beim Öffnen laden (lazy).
    effect(() => {
      const d = this.daten();
      if (!d) return;
      const t = this.tab();
      if (t === 'aufgaben' && this.tasksState().kind === 'idle') this.loadTasks(d.id);
      if (t === 'auftraege' && this.ordersState().kind === 'idle') this.loadOrders(d.id);
      if (t === 'logbuch' && this.logState().kind === 'idle') this.loadLog(d.id);
      if (t === 'checklisten' && this.checklistsState().kind === 'idle') {
        this.loadChecklists(d.id);
      }
    });
  }

  private loadTasks(projectId: string): void {
    this.tasksState.set({ kind: 'loading' });
    this.aufgabeSvc.list({ page: 1, page_size: 50, project_id: projectId }).subscribe({
      next: (d) => this.tasksState.set({ kind: 'ready', items: d.items }),
      error: (err) => this.tasksState.set(fehlerState(err)),
    });
  }

  private loadOrders(projectId: string): void {
    this.ordersState.set({ kind: 'loading' });
    this.auftragSvc.list({ page: 1, page_size: 50, project_id: projectId }).subscribe({
      next: (d) => this.ordersState.set({ kind: 'ready', items: d.items }),
      error: (err) => this.ordersState.set(fehlerState(err)),
    });
  }

  private loadLog(projectId: string): void {
    this.logState.set({ kind: 'loading' });
    this.svc.getProjectLog(projectId).subscribe({
      next: (items) => this.logState.set({ kind: 'ready', items }),
      error: (err) => this.logState.set(fehlerState(err)),
    });
  }

  private loadChecklists(projectId: string): void {
    this.checklistsState.set({ kind: 'loading' });
    this.svc.getChecklists(projectId).subscribe({
      next: (items) => this.checklistsState.set({ kind: 'ready', items }),
      error: (err) => this.checklistsState.set(fehlerState(err)),
    });
  }

  // --- Dialoge öffnen/schließen -------------------------------------------
  dialogOeffnen(art: DialogArt): void {
    this.formularMeldung.set(null);
    switch (art) {
      case 'log':
        this.logForm.reset({ entry: '', category: 'NOTIZ' });
        break;
      case 'checkliste':
        this.checklistForm.reset({ name: '', items: '' });
        break;
      case 'vorgang':
        this.vorgangForm.reset({
          property_id: '',
          subject: '',
          priority: 'NORMAL',
          description: '',
          reported_by_party_id: '',
        });
        break;
      case 'auftrag':
        this.auftragForm.reset({
          property_id: '',
          title: '',
          priority: 'NORMAL',
          desired_date: '',
          customer_reference: '',
          description: '',
          is_emergency: false,
        });
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

  private nichtBereit(form: Parameters<typeof serverFehlerZuruecksetzen>[0]): boolean {
    if (this.dialogLaedt()) return true;
    serverFehlerZuruecksetzen(form);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(form);
    return form.invalid;
  }

  // --- Freies Notizfeld (Projekte-7) --------------------------------------
  notizSpeichern(): void {
    const d = this.daten();
    if (!d || this.notizLaedt() || !this.notizGeaendert()) return;
    const roh = this.notizForm.getRawValue().internal_note.trim();
    this.notizLaedt.set(true);
    this.svc.setInternalNote(d.id, { internal_note: roh || null }).subscribe({
      next: (data) => {
        this.notizLaedt.set(false);
        this.state.set({ kind: 'ready', data });
        this.notizForm.reset({ internal_note: data.internal_note ?? '' });
        this.meldung.set({ art: 'erfolg', text: 'Notiz gespeichert.' });
      },
      error: (err) => {
        this.notizLaedt.set(false);
        this.meldung.set({
          art: 'fehler',
          text: apiFehlerZuweisen(err, this.notizForm).formular ?? 'Notiz konnte nicht gespeichert werden.',
        });
      },
    });
  }

  // --- Logbuch-Eintrag -----------------------------------------------------
  logAbsenden(): void {
    const d = this.daten();
    if (!d || this.nichtBereit(this.logForm)) return;
    const v = this.logForm.getRawValue();
    const payload: LogEntryCreate = { entry: v.entry.trim(), category: v.category as LogCategory };
    this.dialogLaedt.set(true);
    this.svc.addLog(d.id, payload).subscribe({
      next: () => {
        this.dialogLaedt.set(false);
        this.dialogOffen.set(null);
        this.meldung.set({ art: 'erfolg', text: 'Logbuch-Eintrag hinzugefügt.' });
        this.loadLog(d.id);
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.logForm).formular);
      },
    });
  }

  // --- Checkliste ----------------------------------------------------------
  checklistAbsenden(): void {
    const d = this.daten();
    if (!d || this.nichtBereit(this.checklistForm)) return;
    const v = this.checklistForm.getRawValue();
    const items = v.items
      .split('\n')
      .map((z) => z.trim())
      .filter((z) => z.length > 0);
    const payload: ChecklistCreate = { name: v.name.trim(), items };
    this.dialogLaedt.set(true);
    this.svc.createChecklist(d.id, payload).subscribe({
      next: () => {
        this.dialogLaedt.set(false);
        this.dialogOffen.set(null);
        this.meldung.set({ art: 'erfolg', text: `Checkliste „${payload.name}“ angelegt.` });
        this.loadChecklists(d.id);
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.checklistForm).formular);
      },
    });
  }

  // --- Vorgang -------------------------------------------------------------
  vorgangAbsenden(): void {
    const d = this.daten();
    if (!d || this.nichtBereit(this.vorgangForm)) return;
    const v = this.vorgangForm.getRawValue();
    const payload: ServiceCaseCreate = {
      property_id: v.property_id,
      subject: v.subject.trim(),
      priority: v.priority as CasePriority,
      description: v.description.trim() || null,
      reported_by_party_id: v.reported_by_party_id || null,
    };
    this.dialogLaedt.set(true);
    this.svc.createServiceCase(d.id, payload).subscribe({
      next: () => {
        this.dialogLaedt.set(false);
        this.dialogOffen.set(null);
        this.meldung.set({ art: 'erfolg', text: 'Vorgang angelegt.' });
        this.load(d.id); // Projektdetail (service_cases) neu laden
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.vorgangForm).formular);
      },
    });
  }

  // --- Auftrag -------------------------------------------------------------
  auftragAbsenden(): void {
    const d = this.daten();
    if (!d || this.nichtBereit(this.auftragForm)) return;
    const v = this.auftragForm.getRawValue();
    const payload: WorkOrderCreate = {
      property_id: v.property_id,
      title: v.title.trim(),
      project_id: d.id,
      priority: v.priority as WorkOrder['priority'],
      desired_date: v.desired_date || null,
      customer_reference: v.customer_reference.trim() || null,
      description: v.description.trim() || null,
      is_emergency: v.is_emergency,
    };
    this.dialogLaedt.set(true);
    this.auftragSvc.create(payload).subscribe({
      next: () => {
        this.dialogLaedt.set(false);
        this.dialogOffen.set(null);
        this.meldung.set({ art: 'erfolg', text: `Auftrag „${payload.title}“ angelegt.` });
        this.loadOrders(d.id);
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.auftragForm).formular);
      },
    });
  }

  logCategoryLabel(c: LogCategory): string {
    const map: Record<LogCategory, string> = {
      NOTIZ: 'Notiz',
      ANRUF: 'Anruf',
      ABSPRACHE: 'Absprache',
      ENTSCHEIDUNG: 'Entscheidung',
      SYSTEM: 'System',
    };
    return map[c] ?? c;
  }

  // ---- Kontaktkarte -------------------------------------------------------
  kontaktMonogram(name: string): string {
    const parts = name.trim().split(/\s+/).filter(Boolean);
    if (parts.length === 0) return '–';
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }

  rolleLabel(role: string): string {
    const map: Record<string, string> = {
      PROPERTY_OWNER: 'Eigentümer',
      COMMUNITY_OF_OWNERS: 'Eigentümergemeinschaft',
      OPERATOR: 'Betreiber',
      CARETAKER: 'Hausmeister',
    };
    return map[role] ?? role;
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
        if (rid === this.reqId) {
          this.state.set({ kind: 'ready', data });
          this.notizForm.reset({ internal_note: data.internal_note ?? '' });
        }
      },
      error: (err) => {
        if (rid === this.reqId) this.state.set(fehlerState(err));
      },
    });
  }

  // ---- Darstellungshelfer -------------------------------------------------
  statusLabel(s: ProjectStatus): string {
    return s === 'OPEN' ? 'Offen' : 'Geschlossen';
  }
  statusClass(s: ProjectStatus): string {
    return s === 'OPEN' ? 'stamp--positive' : '';
  }

  caseStatusLabel(s: ServiceCaseStatus): string {
    const map: Record<ServiceCaseStatus, string> = {
      NEU: 'Neu',
      IN_PRUEFUNG: 'In Prüfung',
      RUECKFRAGE: 'Rückfrage',
      FREIGABE_AUSSTEHEND: 'Freigabe ausstehend',
      BEAUFTRAGT: 'Beauftragt',
      ABGESCHLOSSEN: 'Abgeschlossen',
      ABGELEHNT: 'Abgelehnt',
    };
    return map[s] ?? s;
  }
  caseStatusClass(s: ServiceCaseStatus): string {
    if (s === 'ABGESCHLOSSEN') return 'stamp--positive';
    if (s === 'ABGELEHNT') return 'stamp--warn';
    return '';
  }

  priorityLabel(p: CasePriority): string {
    const map: Record<CasePriority, string> = {
      NORMAL: 'Normal',
      DRINGEND: 'Dringend',
      NOTFALL: 'Notfall',
    };
    return map[p] ?? p;
  }
  priorityClass(p: CasePriority): string {
    return p === 'NORMAL' ? '' : 'stamp--warn';
  }

  taskStatusLabel(s: TaskStatus): string {
    switch (s) {
      case 'OFFEN':
        return 'Offen';
      case 'ERLEDIGT':
        return 'Erledigt';
      case 'VERWORFEN':
        return 'Verworfen';
    }
  }
  taskStatusClass(s: TaskStatus): string {
    if (s === 'ERLEDIGT') return 'stamp--positive';
    if (s === 'VERWORFEN') return 'stamp--warn';
    return '';
  }

  orderStatusLabel(s: WorkOrderStatus): string {
    return workOrderStatusLabel(s);
  }
  orderStatusClass(s: WorkOrderStatus): string {
    return workOrderStatusClass(s);
  }
}
