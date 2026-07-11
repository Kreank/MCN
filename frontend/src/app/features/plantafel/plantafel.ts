import { Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { map, of, switchMap } from 'rxjs';
import { EinsatzService } from '../../core/einsatz.service';
import {
  BoardJob,
  Plantafel as PlantafelData,
  ServiceJobStatus,
  categoryColorClass,
  resourceTypeLabel,
  serviceJobStatusLabel,
} from '../../core/einsatz.model';
import { AuftragService } from '../../core/auftrag.service';
import { AuthService } from '../../core/auth.service';
import { PlanungNav } from '../planung-nav/planung-nav';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld } from '../../shared/formular/feld';
import { ReferenzWahl, RefSuche } from '../../shared/formular/referenz-wahl';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: PlantafelData }
  | VerbotenState
  | { kind: 'error' };

/** Bahn: Mitarbeiter, Betriebsmittel-Ressource oder Sammelbahn ohne Zuweisung. */
type Lane = {
  id: string;
  name: string;
  kind: 'user' | 'resource' | 'unassigned';
  sub?: string;
};

const WINDOW_DAYS = 7;

// Statusfarbe der Kacheln (nie nur Farbe — die Kachel trägt zusätzlich das
// Status-Label als Text).
const STATUS_MOD: Record<ServiceJobStatus, string> = {
  UNGEPLANT: 'neutral',
  GEPLANT: 'plan',
  BESTAETIGT: 'plan',
  UNTERWEGS: 'aktiv',
  VOR_ORT: 'aktiv',
  PAUSIERT: 'pause',
  ABGESCHLOSSEN: 'fertig',
  NACHARBEIT: 'nach',
  AUSGEFALLEN: 'aus',
};

function todayIso(): string {
  const d = new Date();
  const m = `${d.getMonth() + 1}`.padStart(2, '0');
  const day = `${d.getDate()}`.padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}

