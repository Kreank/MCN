import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';
import { AuthService } from '../../core/auth.service';
import { EinsatzService } from '../../core/einsatz.service';
import {
  ASSIGNMENT_ROLES,
  MaterialLogInput,
  ServiceJobDetail,
  ServiceJobStatus,
  TimeLogInput,
  assignmentRoleLabel,
  serviceJobStatusClass,
  serviceJobStatusLabel,
  serviceJobStatusLabelStr,
  timeTypeLabel,
  workOrderStatusLabel,
} from '../../core/einsatz.model';
import { WorkOrderStatus } from '../../core/auftrag.model';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { ReferenzWahl, RefSuche } from '../../shared/formular/referenz-wahl';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import { map } from 'rxjs';
import { deZuApiDezimal, dezimalValidator } from '../../shared/formular/dezimal';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: ServiceJobDetail }
  | VerbotenState
  | { kind: 'error' };

type Meldung = { art: 'erfolg' | 'fehler'; text: string };
type DialogArt = 'termin' | 'status' | 'zeit' | 'material' | 'zuweisung';

const JOB_STATUSES: ServiceJobStatus[] = [
  'UNGEPLANT',
  'GEPLANT',
  'BESTAETIGT',
  'UNTERWEGS',
  'VOR_ORT',
  'PAUSIERT',
  'ABGESCHLOSSEN',
  'NACHARBEIT',
  'AUSGEFALLEN',
];

