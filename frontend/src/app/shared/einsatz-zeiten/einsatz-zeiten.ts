import { Component, computed, inject, input, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { AuthService } from '../../core/auth.service';
import { ZeiterfassungService } from '../../core/zeiterfassung.service';
import {
  Zeiteintrag,
  Zeitkategorie,
  dauerText,
  fromLocalInput,
  uhrzeit,
} from '../../core/zeiterfassung.model';
import { Dialog } from '../dialog/dialog';
import { Feld } from '../formular/feld';
import { apiFehlerZuweisen } from '../formular/api-fehler';
import { fehlerDetail } from '../http-fehler';

/**
 * Zeitbuchungen an EINEM Einsatz/Termin — der Baustein für den
 * Baustellenbericht und die Einsatz-Mappe.
 *
 * Wunsch des Auftraggebers: „Zeitbuchungen für den einzelnen Termin im
 * Baustellenbericht". Der Bericht dokumentiert, was vor Ort geschah — dazu
 * gehört, wie lange es dauerte. Die Buchung landet im **selben** Zeitstrahl
 * (`workflow.time_entry`) wie die Stempeluhr: derselbe Arbeitstag, dieselbe
 * Freigabe, dieselben Schlösser. Es gibt keinen zweiten Datenbestand.
 *
 * Rechte: `hr/LESEN`/`hr/AENDERN`. Bei row_scope EIGENE liefert der Server nur
 * die eigenen Buchungen und erzwingt den Akteur als Mitarbeiter — der Monteur
 * kann hier also niemanden anderen buchen.
 */
@Component({
  selector: 'app-einsatz-zeiten',
  imports: [ReactiveFormsModule, Dialog, Feld],
  templateUrl: './einsatz-zeiten.html',
  styleUrl: './einsatz-zeiten.scss',
})
export class EinsatzZeiten {
  private readonly svc = inject(ZeiterfassungService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  readonly serviceJobId = input.required<string>();

  protected readonly eintraege = signal<Zeiteintrag[]>([]);
  protected readonly kategorien = signal<Zeitkategorie[]>([]);
  protected readonly laedt = signal(true);
  protected readonly fehler = signal<string | null>(null);
  protected readonly meldung = signal('');
  protected readonly dialogOffen = signal(false);
  protected readonly speichert = signal(false);
  /** Kein Recht auf hr → der Baustein bleibt still (kein Fehlerbanner). */
  protected readonly verboten = signal(false);

  protected readonly dauerText = dauerText;
  protected readonly uhrzeit = uhrzeit;

  protected readonly darfBuchen = computed(() => this.auth.darf('hr', 'AENDERN'));

  protected readonly summe = computed(() =>
    this.eintraege()
      .filter((e) => e.is_work_time && e.dauer_sekunden !== null)
      .reduce((s, e) => s + (e.dauer_sekunden ?? 0), 0),
  );

  protected readonly kategorieOptionen = computed(() =>
    this.kategorien().map((k) => ({ wert: k.id, label: k.name })),
  );

  protected readonly form = this.fb.group({
    category_id: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    datum: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    von: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    bis: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    note: this.fb.control(''),
  });

  constructor() {
    this.svc.kategorien().subscribe({
      next: (k) => this.kategorien.set(k),
      error: () => this.kategorien.set([]),
    });
    // input.required ist im Konstruktor noch nicht gesetzt → im ersten
    // Effektlauf laden. Ein simpler Microtask genügt hier.
    queueMicrotask(() => this.laden());
  }

  protected laden(): void {
    this.laedt.set(true);
    this.svc.eintraegeAmEinsatz(this.serviceJobId()).subscribe({
      next: (e) => {
        this.eintraege.set(e);
        this.laedt.set(false);
      },
      error: (err) => {
        this.laedt.set(false);
        // 403 = keine hr-Rechte: der Baustein blendet sich aus, statt einen
        // Fehler zu behaupten, der keiner ist.
        this.verboten.set(true);
        this.fehler.set(fehlerDetail(err));
      },
    });
  }

  protected dialogOeffnen(): void {
    const heute = new Date();
    const pad = (n: number) => String(n).padStart(2, '0');
    const arbeit = this.kategorien().find((k) => k.code === 'ARBEITSZEIT');
    this.form.reset({
      category_id: arbeit?.id ?? '',
      datum: `${heute.getFullYear()}-${pad(heute.getMonth() + 1)}-${pad(heute.getDate())}`,
      von: '08:00',
      bis: '12:00',
      note: '',
    });
    this.dialogOffen.set(true);
  }

  protected speichern(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    const v = this.form.getRawValue();
    this.speichert.set(true);
    this.svc
      .eintragAnlegen({
        category_id: v.category_id,
        service_job_id: this.serviceJobId(),
        started_at: fromLocalInput(`${v.datum}T${v.von}`),
        ended_at: fromLocalInput(`${v.datum}T${v.bis}`),
        note: v.note || null,
      })
      .subscribe({
        next: () => {
          this.speichert.set(false);
          this.dialogOffen.set(false);
          this.meldung.set('Zeit am Termin gebucht.');
          this.laden();
        },
        error: (err) => {
          this.speichert.set(false);
          apiFehlerZuweisen(err, this.form);
          this.fehler.set(fehlerDetail(err));
        },
      });
  }
}
