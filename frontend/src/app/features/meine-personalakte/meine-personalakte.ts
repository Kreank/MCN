import { Component, computed, inject, signal } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { MitarbeiterService } from '../../core/mitarbeiter.service';
import {
  ABSENCE_TYPES,
  Absence,
  AbsenceType,
  Contract,
  EmployeeDetail,
  absenceStatusClass,
  absenceStatusLabel,
  absenceTypeLabel,
} from '../../core/mitarbeiter.model';
import { Attest } from '../../shared/attest/attest';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld } from '../../shared/formular/feld';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: EmployeeDetail }
  | { kind: 'kein-mitarbeiter' }
  | VerbotenState
  | { kind: 'error' };

/**
 * Meine Personalakte (Selbstauskunft **und Selbstbedienung**) — die EIGENEN
 * HR-Daten des angemeldeten Kontos: Resturlaub, laufender Vertrag, eigene
 * Abwesenheiten. Liest `/hr/self` (Recht hr/LESEN; der Server liefert nur die
 * eigene Zeile). 404 → eigener „kein Mitarbeiterdatensatz"-Hinweis statt Fehler.
 *
 * Befund E6: Bis Migration 0130 war das eine reine Anzeige — der Monteur konnte
 * seinen Urlaub nicht beantragen, das musste das Büro für ihn tun. Jetzt stellt
 * er den Antrag hier selbst, reicht ihn ein und zieht ihn bei Bedarf zurück.
 *
 * Die Grenze, die bleibt: **Genehmigen ist nicht Sache des Antragstellers.**
 * Dafür gibt es keinen Knopf; der Server verlangt `hr/FREIGEBEN`, das der
 * Monteur nicht trägt.
 */
@Component({
  selector: 'app-meine-personalakte',
  imports: [ReactiveFormsModule, RouterLink, KeinZugriff, Attest, Dialog, Feld],
  templateUrl: './meine-personalakte.html',
  styleUrl: './meine-personalakte.scss',
})
export class MeinePersonalakte {
  private readonly svc = inject(MitarbeiterService);
  private readonly fb = inject(FormBuilder);

  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly absenceTypeLabel = absenceTypeLabel;
  protected readonly absenceStatusLabel = absenceStatusLabel;
  protected readonly absenceStatusClass = absenceStatusClass;
  protected readonly absenceTypes = ABSENCE_TYPES;

  /** Rückmeldung nach einer Aktion — als `aria-live`-Region angesagt. */
  protected readonly meldung = signal<{ art: 'erfolg' | 'fehler'; text: string } | null>(null);
  protected readonly antragOffen = signal(false);
  protected readonly antragLaedt = signal(false);
  protected readonly antragFehler = signal<string | null>(null);
  /** Id der Abwesenheit, an der gerade eine Aktion läuft (sperrt nur ihre Knöpfe). */
  protected readonly aktionAn = signal<string | null>(null);

  protected readonly antragForm = this.fb.group({
    absence_type: this.fb.control<AbsenceType>('URLAUB', { nonNullable: true }),
    start_date: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    end_date: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    half_day_start: this.fb.control(false, { nonNullable: true }),
    half_day_end: this.fb.control(false, { nonNullable: true }),
    reason: this.fb.control('', { nonNullable: true }),
  });

  /**
   * Krankheit ist eine **Meldung**, kein Antrag — man beantragt nicht, krank zu
   * sein. Der Hinweistext wechselt entsprechend mit; die Bescheinigung lädt man
   * anschließend weiter unten hoch (§ 5 EFZG).
   */
  protected readonly istKrankmeldung = computed(
    () => this.antragForm.controls.absence_type.value === 'KRANKHEIT',
  );

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

