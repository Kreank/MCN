import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { EinsatzService } from '../../core/einsatz.service';
import {
  BoardJob,
  Plantafel as PlantafelData,
  ServiceJobStatus,
  categoryColorClass,
  resourceTypeLabel,
  serviceJobStatusLabel,
} from '../../core/einsatz.model';
import { PlanungNav } from '../planung-nav/planung-nav';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';

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
  imports: [RouterLink, PlanungNav, KeinZugriff],
  templateUrl: './plantafel.html',
  styleUrl: './plantafel.scss',
})
export class Plantafel {
  private readonly svc = inject(EinsatzService);

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
