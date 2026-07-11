import { Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { FirmaService } from '../../core/firma.service';
import { AuthService } from '../../core/auth.service';
import { AcquisitionSource } from '../../core/firma.model';
import { Feld } from '../../shared/formular/feld';
import { EinstellungenNav } from '../einstellungen-nav/einstellungen-nav';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: AcquisitionSource[] }
  | VerbotenState
  | { kind: 'error' };

/**
 * Akquisekanal-Katalog (company.acquisition_source) — „Wie ist der Kunde auf uns
 * gekommen?". Anlegen, Bezeichnung ändern, Deaktivieren (kein Löschen). Ohne
 * `company/AENDERN` schreibgeschützt.
 */
@Component({
  selector: 'app-quellen',
  imports: [ReactiveFormsModule, Feld, EinstellungenNav, KeinZugriff],
  templateUrl: './quellen.html',
  styleUrl: './quellen.scss',
})
export class Quellen {
  private readonly svc = inject(FirmaService);
  private readonly fb = inject(FormBuilder);
  private readonly auth = inject(AuthService);

  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly darfAendern = computed(() => this.auth.darf('company', 'AENDERN'));
  protected readonly laedt = signal(false);
  protected readonly meldung = signal<string | null>(null);
  protected readonly bearbeite = signal<string | null>(null);

  protected readonly neuForm = this.fb.group({
    code: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.pattern(/^[A-Za-z0-9_]{2,}$/)],
    }),
    label: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
  });

  protected readonly editLabel = this.fb.control('', {
    nonNullable: true,
    validators: [Validators.required],
  });

  constructor() {
    this.laden();
  }

  private laden(): void {
    this.state.set({ kind: 'loading' });
    this.svc.listAcquisitionSources().subscribe({
      next: (data) => this.state.set({ kind: 'ready', data }),
      error: (err: unknown) => this.state.set(fehlerState(err)),
    });
  }

  anlegen(): void {
    if (this.laedt() || !this.darfAendern()) return;
    this.meldung.set(null);
    this.neuForm.markAllAsTouched();
    if (this.neuForm.invalid) return;
    const v = this.neuForm.getRawValue();
    this.laedt.set(true);
    this.svc
      .createAcquisitionSource({ code: v.code.trim().toUpperCase(), label: v.label.trim() })
      .subscribe({
        next: () => {
          this.laedt.set(false);
          this.neuForm.reset({ code: '', label: '' });
          this.laden();
        },
        error: (err: unknown) => {
          this.laedt.set(false);
          this.meldung.set(fehlerDetail(err) ?? 'Der Kanal konnte nicht angelegt werden.');
        },
      });
  }

  starteBearbeiten(s: AcquisitionSource): void {
    if (!this.darfAendern()) return;
    this.meldung.set(null);
    this.editLabel.setValue(s.label);
    this.bearbeite.set(s.id);
  }

  speichern(s: AcquisitionSource): void {
    if (this.laedt()) return;
    this.editLabel.markAsTouched();
    if (this.editLabel.invalid) return;
    this.laedt.set(true);
    this.svc.updateAcquisitionSource(s.id, { label: this.editLabel.value.trim() }).subscribe({
      next: () => {
        this.laedt.set(false);
        this.bearbeite.set(null);
        this.laden();
      },
      error: (err: unknown) => {
        this.laedt.set(false);
        this.meldung.set(fehlerDetail(err) ?? 'Der Kanal konnte nicht gespeichert werden.');
      },
    });
  }

  umschalten(s: AcquisitionSource): void {
    if (this.laedt() || !this.darfAendern()) return;
    this.meldung.set(null);
    this.laedt.set(true);
    this.svc.updateAcquisitionSource(s.id, { active: !s.active }).subscribe({
      next: () => {
        this.laedt.set(false);
        this.laden();
      },
      error: (err: unknown) => {
        this.laedt.set(false);
        this.meldung.set(fehlerDetail(err) ?? 'Der Status konnte nicht geändert werden.');
      },
    });
  }

  abbrechen(): void {
    this.bearbeite.set(null);
  }
}