  /** Stiller Nachzieher nach einer Aktion — ohne Ladezustand, damit die
   *  Rückmeldung stehen bleibt und die Seite nicht wegblinkt. */
  private neuLaden(): void {
    this.svc.getSelf().subscribe({
      next: (data) => this.state.set({ kind: 'ready', data }),
      error: (err: unknown) => this.state.set(fehlerState(err)),
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

  /**
   * Die eigenen Krankmeldungen — nur an ihnen hängt eine
   * Arbeitsunfähigkeitsbescheinigung. Für Urlaub oder Fortbildung gibt es nichts
   * zu bescheinigen, und ein Upload-Feld dort lüde zu Datensammlung ein, für die
   * es keinen Zweck gibt (DSGVO Art. 5: Datenminimierung).
   */
  protected krankmeldungen(data: EmployeeDetail): Absence[] {
    return data.absences.filter((a) => a.absence_type === 'KRANKHEIT');
  }

  /**
   * Eine verworfene Abwesenheit (abgelehnt/zurückgezogen) nimmt keine
   * Bescheinigung mehr an — der Antrag ist gegenstandslos, die DB verbietet den
   * Anhang. Der Knopf verschwindet, statt in einen 422 zu laufen.
   */
  protected attestOffen(a: Absence): boolean {
    return a.status !== 'ABGELEHNT' && a.status !== 'ZURUECKGEZOGEN';
  }

  // --- Antrag stellen -------------------------------------------------------

  protected antragOeffnen(): void {
    const heute = new Date();
    const pad = (n: number) => String(n).padStart(2, '0');
    const iso = `${heute.getFullYear()}-${pad(heute.getMonth() + 1)}-${pad(heute.getDate())}`;
    this.antragForm.reset({
      absence_type: 'URLAUB',
      start_date: iso,
      end_date: iso,
      half_day_start: false,
      half_day_end: false,
      reason: '',
    });
    this.antragFehler.set(null);
    this.meldung.set(null);
    this.antragOffen.set(true);
  }

  protected antragSchliessen(): void {
    if (!this.antragLaedt()) this.antragOffen.set(false);
  }

  protected antragAbsenden(): void {
    const s = this.state();
    if (s.kind !== 'ready' || this.antragLaedt()) return;
    serverFehlerZuruecksetzen(this.antragForm);
    this.antragFehler.set(null);
    felderAlsBeruehrtMarkieren(this.antragForm);
    if (this.antragForm.invalid) return;

    const v = this.antragForm.getRawValue();
    this.antragLaedt.set(true);
    // days_count rechnet der Server aus dem Vertrag — nie im Frontend senden.
    this.svc
      .createAbsence(s.data.id, {
        absence_type: v.absence_type,
        start_date: v.start_date,
        end_date: v.end_date,
        half_day_start: v.half_day_start,
        half_day_end: v.half_day_end,
        reason: v.reason.trim() || null,
      })
      .subscribe({
        next: () => {
          this.antragLaedt.set(false);
          this.antragOffen.set(false);
          this.meldung.set({
            art: 'erfolg',
            text: 'Antrag als Entwurf angelegt. Zum Genehmigen bitte noch einreichen.',
          });
          this.neuLaden();
        },
        error: (err) => {
          this.antragLaedt.set(false);
          this.antragFehler.set(apiFehlerZuweisen(err, this.antragForm).formular);
        },
      });
  }

  // --- Einreichen / Zurückziehen -------------------------------------------

  /** Ein Entwurf ist noch nicht beantragt — er muss eingereicht werden. */
  protected kannEinreichen(a: Absence): boolean {
    return a.status === 'ENTWURF';
  }

  /**
   * Zurückziehen geht, solange noch nicht entschieden ist. Nach der
   * Entscheidung ist der Antrag Historie; ein genehmigter Urlaub wird nicht
   * einseitig zurückgenommen, sondern mit dem Betrieb besprochen.
   */
  protected kannZurueckziehen(a: Absence): boolean {
    return a.status === 'ENTWURF' || a.status === 'EINGEREICHT';
  }

  protected einreichen(a: Absence): void {
    this.absenceAktion(a.id, this.svc.submitAbsence(a.id), 'Antrag eingereicht.');
  }

  protected zurueckziehen(a: Absence): void {
    this.absenceAktion(a.id, this.svc.withdrawAbsence(a.id), 'Antrag zurückgezogen.');
  }

  private absenceAktion(
    id: string,
    obs: ReturnType<MitarbeiterService['submitAbsence']>,
    erfolg: string,
  ): void {
    if (this.aktionAn()) return;
    this.aktionAn.set(id);
    this.meldung.set(null);
    obs.subscribe({
      next: () => {
        this.aktionAn.set(null);
        this.meldung.set({ art: 'erfolg', text: erfolg });
        this.neuLaden();
      },
      error: (err) => {
        this.aktionAn.set(null);
        this.meldung.set({
          art: 'fehler',
          text: fehlerDetail(err) ?? 'Die Aktion ist fehlgeschlagen.',
        });
      },
    });
  }
}
