import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { FirmaService } from '../../core/firma.service';
import { AuthService } from '../../core/auth.service';
import { CompanyProfile, CompanyProfileInput } from '../../core/firma.model';
import { Feld } from '../../shared/formular/feld';
import { EinstellungenNav } from '../einstellungen-nav/einstellungen-nav';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';
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
  imports: [ReactiveFormsModule, Feld, EinstellungenNav, KeinZugriff, Bestaetigung],
  templateUrl: './firmenprofil.html',
  styleUrl: './firmenprofil.scss',
})
export class Firmenprofil {
  private readonly svc = inject(FirmaService);
  private readonly fb = inject(FormBuilder);
  private readonly auth = inject(AuthService);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly laedt = signal(false);
  protected readonly erfolg = signal<string | null>(null);
  protected readonly formularMeldung = signal<string | null>(null);

  // --- Firmenlogo ----------------------------------------------------------
  /** Object-URL der aktuellen Logo-Vorschau (null = kein Logo). */
  protected readonly logoUrl = signal<string | null>(null);
  protected readonly logoVorhanden = signal(false);
  protected readonly logoLaedt = signal(false);
  protected readonly logoFehler = signal<string | null>(null);
  protected readonly logoEntfernenFragen = signal(false);
  /** Erlaubte Formate (Server erzwingt es ohnehin) — für accept + Hinweis. */
  protected readonly logoAkzeptiert = 'image/png,image/jpeg';

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
    // Angezeigte Object-URL beim Zerstören freigeben (kein Speicherleck).
    this.destroyRef.onDestroy(() => this.logoUrlSetzen(null));
  }

  private laden(): void {
    this.state.set({ kind: 'loading' });
    this.svc.getProfile().subscribe({
      next: (p) => {
        this.füllen(p);
        this.state.set({ kind: 'ready', data: p });
        if (!this.darfAendern()) this.form.disable();
        this.logoVorhanden.set(p.has_logo);
        if (p.has_logo) this.logoVorschauLaden();
        else this.logoUrlSetzen(null);
      },
      error: (err: unknown) => this.state.set(fehlerState(err)),
    });
  }

  // --- Firmenlogo ----------------------------------------------------------

  /** Holt die Logo-Bytes als Blob und zeigt sie als Vorschau (Object-URL). */
  private logoVorschauLaden(): void {
    this.svc
      .getLogo()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (blob) => this.logoUrlSetzen(URL.createObjectURL(blob)),
        // Vorschau ist nicht kritisch: bei Abruf-Fehler bleibt sie leer.
        error: () => this.logoUrlSetzen(null),
      });
  }

  /** Setzt die Vorschau-URL und gibt eine zuvor gehaltene URL frei. */
  private logoUrlSetzen(url: string | null): void {
    const alt = this.logoUrl();
    if (alt) URL.revokeObjectURL(alt);
    this.logoUrl.set(url);
  }

  logoGewaehlt(event: Event): void {
    const input = event.target as HTMLInputElement;
    const datei = input.files?.[0];
    // Zurücksetzen, damit dieselbe Datei erneut gewählt werden kann.
    input.value = '';
    if (datei) this.logoHochladen(datei);
  }

  private logoHochladen(datei: File): void {
    if (this.logoLaedt() || !this.darfAendern()) return;
    this.logoFehler.set(null);
    this.logoLaedt.set(true);
    this.svc
      .uploadLogo(datei)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (p) => {
          this.logoLaedt.set(false);
          this.logoVorhanden.set(p.has_logo);
          // Vom Server frisch laden (zeigt genau die gespeicherten Bytes).
          this.logoVorschauLaden();
        },
        error: (err: unknown) => {
          this.logoLaedt.set(false);
          this.logoFehler.set(
            fehlerDetail(err) ??
              'Das Logo konnte nicht hochgeladen werden. Erlaubt sind PNG und JPEG bis 2 MB.',
          );
        },
      });
  }

  logoEntfernenFragenOeffnen(): void {
    this.logoFehler.set(null);
    this.logoEntfernenFragen.set(true);
  }

  logoEntfernenAbbrechen(): void {
    if (!this.logoLaedt()) this.logoEntfernenFragen.set(false);
  }

  logoEntfernenBestaetigen(): void {
    if (this.logoLaedt() || !this.darfAendern()) return;
    this.logoLaedt.set(true);
    this.svc
      .deleteLogo()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (p) => {
          this.logoLaedt.set(false);
          this.logoEntfernenFragen.set(false);
          this.logoVorhanden.set(p.has_logo);
          this.logoUrlSetzen(null);
        },
        error: (err: unknown) => {
          this.logoLaedt.set(false);
          this.logoEntfernenFragen.set(false);
          this.logoFehler.set(
            fehlerDetail(err) ?? 'Das Logo konnte nicht entfernt werden.',
          );
        },
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
