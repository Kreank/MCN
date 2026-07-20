import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { EinsatzService } from '../../core/einsatz.service';
import {
  BoardJob,
  ServiceJobStatus,
  categoryColorClass,
  serviceJobStatusLabel,
} from '../../core/einsatz.model';
import { PlanungNav } from '../planung-nav/planung-nav';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; jobs: BoardJob[] }
  | VerbotenState
  | { kind: 'error' };

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
const WEEKDAYS = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'];

function todayIso(): string {
  const d = new Date();
  const m = `${d.getMonth() + 1}`.padStart(2, '0');
  const day = `${d.getDate()}`.padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}
function monthStartIso(): string {
  return `${todayIso().slice(0, 7)}-01`;
}
function addDaysIso(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}
/** Kalendertag eines Zeitstempels in LOKALER Zeit (konsistent mit Anzeige). */
function localDayIso(iso: string): string {
  const d = new Date(iso);
  const m = `${d.getMonth() + 1}`.padStart(2, '0');
  const day = `${d.getDate()}`.padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}
function addMonthsIso(monthIso: string, n: number): string {
  const [y, m] = monthIso.split('-').map(Number);
  const idx = (y * 12 + (m - 1)) + n;
  const ny = Math.floor(idx / 12);
  const nm = `${(idx % 12) + 1}`.padStart(2, '0');
  return `${ny}-${nm}-01`;
}

@Component({
  selector: 'app-planung-kalender',
  imports: [RouterLink, PlanungNav, KeinZugriff],
  templateUrl: './planung-kalender.html',
  styleUrl: './planung-kalender.scss',
})
export class PlanungKalender {
  private readonly svc = inject(EinsatzService);

  protected readonly weekdays = WEEKDAYS;
  protected readonly monthStart = signal(monthStartIso());
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  private readonly monthFmt = new Intl.DateTimeFormat('de-DE', {
    month: 'long',
    year: 'numeric',
  });
  private readonly timeFmt = new Intl.DateTimeFormat('de-DE', {
    hour: '2-digit',
    minute: '2-digit',
  });

  /** Das Gitter beginnt am Montag der Woche des Monatsersten. */
  private readonly gridStart = computed(() => {
    const first = this.monthStart();
    const dow = (new Date(`${first}T00:00:00Z`).getUTCDay() + 6) % 7; // Mo=0
    return addDaysIso(first, -dow);
  });

  protected readonly weeks = computed(() => {
    const start = this.gridStart();
    const month = this.monthStart().slice(0, 7);
    const today = todayIso();
    // Anzahl Wochen, die den Monat abdecken (5 oder 6).
    const daysInMonth = new Date(
      Number(month.slice(0, 4)),
      Number(month.slice(5, 7)),
      0,
    ).getDate();
    const lead = (new Date(`${this.monthStart()}T00:00:00Z`).getUTCDay() + 6) % 7;
    const weekCount = Math.ceil((lead + daysInMonth) / 7);
    const rows: { iso: string; day: number; inMonth: boolean; isToday: boolean }[][] = [];
    for (let w = 0; w < weekCount; w++) {
      const row = [];
      for (let d = 0; d < 7; d++) {
        const iso = addDaysIso(start, w * 7 + d);
        row.push({
          iso,
          day: Number(iso.slice(8, 10)),
          inMonth: iso.slice(0, 7) === month,
          isToday: iso === today,
        });
      }
      rows.push(row);
    }
    return rows;
  });

  protected readonly monthLabel = computed(() =>
    this.monthFmt.format(new Date(`${this.monthStart()}T00:00:00Z`)),
  );

  protected readonly summary = computed(() => {
    const s = this.state();
    if (s.kind === 'loading') return 'Kalender wird geladen.';
    if (s.kind === 'forbidden') return 'Keine Berechtigung für den Kalender.';
    if (s.kind === 'error') return 'Kalender konnte nicht geladen werden.';
    return `${s.jobs.length} Termine im ${this.monthLabel()}.`;
  });

  constructor() {
    this.fetch();
  }

  prev(): void {
    this.monthStart.set(addMonthsIso(this.monthStart(), -1));
    this.fetch();
  }
  next(): void {
    this.monthStart.set(addMonthsIso(this.monthStart(), 1));
    this.fetch();
  }
  today(): void {
    this.monthStart.set(monthStartIso());
    this.fetch();
  }
  retry(): void {
    this.fetch();
  }

  jobsOn(dayIso: string): BoardJob[] {
    const s = this.state();
    if (s.kind !== 'ready') return [];
    return s.jobs
      .filter((j) => localDayIso(j.scheduled_start) === dayIso)
      .sort((a, b) => (a.scheduled_start < b.scheduled_start ? -1 : 1));
  }

  private fetch(): void {
    const id = ++this.reqId;
    this.state.set({ kind: 'loading' });
    // Board-Endpoint (unpaginiert) statt Listen-Endpoint: kein Abschneiden bei
    // vielen Einsätzen. Fenster um je einen Tag geweitet für lokale Tagesgrenzen.
    const from = addDaysIso(this.gridStart(), -1);
    const to = addDaysIso(this.gridStart(), this.weeks().length * 7);
    this.svc.plantafel({ date_from: from, date_to: to }).subscribe({
      next: (data) => {
        if (id === this.reqId) this.state.set({ kind: 'ready', jobs: data.jobs });
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
  time(iso: string | null): string {
    return iso ? this.timeFmt.format(new Date(iso)) : '';
  }
  /** Farbklasse der Kategorie (Ergänzung; der Name steht immer als Text dabei). */
  categoryClass(job: BoardJob): string {
    return job.category ? categoryColorClass(job.category.color_token) : '';
  }
}