@Component({
  selector: 'app-einsatz-detail',
  imports: [Mappe, RouterLink, KeinZugriff, ReactiveFormsModule, Dialog, Feld, ReferenzWahl],
  templateUrl: './einsatz-detail.html',
  styleUrl: './einsatz-detail.scss',
})
export class EinsatzDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(EinsatzService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  /** Disposition (Termin/Status): AENDERN mit Scope ALLE. */
  protected readonly darfDispo = computed(() => {
    const u = this.auth.user();
    return (
      u?.permissions.some(
        (p) => p.module === 'workflow' && p.action === 'AENDERN' && p.row_scope === 'ALLE',
      ) ?? false
    );
  });
  /** Erfassung (Zeit/Material): AENDERN in beliebigem Scope (auch Monteur). */
  protected readonly darfErfassen = computed(() => this.auth.darf('workflow', 'AENDERN'));

  protected readonly tab = signal('uebersicht');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  protected readonly tabs: MappeTab[] = [
    { id: 'uebersicht', label: 'Übersicht' },
    { id: 'zuweisungen', label: 'Zuweisungen' },
    { id: 'erfassung', label: 'Zeiten & Material' },
    { id: 'verlauf', label: 'Verlauf' },
  ];

  private readonly dateFmt = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  // --- Schreibaktionen -----------------------------------------------------
  protected readonly meldung = signal<Meldung | null>(null);
  protected readonly dialogOffen = signal<DialogArt | null>(null);
  protected readonly dialogLaedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);

  protected readonly rollen: FeldOption[] = ASSIGNMENT_ROLES;

  /** Benutzersuche (aktive app_user) für die Einsatz-Zuweisung. */
  protected readonly benutzerSuche: RefSuche = (q) =>
    this.svc.listUsers(q).pipe(
      map((users) => users.map((u) => ({ id: u.id, label: u.display_name }))),
    );

  protected readonly zeitarten: FeldOption[] = [
    { wert: 'ARBEITSZEIT', label: 'Arbeitszeit' },
    { wert: 'FAHRTZEIT', label: 'Fahrtzeit' },
    { wert: 'PAUSE', label: 'Pause' },
    { wert: 'BEREITSCHAFT', label: 'Bereitschaft' },
    { wert: 'NACHARBEIT', label: 'Nacharbeit' },
    { wert: 'INTERNE_ZEIT', label: 'Interne Zeit' },
  ];

  protected readonly statusOptionen = computed<FeldOption[]>(() => {
    const cur = this.daten()?.status;
    return JOB_STATUSES.filter((s) => s !== cur).map((s) => ({
      wert: s,
      label: serviceJobStatusLabel(s),
    }));
  });

  protected readonly terminForm = this.fb.group({
    scheduled_start: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    scheduled_end: this.fb.control('', { nonNullable: true }),
  });
  protected readonly statusForm = this.fb.group({
    to_status: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    reason: this.fb.control('', { nonNullable: true }),
  });
  protected readonly zeitForm = this.fb.group({
    time_type: this.fb.control('ARBEITSZEIT', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    started_at: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    ended_at: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    note: this.fb.control('', { nonNullable: true }),
  });
  protected readonly materialForm = this.fb.group({
    description: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    quantity: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, dezimalValidator],
    }),
    unit: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    note: this.fb.control('', { nonNullable: true }),
  });
  protected readonly zuweisungForm = this.fb.group({
    assignee_user_id: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    role: this.fb.control('TECHNICIAN', { nonNullable: true, validators: [Validators.required] }),
  });

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.tab.set('uebersicht');
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
    const d = this.daten();
    this.formularMeldung.set(null);
    switch (art) {
      case 'termin':
        this.terminForm.reset({
          scheduled_start: this.zuLocal(d?.scheduled_start ?? null),
          scheduled_end: this.zuLocal(d?.scheduled_end ?? null),
        });
        break;
      case 'status':
        this.statusForm.reset({ to_status: '', reason: '' });
        break;
      case 'zeit':
        this.zeitForm.reset({ time_type: 'ARBEITSZEIT', started_at: '', ended_at: '', note: '' });
        break;
      case 'material':
        this.materialForm.reset({ description: '', quantity: '', unit: '', note: '' });
        break;
      case 'zuweisung':
        this.zuweisungForm.reset({ assignee_user_id: '', role: 'TECHNICIAN' });
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

  private nachSchreiben(text: string): void {
    const id = this.daten()?.id;
    this.dialogLaedt.set(false);
    this.dialogOffen.set(null);
    this.meldung.set({ art: 'erfolg', text });
    if (id) this.load(id);
  }

  terminAbsenden(): void {
    const d = this.daten();
    if (!d || this.nichtBereit(this.terminForm)) return;
    const v = this.terminForm.getRawValue();
    this.dialogLaedt.set(true);
    this.svc
      .setSchedule(d.id, { scheduled_start: v.scheduled_start, scheduled_end: v.scheduled_end || null })
      .subscribe({
        next: () => this.nachSchreiben('Termin gesetzt.'),
        error: (err) => {
          this.dialogLaedt.set(false);
          this.formularMeldung.set(apiFehlerZuweisen(err, this.terminForm).formular);
        },
      });
  }

  statusAbsenden(): void {
    const d = this.daten();
    if (!d || this.nichtBereit(this.statusForm)) return;
    const v = this.statusForm.getRawValue();
    this.dialogLaedt.set(true);
    this.svc
      .advanceStatus(d.id, { to_status: v.to_status as ServiceJobStatus, reason: v.reason.trim() || null })
      .subscribe({
        next: () => this.nachSchreiben('Status geändert.'),
        error: (err) => {
          this.dialogLaedt.set(false);
          this.formularMeldung.set(apiFehlerZuweisen(err, this.statusForm).formular);
        },
      });
  }

  zeitAbsenden(): void {
    const d = this.daten();
    if (!d || this.nichtBereit(this.zeitForm)) return;
    const v = this.zeitForm.getRawValue();
    const payload: TimeLogInput = {
      time_type: v.time_type,
      started_at: v.started_at,
      ended_at: v.ended_at,
      note: v.note.trim() || null,
    };
    this.dialogLaedt.set(true);
    this.svc.logTime(d.id, payload).subscribe({
      next: () => this.nachSchreiben('Zeit gebucht.'),
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.zeitForm).formular);
      },
    });
  }

  materialAbsenden(): void {
    const d = this.daten();
    if (!d || this.nichtBereit(this.materialForm)) return;
    const v = this.materialForm.getRawValue();
    const payload: MaterialLogInput = {
      description: v.description.trim(),
      quantity: deZuApiDezimal(v.quantity),
      unit: v.unit.trim(),
      note: v.note.trim() || null,
    };
    this.dialogLaedt.set(true);
    this.svc.logMaterial(d.id, payload).subscribe({
      next: () => this.nachSchreiben('Material gebucht.'),
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.materialForm).formular);
      },
    });
  }

  zuweisungAbsenden(): void {
    const d = this.daten();
    if (!d || this.nichtBereit(this.zuweisungForm)) return;
    const v = this.zuweisungForm.getRawValue();
    this.dialogLaedt.set(true);
    this.svc.assign(d.id, { assignee_user_id: v.assignee_user_id, role: v.role }).subscribe({
      next: () => this.nachSchreiben('Mitarbeiter zugewiesen.'),
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.zuweisungForm).formular);
      },
    });
  }

  /** ISO-Datetime → Wert für <input type="datetime-local"> (lokale Zeit). */
  private zuLocal(iso: string | null): string {
    if (!iso) return '';
    const dt = new Date(iso);
    if (Number.isNaN(dt.getTime())) return '';
    const p = (n: number) => `${n}`.padStart(2, '0');
    return `${dt.getFullYear()}-${p(dt.getMonth() + 1)}-${p(dt.getDate())}T${p(dt.getHours())}:${p(dt.getMinutes())}`;
  }

  // ---- Darstellungshelfer -------------------------------------------------
  statusLabel(s: ServiceJobStatus): string {
    return serviceJobStatusLabel(s);
  }
  statusClass(s: ServiceJobStatus): string {
    return serviceJobStatusClass(s);
  }
  statusLabelStr(s: string | null): string {
    return serviceJobStatusLabelStr(s);
  }
  orderStatusLabel(s: WorkOrderStatus): string {
    return workOrderStatusLabel(s);
  }
  timeTypeLabel(t: string): string {
    return timeTypeLabel(t);
  }
  roleLabel(r: string): string {
    return assignmentRoleLabel(r);
  }
  dt(iso: string | null): string {
    if (!iso) return '—';
    return this.dateFmt.format(new Date(iso));
  }
}
