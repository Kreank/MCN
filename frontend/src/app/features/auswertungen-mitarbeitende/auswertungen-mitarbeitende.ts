import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { AuswertungService, csvDownloadAusloesen } from '../../core/auswertungen.service';
import { MitarbeitendeDashboard } from '../../core/auswertungen.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: MitarbeitendeDashboard }
  | VerbotenState
  | { kind: 'error' };

interface Bar {
  label: string;
  display: string;
  widthPct: number;
}

const ABSENCE_LABEL: Record<string, string> = {
  URLAUB: 'Urlaub',
  KRANKHEIT: 'Krankheit',
  ELTERNZEIT: 'Elternzeit',
  SONDERURLAUB: 'Sonderurlaub',
  UNBEZAHLT: 'Unbezahlt',
  FORTBILDUNG: 'Fortbildung',
};

@Component({
  selector: 'app-auswertungen-mitarbeitende',
  imports: [RouterLink, KeinZugriff],
  templateUrl: './auswertungen-mitarbeitende.html',
  styleUrl: './auswertungen-mitarbeitende.scss',
})
export class AuswertungenMitarbeitende {
  private readonly svc = inject(AuswertungService);
  private readonly destroyRef = inject(DestroyRef);
  protected readonly year = signal(new Date().getFullYear());
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly exportLaeuft = signal(false);
  protected readonly exportMeldung = signal<string | null>(null);
  private reqId = 0;

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  /** Auslastung (Ist-Stunden) je Mitarbeiter als horizontale Balken. */
  protected readonly stundenBars = computed<Bar[]>(() => {
    const d = this.daten();
    if (!d) return [];
    const max = Math.max(1, ...d.people.map((p) => Number(p.worked_hours)));
    return d.people.map((p) => {
      const value = Number(p.worked_hours);
      return {
        label: p.display_name,
        display: this.stunden(p.worked_hours),
        widthPct: Math.max(value > 0 ? 4 : 0, (value / max) * 100),
      };
    });
  });

  constructor() {
    this.load();
  }

  retry(): void {
    this.load();
  }

  prevYear(): void {
    this.year.update((y) => y - 1);
    this.load();
  }
  nextYear(): void {
    this.year.update((y) => y + 1);
    this.load();
  }

  /** Laedt den CSV-Export fuer das AKTUELL gewaehlte Jahr (Export = Ansicht). */
  exportieren(): void {
    if (this.exportLaeuft()) return;
    this.exportLaeuft.set(true);
    this.exportMeldung.set(null);
    this.svc
      .mitarbeitendeExport(this.year())
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: ({ blob, filename }) => {
          csvDownloadAusloesen(blob, filename);
          this.exportLaeuft.set(false);
          this.exportMeldung.set(`Export „${filename}" wurde heruntergeladen.`);
        },
        error: (err) => {
          this.exportLaeuft.set(false);
          this.exportMeldung.set(fehlerDetail(err) ?? 'Der Export konnte nicht erstellt werden.');
        },
      });
  }

  private load(): void {
    const id = ++this.reqId;
    this.state.set({ kind: 'loading' });
    this.svc.mitarbeitende(this.year()).subscribe({
      next: (data) => {
        if (id === this.reqId) this.state.set({ kind: 'ready', data });
      },
      error: (err) => {
        if (id === this.reqId) this.state.set(fehlerState(err));
      },
    });
  }

  absenceLabel(t: string): string {
    return ABSENCE_LABEL[t] ?? t;
  }

  /** Stundenzahl als deutsche Zahl mit „h". */
  stunden(h: string): string {
    return new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1 }).format(Number(h)) + ' h';
  }

  /** Tageszahl als deutsche Zahl (bis 1 Nachkommastelle). */
  tage(d: string): string {
    return new Intl.NumberFormat('de-DE', { maximumFractionDigits: 2 }).format(Number(d));
  }
}
