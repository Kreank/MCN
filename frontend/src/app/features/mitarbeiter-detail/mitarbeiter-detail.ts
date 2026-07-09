import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';
import { MitarbeiterService } from '../../core/mitarbeiter.service';
import {
  AbsenceStatus,
  AbsenceType,
  Contract,
  ContractStatus,
  EmployeeDetail,
  EmployeeStatus,
  VacationAccount,
  WEEKDAYS,
  absenceStatusClass,
  absenceStatusLabel,
  absenceTypeLabel,
  contractStatusClass,
  contractStatusLabel,
  employeeStatusClass,
  employeeStatusLabel,
} from '../../core/mitarbeiter.model';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: EmployeeDetail }
  | VerbotenState
  | { kind: 'error' };

@Component({
  selector: 'app-mitarbeiter-detail',
  imports: [Mappe, RouterLink, KeinZugriff],
  templateUrl: './mitarbeiter-detail.html',
  styleUrl: './mitarbeiter-detail.scss',
})
export class MitarbeiterDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(MitarbeiterService);

  protected readonly weekdays = WEEKDAYS;
  protected readonly tab = signal('persoenliches');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  protected readonly tabs: MappeTab[] = [
    { id: 'persoenliches', label: 'Persönliches' },
    { id: 'vertrag', label: 'Vertrag' },
    { id: 'abwesenheiten', label: 'Abwesenheiten' },
    { id: 'urlaub', label: 'Urlaub' },
  ];

  private readonly dateFmt = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  });
  private readonly numFmt = new Intl.NumberFormat('de-DE', {
    maximumFractionDigits: 2,
  });
  private readonly rateFmt = new Intl.NumberFormat('de-DE', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.tab.set('persoenliches');
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

  // ---- Darstellungshelfer -------------------------------------------------
  statusLabel(s: EmployeeStatus): string {
    return employeeStatusLabel(s);
  }
  statusClass(s: EmployeeStatus): string {
    return employeeStatusClass(s);
  }
  contractStatusLabel(s: ContractStatus): string {
    return contractStatusLabel(s);
  }
  contractStatusClass(s: ContractStatus): string {
    return contractStatusClass(s);
  }
  absenceStatusLabel(s: AbsenceStatus): string {
    return absenceStatusLabel(s);
  }
  absenceStatusClass(s: AbsenceStatus): string {
    return absenceStatusClass(s);
  }
  absenceTypeLabel(t: AbsenceType): string {
    return absenceTypeLabel(t);
  }

  d(iso: string | null): string {
    if (!iso) return '—';
    return this.dateFmt.format(new Date(iso));
  }
  /** Decimal-String → deutsche Anzeige ohne Einheit. */
  num(value: string): string {
    return this.numFmt.format(Number(value));
  }
  rate(value: string): string {
    return `${this.rateFmt.format(Number(value))} €/h`;
  }

  /** Höchste Tages-Sollstunde eines Vertrags — Bezugsgröße für die Balken. */
  maxWeekdayHours(c: Contract): number {
    const values = this.weekdays.map((w) => Number(c[w.key]));
    const max = Math.max(...values, 0);
    return max > 0 ? max : 1;
  }
  weekdayHours(c: Contract, key: keyof Contract): string {
    return this.num(c[key] as string);
  }
  weekdayPercent(c: Contract, key: keyof Contract): number {
    const v = Number(c[key]);
    return Math.round((v / this.maxWeekdayHours(c)) * 100);
  }

  /** Urlaub: verbraucht/Summe als Prozent für die Fortschrittsdarstellung. */
  vacationPercent(a: VacationAccount): number {
    const total = Number(a.total_days);
    if (total <= 0) return 0;
    const used = Number(a.used_days);
    return Math.min(100, Math.max(0, Math.round((used / total) * 100)));
  }
  vacationAria(a: VacationAccount): string {
    return `Urlaub ${this.num(a.used_days)} von ${this.num(a.total_days)} Tagen verbraucht, ${this.num(a.remaining_days)} Tage Rest.`;
  }
}
