import { Component, computed, inject, signal } from '@angular/core';
import { FormArray, FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { ZeiterfassungService } from '../../core/zeiterfassung.service';
import { AuthService } from '../../core/auth.service';
import {
  PAUSEN_MODUS_LABEL,
  PausenModus,
  Pausenregel,
  Zeitkategorie,
} from '../../core/zeiterfassung.model';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld } from '../../shared/formular/feld';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import { EinstellungenNav } from '../einstellungen-nav/einstellungen-nav';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';

type ViewState = { kind: 'loading' } | { kind: 'ready' } | VerbotenState | { kind: 'error' };

/**
 * Einstellungen → Zeiterfassung: **Zeitkategorien** und **Pausenregel**.
 *
 * `is_work_time` ist das einzige fachlich harte Attribut einer Kategorie: nur
 * daran hängt, ob die Zeit als Arbeitszeit im Sinne von ArbZG/MiLoG zählt.
 * Systemkategorien sind nicht archivierbar; „Pause" ist nicht auf Arbeitszeit
 * umschaltbar (DB-Trigger) — beides wird hier erklärt, nicht nur verhindert.
 */
@Component({
  selector: 'app-zeitkategorien',
  imports: [ReactiveFormsModule, Dialog, Bestaetigung, Feld, EinstellungenNav, KeinZugriff],
  templateUrl: './zeitkategorien.html',
  styleUrl: './zeitkategorien.scss',
})
export class Zeitkategorien {
  private readonly svc = inject(ZeiterfassungService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly kategorien = signal<Zeitkategorie[]>([]);
  protected readonly regel = signal<Pausenregel | null>(null);
  protected readonly zeigeArchivierte = signal(false);
  protected readonly fehler = signal<string | null>(null);
  protected readonly meldung = signal('');
  protected readonly laeuftAktion = signal(false);

  protected readonly dialogOffen = signal(false);
  protected readonly bearbeitet = signal<Zeitkategorie | null>(null);
  protected readonly archivierenOffen = signal<Zeitkategorie | null>(null);

  protected readonly PAUSEN_MODUS_LABEL = PAUSEN_MODUS_LABEL;
  protected readonly darfAendern = computed(() => this.auth.darf('hr', 'AENDERN'));
  protected readonly darfAnlegen = computed(() => this.auth.darf('hr', 'ANLEGEN'));

  protected readonly modi: PausenModus[] = ['KEINE', 'GESETZLICH', 'FESTE_ZEITEN'];

  protected readonly katForm = this.fb.group({
    name: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    description: this.fb.control(''),
    is_work_time: this.fb.control(true, { nonNullable: true }),
    sort_order: this.fb.control(100, { nonNullable: true }),
  });

  protected readonly pausenForm = this.fb.group({
    mode: this.fb.control<PausenModus>('GESETZLICH', { nonNullable: true }),
    fenster: this.fb.array<
      ReturnType<Zeitkategorien['neuesFenster']>
    >([]),
  });

  protected get fenster(): FormArray {
    return this.pausenForm.controls.fenster as unknown as FormArray;
  }

  protected readonly istFesteZeiten = computed(
    () => this.pausenModus() === 'FESTE_ZEITEN',
  );
  private readonly pausenModus = signal<PausenModus>('GESETZLICH');

  constructor() {
    this.laden();
    this.pausenForm.controls.mode.valueChanges.subscribe((m) => this.pausenModus.set(m));
  }

  private neuesFenster(von = '12:00', bis = '12:30') {
    return this.fb.group({
      von: this.fb.control(von, { nonNullable: true, validators: [Validators.required] }),
      bis: this.fb.control(bis, { nonNullable: true, validators: [Validators.required] }),
    });
  }

  protected laden(): void {
    this.state.set({ kind: 'loading' });
    this.svc.kategorien(this.zeigeArchivierte()).subscribe({
      next: (k) => {
        this.kategorien.set(k);
        this.svc.pausenregel().subscribe({
          next: (r) => {
            this.regel.set(r);
            this.pausenModus.set(r.mode);
            this.fenster.clear();
            for (const f of r.fixed_breaks) this.fenster.push(this.neuesFenster(f.von, f.bis));
            this.pausenForm.controls.mode.setValue(r.mode, { emitEvent: false });
            this.state.set({ kind: 'ready' });
          },
          error: (err) => this.state.set(fehlerState(err)),
        });
      },
      error: (err) => this.state.set(fehlerState(err)),
    });
  }

  // --- Kategorien ---------------------------------------------------------

  protected neu(): void {
    this.bearbeitet.set(null);
    this.katForm.reset({ name: '', description: '', is_work_time: true, sort_order: 100 });
    this.dialogOffen.set(true);
  }

  protected bearbeiten(k: Zeitkategorie): void {
    this.bearbeitet.set(k);
    this.katForm.reset({
      name: k.name,
      description: k.description ?? '',
      is_work_time: k.is_work_time,
      sort_order: k.sort_order,
    });
    // Die Pause ist per DB-Trigger nicht umschaltbar — das Feld wird gesperrt,
    // statt den Benutzer in einen 422 laufen zu lassen.
    if (k.code === 'PAUSE') this.katForm.controls.is_work_time.disable();
    else this.katForm.controls.is_work_time.enable();
    this.dialogOffen.set(true);
  }

  protected speichern(): void {
    if (this.katForm.invalid) {
      this.katForm.markAllAsTouched();
      return;
    }
    const v = this.katForm.getRawValue();
    const k = this.bearbeitet();
    this.laeuftAktion.set(true);
    const obs = k
      ? this.svc.kategorieAendern(k.id, {
          name: v.name,
          description: v.description || null,
          is_work_time: k.code === 'PAUSE' ? undefined : v.is_work_time,
          sort_order: v.sort_order,
        })
      : this.svc.kategorieAnlegen({
          name: v.name,
          description: v.description || null,
          is_work_time: v.is_work_time,
          sort_order: v.sort_order,
        });
    obs.subscribe({
      next: () => {
        this.laeuftAktion.set(false);
        this.dialogOffen.set(false);
        this.meldung.set(k ? 'Kategorie geändert.' : 'Kategorie angelegt.');
        this.laden();
      },
      error: (err) => {
        this.laeuftAktion.set(false);
        apiFehlerZuweisen(err, this.katForm);
        this.fehler.set(fehlerDetail(err));
      },
    });
  }

  protected archivieren(): void {
    const k = this.archivierenOffen();
    if (!k) return;
    this.laeuftAktion.set(true);
    this.svc.kategorieArchivieren(k.id).subscribe({
      next: () => {
        this.laeuftAktion.set(false);
        this.archivierenOffen.set(null);
        this.meldung.set('Kategorie archiviert.');
        this.laden();
      },
      error: (err) => {
        this.laeuftAktion.set(false);
        this.archivierenOffen.set(null);
        this.fehler.set(fehlerDetail(err) ?? 'Archivieren fehlgeschlagen.');
      },
    });
  }

  // --- Pausenregel --------------------------------------------------------

  protected fensterHinzu(): void {
    this.fenster.push(this.neuesFenster());
  }

  protected fensterWeg(i: number): void {
    this.fenster.removeAt(i);
  }

  protected pausenSpeichern(): void {
    const mode = this.pausenForm.controls.mode.value;
    const fixed_breaks =
      mode === 'FESTE_ZEITEN'
        ? (this.fenster.getRawValue() as { von: string; bis: string }[])
        : [];
    this.laeuftAktion.set(true);
    this.fehler.set(null);
    this.svc.pausenregelSetzen({ mode, fixed_breaks }).subscribe({
      next: (r) => {
        this.laeuftAktion.set(false);
        this.regel.set(r);
        this.meldung.set('Pausenregel gespeichert.');
      },
      error: (err) => {
        this.laeuftAktion.set(false);
        this.fehler.set(fehlerDetail(err) ?? 'Speichern fehlgeschlagen.');
      },
    });
  }
}
