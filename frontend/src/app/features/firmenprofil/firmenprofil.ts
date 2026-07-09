import { Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { FirmaService } from '../../core/firma.service';
import { AuthService } from '../../core/auth.service';
import { CompanyProfile, CompanyProfileInput } from '../../core/firma.model';
import { Feld } from '../../shared/formular/feld';
import { EinstellungenNav } from '../einstellungen-nav/einstellungen-nav';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: CompanyProfile }
  | VerbotenState
  | { kind: 'error' };

/**
 * Firmenprofil (Singleton) — ganzseitiges Bearbeitungsformular, gegliedert in
 * Abschnitte (Allgemein, Anschrift, Kontakt, Steuer & Register, Bank,
 * Geschäftsführung). Ohne `company/AENDERN` ist das Formular schreibgeschützt
 * (Read-Ansicht); der Server setzt das ohnehin durch.
 */
@Component({
  selector: 'app-firmenprofil',
  imports: [ReactiveFormsModule, Feld, EinstellungenNav, KeinZugriff],
  templateUrl: './firmenprofil.html',
  styleUrl: './firmenprofil.scss',
})
export class Firmenprofil {
  private readonly svc = inject(FirmaService);
  private readonly fb = inject(FormBuilder);
  private readonly auth = inject(AuthService);

  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly laedt = signal(false);
  protected readonly erfolg = signal<string | null>(null);
  protected readonly formularMeldung = signal<string | null>(null);

  protected readonly darfAendern = computed(() => this.auth.darf('company', 'AENDERN'));

  protected readonly form = this.fb.group({
    company_name: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    legal_form: this.fb.control('', { nonNullable: true }),
    street: this.fb.control('', { nonNullable: true }),
    postal_code: this.fb.control('', { nonNullable: true }),
    city: this.fb.control('', { nonNullable: true }),
    country: this.fb.control('DE', { nonNullable: true }),
    state_code: this.fb.control('', { nonNullable: true }),
    phone: this.fb.control('', { nonNullable: true }),
    email: this.fb.control('', { nonNullable: true, validators: [Validators.email] }),
    web: this.fb.control('', { nonNullable: true }),
    tax_number: this.fb.control('', { nonNullable: true }),
    vat_id: this.fb.control('', { nonNullable: true }),
    commercial_register: this.fb.control('', { nonNullable: true }),
    bank_name: this.fb.control('', { nonNullable: true }),
    iban: this.fb.control('', { nonNullable: true }),
    bic: this.fb.control('', { nonNullable: true }),
    managing_director: this.fb.control('', { nonNullable: true }),
    managing_director_title: this.fb.control('', { nonNullable: true }),
    default_language: this.fb.control('de', { nonNullable: true }),
  });

  constructor() {
    this.laden();
  }

  private laden(): void {
    this.state.set({ kind: 'loading' });
    this.svc.getProfile().subscribe({
      next: (p) => {
        this.füllen(p);
        this.state.set({ kind: 'ready', data: p });
        if (!this.darfAendern()) this.form.disable();
      },
      error: (err: unknown) => this.state.set(fehlerState(err)),
    });
  }

  private füllen(p: CompanyProfile): void {
    this.form.patchValue({
      company_name: p.company_name ?? '',
      legal_form: p.legal_form ?? '',
      street: p.street ?? '',
      postal_code: p.postal_code ?? '',
      city: p.city ?? '',
      country: p.country ?? 'DE',
      state_code: p.state_code ?? '',
      phone: p.phone ?? '',
      email: p.email ?? '',
      web: p.web ?? '',
      tax_number: p.tax_number ?? '',
      vat_id: p.vat_id ?? '',
      commercial_register: p.commercial_register ?? '',
      bank_name: p.bank_name ?? '',
      iban: p.iban ?? '',
      bic: p.bic ?? '',
      managing_director: p.managing_director ?? '',
      managing_director_title: p.managing_director_title ?? '',
      default_language: p.default_language ?? 'de',
    });
  }

  absenden(): void {
    if (this.laedt() || !this.darfAendern()) return;
    serverFehlerZuruecksetzen(this.form);
    this.formularMeldung.set(null);
    this.erfolg.set(null);
    felderAlsBeruehrtMarkieren(this.form);
    if (this.form.invalid) return;

    const payload = this.form.getRawValue() as CompanyProfileInput;
    this.laedt.set(true);
    this.svc.updateProfile(payload).subscribe({
      next: (p) => {
        this.laedt.set(false);
        this.füllen(p);
        this.state.set({ kind: 'ready', data: p });
        this.erfolg.set('Das Firmenprofil wurde gespeichert.');
      },
      error: (err: unknown) => {
        this.laedt.set(false);
        const ergebnis = apiFehlerZuweisen(err, this.form);
        this.formularMeldung.set(ergebnis.formular);
      },
    });
  }
}
