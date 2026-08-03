import { Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { map } from 'rxjs';
import { AuthService } from '../../core/auth.service';
import { FaelligkeitService } from '../../core/faelligkeit.service';
import {
  Inspection,
  InspectionPage,
  InspectionType,
  IntervallArt,
  intervallLabel,
} from '../../core/faelligkeit.model';
import { PropertyService } from '../../core/property.service';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { ReferenzWahl, RefSuche } from '../../shared/formular/referenz-wahl';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { WartungNav } from '../wartung-nav/wartung-nav';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: InspectionPage }
  | VerbotenState
  | { kind: 'error' };

type Meldung = { art: 'erfolg' | 'fehler'; text: string };

/**
 * Prüffristen — wiederkehrende gesetzliche/technische Prüfungen an einer
 * Liegenschaft, **ohne** Wartungsvertrag (Legionellen, Schornsteinfeger,
 * Rückflussverhinderer, Rauchwarnmelder …).
 *
 * Die **Prüfarten sind Stammdaten des Betriebs**. Was mitgeliefert wird, ist
 * ein Vorschlag (`is_suggestion`) — kein Normkatalog und keine Rechtsauskunft.
 * Das UI sagt das ausdrücklich.
 */
@Component({
  selector: 'app-pruefungen',
  imports: [
    RouterLink,
    ReactiveFormsModule,
    WartungNav,
    KeinZugriff,
    Dialog,
    Feld,
    ReferenzWahl,
    Bestaetigung,
  ],
  templateUrl: './pruefungen.html',
  styleUrl: './pruefungen.scss',
})
export class Pruefungen {
  private readonly svc = inject(FaelligkeitService);
  private readonly propertySvc = inject(PropertyService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  protected readonly darfAnlegen = computed(() =>
    this.auth.darf('maintenance', 'ANLEGEN'),
  );
  protected readonly darfAendern = computed(() =>
    this.auth.darf('maintenance', 'AENDERN'),
  );

  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly arten = signal<InspectionType[]>([]);
  protected readonly meldung = signal<Meldung | null>(null);

  protected readonly neuOffen = signal(false);
  protected readonly artenOffen = signal(false);
  protected readonly deaktivierenFuer = signal<Inspection | null>(null);
  protected readonly laeuft = signal(false);
  protected readonly formFehler = signal<string | null>(null);

  protected readonly intervalle: FeldOption[] = [
    { wert: 'JAEHRLICH', label: 'Jährlich' },
    { wert: 'MONATLICH', label: 'Monatlich' },
    { wert: 'WOECHENTLICH', label: 'Wöchentlich' },
    { wert: 'TAGE', label: 'Alle N Tage' },
  ];

  protected readonly artOptionen = computed<FeldOption[]>(() =>
    this.arten().map((a) => ({ wert: a.id, label: a.name })),
  );

  protected readonly form = this.fb.nonNullable.group({
    inspection_type_id: ['', Validators.required],
    property_id: ['', Validators.required],
    start_date: ['', Validators.required],
    name: '',
    notes: '',
  });

  protected readonly artForm = this.fb.nonNullable.group({
    name: ['', Validators.required],
    interval_kind: ['JAEHRLICH' as IntervallArt, Validators.required],
    interval_days: '',
    lead_time_days: '30',
    responsibility: '',
    notes: '',
  });

  private reqId = 0;
  protected readonly intervallLabel = intervallLabel;

  /** Die gewählte Prüfart — für die Vorschau von Intervall/Vorlauf im Formular.
   *
   * `arten()` allein reicht als Producer NICHT: Das computed rechnete dann nur
   * neu, wenn sich der KATALOG ändert, nicht wenn der Nutzer eine andere Art
   * wählt — die Vorschau zeigte weiter die Werte der ersten Auswahl. Deshalb
   * kommt die Auswahl über `toSignal` aus den `valueChanges`. */
  private readonly artId = toSignal(this.form.controls.inspection_type_id.valueChanges, {
    initialValue: this.form.controls.inspection_type_id.value,
  });
  protected readonly gewaehlteArt = computed(() =>
    this.arten().find((a) => a.id === this.artId()),
  );

  protected readonly liegenschaftSuche: RefSuche = (q) =>
    this.propertySvc.list({ page: 1, page_size: 20, q }).pipe(
      map((p) =>
        p.items.map((o) => ({
          id: o.id,
          label: o.name,
          sub: `${o.property_number} · ${o.city}`,
        })),
      ),
    );

  constructor() {
    this.laden();
    this.artenLaden();
  }

  protected laden(): void {
    const id = ++this.reqId;
    this.state.set({ kind: 'loading' });
    this.svc.pruefungen({ page: 1, page_size: 100 }).subscribe({
      next: (data) => {
        if (id !== this.reqId) return;
        this.state.set({ kind: 'ready', data });
      },
      error: (err) => {
        if (id !== this.reqId) return;
        this.state.set(fehlerState(err));
      },
    });
  }

  private artenLaden(): void {
    this.svc.pruefarten(true).subscribe({
      next: (arten) => this.arten.set(arten),
      error: () => this.arten.set([]),
    });
  }

  // --- Prüfung anlegen ------------------------------------------------------

  protected neuOeffnen(): void {
    this.formFehler.set(null);
    this.form.reset({
      inspection_type_id: '',
      property_id: '',
      start_date: '',
      name: '',
      notes: '',
    });
    this.neuOffen.set(true);
  }

  protected anlegen(): void {
    if (this.form.invalid || this.laeuft()) {
      this.form.markAllAsTouched();
      return;
    }
    const w = this.form.getRawValue();
    this.laeuft.set(true);
    this.formFehler.set(null);
    this.svc
      .pruefungAnlegen({
        inspection_type_id: w.inspection_type_id,
        property_id: w.property_id,
        start_date: w.start_date,
        name: w.name.trim() || null,
        notes: w.notes.trim() || null,
      })
      .subscribe({
        next: (p) => {
          this.laeuft.set(false);
          this.neuOffen.set(false);
          this.meldung.set({
            art: 'erfolg',
            text: `Prüffrist „${p.name}“ angelegt (erste Fälligkeit ${p.next_due_date}).`,
          });
          this.laden();
        },
        error: (err) => {
          this.laeuft.set(false);
          this.formFehler.set(apiFehlerZuweisen(err, this.form).formular ?? null);
        },
      });
  }

  // --- Prüfart anlegen ------------------------------------------------------

  protected artenOeffnen(): void {
    this.formFehler.set(null);
    this.artForm.reset({
      name: '',
      interval_kind: 'JAEHRLICH',
      interval_days: '',
      lead_time_days: '30',
      responsibility: '',
      notes: '',
    });
    this.artenOffen.set(true);
  }

  protected artAnlegen(): void {
    if (this.artForm.invalid || this.laeuft()) {
      this.artForm.markAllAsTouched();
      return;
    }
    const w = this.artForm.getRawValue();
    this.laeuft.set(true);
    this.formFehler.set(null);
    this.svc
      .pruefartAnlegen({
        name: w.name,
        interval_kind: w.interval_kind,
        interval_days: w.interval_days ? Number(w.interval_days) : null,
        lead_time_days: Number(w.lead_time_days) || 0,
        responsibility: w.responsibility.trim() || null,
        notes: w.notes.trim() || null,
      })
      .subscribe({
        next: (a) => {
          this.laeuft.set(false);
          this.artenOffen.set(false);
          this.meldung.set({ art: 'erfolg', text: `Prüfart „${a.name}“ angelegt.` });
          this.artenLaden();
        },
        error: (err) => {
          this.laeuft.set(false);
          this.formFehler.set(apiFehlerZuweisen(err, this.artForm).formular ?? null);
        },
      });
  }

  // --- Prüfung deaktivieren -------------------------------------------------

  protected deaktivierenOeffnen(p: Inspection): void {
    this.deaktivierenFuer.set(p);
  }

  protected deaktivieren(): void {
    const p = this.deaktivierenFuer();
    if (!p) return;
    this.laeuft.set(true);
    this.svc.pruefungStatus(p.id, 'INAKTIV').subscribe({
      next: () => {
        this.laeuft.set(false);
        this.deaktivierenFuer.set(null);
        this.meldung.set({
          art: 'erfolg',
          text: 'Prüffrist deaktiviert — sie erzeugt keine neuen Fälligkeiten mehr.',
        });
        this.laden();
      },
      error: (err) => {
        this.laeuft.set(false);
        this.deaktivierenFuer.set(null);
        const e = err as { error?: { detail?: string } };
        this.meldung.set({
          art: 'fehler',
          text: e?.error?.detail ?? 'Die Aktion ist fehlgeschlagen.',
        });
      },
    });
  }
}
