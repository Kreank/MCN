import { Component, computed, inject, signal } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { RouterLink } from '@angular/router';
import { MitarbeiterService } from '../../core/mitarbeiter.service';
import {
  Contract,
  EmployeeDetail,
  absenceStatusClass,
  absenceStatusLabel,
  absenceTypeLabel,
} from '../../core/mitarbeiter.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: EmployeeDetail }
  | { kind: 'kein-mitarbeiter' }
  | VerbotenState
  | { kind: 'error' };

/**
 * Meine Personalakte (Selbstauskunft) — die EIGENEN HR-Daten des angemeldeten
 * Kontos: Resturlaub, laufender Vertrag, eigene Abwesenheiten. Liest `/hr/self`
 * (Recht hr/LESEN; der Server liefert nur die eigene Zeile). 404 → eigener
 * „kein Mitarbeiterdatensatz"-Hinweis statt Fehler.
 */
@Component({
  selector: 'app-meine-personalakte',
  imports: [RouterLink, KeinZugriff],
  templateUrl: './meine-personalakte.html',
  styleUrl: './meine-personalakte.scss',
})
export class MeinePersonalakte {
  private readonly svc = inject(MitarbeiterService);

  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly absenceTypeLabel = absenceTypeLabel;
  protected readonly absenceStatusLabel = absenceStatusLabel;
  protected readonly absenceStatusClass = absenceStatusClass;

  private readonly dateFmt = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  });

  constructor() {
    this.laden();
  }

  protected laden(): void {
    this.state.set({ kind: 'loading' });
    this.svc.getSelf().subscribe({
      next: (data) => this.state.set({ kind: 'ready', data }),
      error: (err: unknown) => {
        if (err instanceof HttpErrorResponse && err.status === 404) {
          this.state.set({ kind: 'kein-mitarbeiter' });
        } else {
          this.state.set(fehlerState(err));
        }
      },
    });
  }

  protected datum(iso: string | null): string {
    return iso ? this.dateFmt.format(new Date(iso)) : '—';
  }

  /** Zahl aus Decimal-String, deutsch formatiert (z. B. „25" oder „12,5"). */
  protected tage(wert: string): string {
    const n = Number(wert);
    return Number.isFinite(n)
      ? new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1 }).format(n)
      : wert;
  }

  protected aktuellerVertrag(data: EmployeeDetail): Contract | null {
    return data.contracts.find((c) => c.is_current) ?? data.contracts[0] ?? null;
  }
}