function addDaysIso(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** Kalendertag eines Zeitstempels in LOKALER Zeit (konsistent mit der lokalen
 * Zeitanzeige und dem lokalen „Heute"). */
function localDayIso(iso: string): string {
  const d = new Date(iso);
  const m = `${d.getMonth() + 1}`.padStart(2, '0');
  const day = `${d.getDate()}`.padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}

@Component({
  selector: 'app-plantafel',
  imports: [
    RouterLink, PlanungNav, KeinZugriff, ReactiveFormsModule, Dialog, Feld, ReferenzWahl,
  ],
  templateUrl: './plantafel.html',
  styleUrl: './plantafel.scss',
})
export class Plantafel {
  private readonly svc = inject(EinsatzService);
  private readonly auftragSvc = inject(AuftragService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  protected readonly windowDays = WINDOW_DAYS;
  protected readonly rangeStart = signal(todayIso());
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  private readonly dowFmt = new Intl.DateTimeFormat('de-DE', { weekday: 'short' });
  private readonly dayFmt = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
  });
  private readonly timeFmt = new Intl.DateTimeFormat('de-DE', {
    hour: '2-digit',
    minute: '2-digit',
  });

  protected readonly days = computed(() => {
    const start = this.rangeStart();
    const today = todayIso();
    return Array.from({ length: WINDOW_DAYS }, (_, i) => {
      const iso = addDaysIso(start, i);
      const d = new Date(`${iso}T00:00:00Z`);
      return {
        iso,
        dow: this.dowFmt.format(d),
        label: this.dayFmt.format(d),
        isToday: iso === today,
      };
    });
  });

  // Bahnen: erst Mitarbeiter (aus Zuweisungen), dann Ressourcen-Bahnen
  // (Betriebsmittel aus resource.job_resource), zuletzt eine „Ohne Zuweisung"-
  // Sammelbahn, falls es verplante, aber niemandem zugeordnete Einsätze gibt.
  protected readonly lanes = computed<Lane[]>(() => {
    const s = this.state();
    if (s.kind !== 'ready') return [];
    const lanes: Lane[] = s.data.resources.map((r) => ({
      id: r.id,
      name: r.display_name,
      kind: 'user' as const,
    }));
    for (const rl of s.data.resource_lanes) {
      lanes.push({
        id: rl.id,
        name: rl.display_name,
        kind: 'resource',
        sub: resourceTypeLabel(rl.resource_type),
      });
    }
    if (s.data.unassigned_count > 0) {
      lanes.push({ id: '', name: 'Ohne Zuweisung', kind: 'unassigned' });
    }
    return lanes;
  });

  protected readonly rangeLabel = computed(() => {
    const s = this.rangeStart();
    const from = new Date(`${s}T00:00:00Z`);
    const to = new Date(`${addDaysIso(s, WINDOW_DAYS - 1)}T00:00:00Z`);
    return `${this.dayFmt.format(from)} – ${this.dayFmt.format(to)}`;
  });

  protected readonly summary = computed(() => {
    const s = this.state();
    if (s.kind === 'loading') return 'Plantafel wird geladen.';
    if (s.kind === 'forbidden') return 'Keine Berechtigung für die Plantafel.';
    if (s.kind === 'error') return 'Plantafel konnte nicht geladen werden.';
    return `${s.data.jobs.length} Einsätze auf ${this.lanes().length} Bahnen im Zeitraum ${this.rangeLabel()}.`;
  });

  constructor() {
    this.fetch();
  }

  prev(): void {
    this.rangeStart.set(addDaysIso(this.rangeStart(), -WINDOW_DAYS));
    this.fetch();
  }
  next(): void {
    this.rangeStart.set(addDaysIso(this.rangeStart(), WINDOW_DAYS));
    this.fetch();
  }
  today(): void {
    this.rangeStart.set(todayIso());
    this.fetch();
  }
  retry(): void {
    this.fetch();
  }

  /** Einsätze einer Bahn an einem Tag (Mehrfachzuweisung → in jeder Bahn). */
  cellJobs(lane: Lane, dayIso: string): BoardJob[] {
    const s = this.state();
    if (s.kind !== 'ready') return [];
    return s.data.jobs.filter((j) => {
      if (localDayIso(j.scheduled_start) !== dayIso) return false;
      if (lane.kind === 'resource') return j.resource_ids.includes(lane.id);
      if (lane.kind === 'unassigned') {
        return j.assignee_ids.length === 0 && j.resource_ids.length === 0;
      }
      return j.assignee_ids.includes(lane.id);
    });
  }

  // ---- Termin für einen Auftrag setzen (Klick in eine Bahn/Tag-Zelle) -------
  protected readonly darfPlanen = computed(() => this.auth.darf('workflow', 'ANLEGEN'));
  protected readonly neuOffen = signal(false);
  protected readonly neuBusy = signal(false);
  protected readonly neuFehler = signal<string | null>(null);
  /** Angeklickte Zelle: Ziel-Bahn (ggf. Mitarbeiter) + Tag. */
  protected readonly neuCell = signal<{ lane: Lane; dayIso: string; dayLabel: string } | null>(null);

  protected readonly neuForm = this.fb.group({
    work_order_id: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    start: this.fb.control('08:00', { nonNullable: true, validators: [Validators.required] }),
    end: this.fb.control('', { nonNullable: true }),
  });

  /** Auftragssuche (Titel/Nummer) für die Termin-Zuordnung. */
  protected readonly auftragSuche: RefSuche = (q) =>
    this.auftragSvc
      .list({ page: 1, page_size: 20, q })
      .pipe(map((p) => p.items.map((o) => ({ id: o.id, label: o.title, sub: o.order_number }))));

  terminOeffnen(lane: Lane, day: { iso: string; label: string }): void {
    if (!this.darfPlanen()) return;
    this.neuCell.set({ lane, dayIso: day.iso, dayLabel: `${day.label}` });
    this.neuFehler.set(null);
    this.neuForm.reset({ work_order_id: '', start: '08:00', end: '' });
    this.neuOffen.set(true);
  }

  terminSchliessen(): void {
    if (this.neuBusy()) return;
    this.neuOffen.set(false);
  }

  private toIso(dayIso: string, time: string): string {
    // Lokale Zeit (dayIso + HH:MM) → UTC-ISO für den Server (timestamptz).
    return new Date(`${dayIso}T${time}:00`).toISOString();
  }

  terminAnlegen(): void {
    if (this.neuBusy()) return;
    const cell = this.neuCell();
    if (!cell) return;
    this.neuForm.markAllAsTouched();
    if (this.neuForm.invalid) return;
    const v = this.neuForm.getRawValue();
    const start = this.toIso(cell.dayIso, v.start);
    const end = v.end ? this.toIso(cell.dayIso, v.end) : null;
    if (end && end <= start) {
      this.neuFehler.set('Das Ende muss nach dem Beginn liegen.');
      return;
    }
    this.neuBusy.set(true);
    this.neuFehler.set(null);
    // Anlegen (UNGEPLANT mit Zeitraum) → auf GEPLANT setzen → Bahn-Mitarbeiter
    // zuweisen (nur bei Mitarbeiter-Bahnen).
    this.svc
      .create({
        work_order_id: v.work_order_id,
        scheduled_start: start,
        scheduled_end: end,
        appointment_category_id: null,
      })
      .pipe(
        switchMap((job) =>
          this.svc.advanceStatus(job.id, { to_status: 'GEPLANT' }).pipe(map(() => job)),
        ),
        switchMap((job) =>
          cell.lane.kind === 'user'
            ? this.svc
                .assign(job.id, { assignee_user_id: cell.lane.id, role: 'TECHNICIAN' })
                .pipe(map(() => job))
            : of(job),
        ),
      )
      .subscribe({
        next: () => {
          this.neuBusy.set(false);
          this.neuOffen.set(false);
          this.fetch();
        },
        error: (err) => {
          this.neuBusy.set(false);
          this.neuFehler.set(fehlerDetail(err) ?? 'Der Termin konnte nicht angelegt werden.');
        },
      });
  }

  private fetch(): void {
    const id = ++this.reqId;
    this.state.set({ kind: 'loading' });
    // Fenster um je einen Tag geweitet, damit Einsätze an der lokalen
    // Tagesgrenze (UTC-Datum ±1) korrekt in eine sichtbare Bahn-Spalte fallen.
    const from = addDaysIso(this.rangeStart(), -1);
    const to = addDaysIso(this.rangeStart(), WINDOW_DAYS);
    this.svc.plantafel(from, to).subscribe({
      next: (data) => {
        if (id === this.reqId) this.state.set({ kind: 'ready', data });
      },
      error: (err) => {
        if (id === this.reqId) this.state.set(fehlerState(err));
      },
    });
  }

  // ---- Darstellungshelfer -------------------------------------------------
  statusLabel(s: ServiceJobStatus): string {
    return serviceJobStatusLabel(s);
  }
  statusMod(s: ServiceJobStatus): string {
    return STATUS_MOD[s] ?? 'neutral';
  }
  time(iso: string): string {
    return this.timeFmt.format(new Date(iso));
  }
  /** Farbklasse der Kategorie-Kachel (Farbe nur Ergänzung; der Name steht als
   * Text dabei). Leerer String, wenn keine Kategorie gesetzt ist. */
  categoryClass(job: BoardJob): string {
    return job.category ? categoryColorClass(job.category.color_token) : '';
  }
}
