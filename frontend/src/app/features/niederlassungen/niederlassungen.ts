import { Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { FirmaService } from '../../core/firma.service';
import { AuthService } from '../../core/auth.service';
import { Branch } from '../../core/firma.model';
import { Feld } from '../../shared/formular/feld';
import { EinstellungenNav } from '../einstellungen-nav/einstellungen-nav';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: Branch[] }
  | VerbotenState
  | { kind: 'error' };

/**
 * Niederlassungen (company.branch) — anlegen, bearbeiten, deaktivieren (kein
 * Löschen). Ohne `company/AENDERN` schreibgeschützt.
 */
@Component({
  selector: 'app-niederlassungen',
  imports: [ReactiveFormsModule, Feld, EinstellungenNav, KeinZugriff],
  templateUrl: './niederlassungen.html',
  styleUrl: './niederlassungen.scss',
})
export class Niederlassungen {
  private readonly svc = inject(FirmaService);
  private readonly fb = inject(FormBuilder);
  private readonly auth = inject(AuthService);

  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly darfAendern = computed(() => this.auth.darf('company', 'AENDERN'));
  protected readonly laedt = signal(false);
  protected readonly meldung = signal<string | null>(null);
  /** 'neu' beim Anlegen, Branch-id beim Bearbeiten, sonst null. */
  protected readonly modus = signal<string | null>(null);

  protected readonly form = this.fb.group({
    name: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    street: this.fb.control('', { nonNullable: true }),
    postal_code: this.fb.control('', { nonNullable: true }),
    city: this.fb.control('', { nonNullable: true }),
    phone: this.fb.control('', { nonNullable: true }),
    email: this.fb.control('', { nonNullable: true, validators: [Validators.email] }),
  });

  constructor() {
    this.laden();
  }

  private laden(): void {
    this.state.set({ kind: 'loading' });
    this.svc.listBranches().subscribe({
      next: (data) => this.state.set({ kind: 'ready', data }),
      error: (err: unknown) => this.state.set(fehlerState(err)),
    });
  }

  neu(): void {
    if (!this.darfAendern()) return;
    this.meldung.set(null);
    this.form.reset({ name: '', street: '', postal_code: '', city: '', phone: '', email: '' });
    this.modus.set('neu');
  }

  starteBearbeiten(b: Branch): void {
    if (!this.darfAendern()) return;
    this.meldung.set(null);
    this.form.reset({
      name: b.name,
      street: b.street ?? '',
      postal_code: b.postal_code ?? '',
      city: b.city ?? '',
      phone: b.phone ?? '',
      email: b.email ?? '',
    });
    this.modus.set(b.id);
  }

  abbrechen(): void {
    this.modus.set(null);
    this.meldung.set(null);
  }

  speichern(): void {
    if (this.laedt()) return;
    this.meldung.set(null);
    this.form.markAllAsTouched();
    if (this.form.invalid) return;
    const v = this.form.getRawValue();
    const modus = this.modus();
    this.laedt.set(true);
    const request$ =
      modus === 'neu'
        ? this.svc.createBranch(v)
        : this.svc.updateBranch(modus!, v);
    request$.subscribe({
      next: () => {
        this.laedt.set(false);
        this.modus.set(null);
        this.laden();
      },
      error: (err: unknown) => {
        this.laedt.set(false);
        this.meldung.set(fehlerDetail(err) ?? 'Die Niederlassung konnte nicht gespeichert werden.');
      },
    });
  }

  umschalten(b: Branch): void {
    if (this.laedt() || !this.darfAendern()) return;
    this.meldung.set(null);
    this.laedt.set(true);
    this.svc.updateBranch(b.id, { active: !b.active }).subscribe({
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
}
