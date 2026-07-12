import { Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { AuthService } from '../../core/auth.service';
import { FaelligkeitService } from '../../core/faelligkeit.service';
import {
  GewaehrleistungBasis,
  Warranty,
  WarrantyPage,
} from '../../core/faelligkeit.model';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { WartungNav } from '../wartung-nav/wartung-nav';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: WarrantyPage }
  | VerbotenState
  | { kind: 'error' };

type Meldung = { art: 'erfolg' | 'fehler'; text: string };

/**
 * Gewährleistung — Fristen aus abgeschlossenen Aufträgen.
 *
 * Die Frist ist **je Auftrag einstellbar**; der Default steht am Firmenprofil.
 * Das Produkt leitet aus der `basis` (BGB/VOB/individuell) **keine** Frist ab —
 * die Auswahl ist ein Etikett, damit man später weiß, was vereinbart war. Der
 * Vorlauf macht den Ablauf rechtzeitig sichtbar (Fälligkeit unter „Was steht an?").
 *
 * Der Hinweis „wartungsbedürftige Anlage ohne Wartungsvertrag" ist ein
 * **Verkaufsargument**, keine Rechtsbehauptung: es wird nichts automatisch
 * verkürzt.
 */
@Component({
  selector: 'app-gewaehrleistung',
  imports: [RouterLink, ReactiveFormsModule, WartungNav, KeinZugriff, Dialog, Feld],
  templateUrl: './gewaehrleistung.html',
  styleUrl: './gewaehrleistung.scss',
})
export class Gewaehrleistung {
  private readonly svc = inject(FaelligkeitService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  protected readonly darfAendern = computed(() =>
    this.auth.darf('maintenance', 'AENDERN'),
  );

  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly meldung = signal<Meldung | null>(null);
  protected readonly bearbeiten = signal<Warranty | null>(null);
  protected readonly laeuft = signal(false);
  protected readonly formFehler = signal<string | null>(null);

  protected readonly basen: FeldOption[] = [
    { wert: 'BGB', label: 'BGB' },
    { wert: 'VOB', label: 'VOB/B' },
    { wert: 'INDIVIDUELL', label: 'Individuell vereinbart' },
  ];

  protected readonly form = this.fb.nonNullable.group({
    basis: ['BGB' as GewaehrleistungBasis, Validators.required],
    start_date: ['', Validators.required],
    duration_months: ['60', Validators.required],
    lead_time_days: ['90', Validators.required],
    is_machinery: false,
    notes: '',
  });

  private reqId = 0;

  constructor() {
    this.laden();
  }

  protected laden(): void {
    const id = ++this.reqId;
    this.state.set({ kind: 'loading' });
    this.svc.gewaehrleistungen({ page: 1, page_size: 100 }).subscribe({
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

  /** Vorschlag je Basis — Voreinstellung, keine Rechtsauskunft. */
  protected basisGewechselt(): void {
    const s = this.state();
    if (s.kind !== 'ready') return;
    const basis = this.form.controls.basis.value;
    const vorschlag = s.data.vorschlaege[basis];
    if (vorschlag) this.form.controls.duration_months.setValue(String(vorschlag));
  }

  protected bearbeitenOeffnen(w: Warranty): void {
    this.formFehler.set(null);
    this.form.reset({
      basis: w.basis,
      start_date: w.start_date,
      duration_months: String(w.duration_months),
      lead_time_days: String(w.lead_time_days),
      is_machinery: w.is_machinery,
      notes: w.notes ?? '',
    });
    this.bearbeiten.set(w);
  }

  protected speichern(): void {
    const w = this.bearbeiten();
    if (!w || this.form.invalid || this.laeuft()) {
      this.form.markAllAsTouched();
      return;
    }
    const v = this.form.getRawValue();
    this.laeuft.set(true);
    this.formFehler.set(null);
    this.svc
      .gewaehrleistungAendern(w.id, {
        basis: v.basis,
        start_date: v.start_date,
        duration_months: Number(v.duration_months),
        lead_time_days: Number(v.lead_time_days),
        is_machinery: v.is_machinery,
        notes: v.notes.trim() || null,
      })
      .subscribe({
        next: (neu) => {
          this.laeuft.set(false);
          this.bearbeiten.set(null);
          this.meldung.set({
            art: 'erfolg',
            text: `Gewährleistung zu ${neu.order_number} gespeichert — läuft ab am ${neu.end_date}.`,
          });
          this.laden();
        },
        error: (err) => {
          this.laeuft.set(false);
          this.formFehler.set(apiFehlerZuweisen(err, this.form).formular ?? null);
        },
      });
  }
}
