import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { FirmaService } from '../../core/firma.service';
import { AuthService } from '../../core/auth.service';
import { CompanyProfile, CompanyProfileInput } from '../../core/firma.model';
import { Feld, FeldOption } from '../../shared/formular/feld';
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

/** Ganze Zahl aus einem Textfeld (leer → null). Der Server prüft die Grenzen. */
function ganzzahlOderNull(wert: string): number | null {
  const t = (wert ?? '').trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) ? Math.trunc(n) : null;
}

/**
 * Firmenprofil (Singleton) — ganzseitiges Bearbeitungsformular, gegliedert in
 * Abschnitte (Allgemein, Anschrift, Kontakt, Steuer & Register, Bank,
 * Geschäftsführung, DATEV-Export). Ohne `company/AENDERN` ist das Formular
 * schreibgeschützt (Read-Ansicht); der Server setzt das ohnehin durch.
 *
 * DATEV: Die Konten sind reine Overrides — leer bedeutet „SKR-Standard des
 * Servers". Der Abschlags-Buchungsmodus entscheidet, ob Abschlags-/Teilrechnungen
 * als Erlös (Teilleistung) oder als erhaltene Anzahlung gebucht werden; das ist
 * eine Frage an den Steuerberater, deshalb steht sie erklärt im Formular.
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

    // --- DATEV-Export --------------------------------------------------------
    datev_consultant_number: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.pattern(/^\d{4,7}$/)],
    }),
    datev_client_number: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.pattern(/^\d{1,5}$/)],
    }),
    datev_chart_of_accounts: this.fb.control('', { nonNullable: true }),
    datev_account_length: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.pattern(/^[4-8]$/)],
    }),
    datev_fiscal_year_start_month: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.pattern(/^(1[0-2]|[1-9])$/)],
    }),
    datev_debtor_account: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.pattern(/^\d{3,9}$/)],
    }),
    datev_revenue_account_full: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.pattern(/^\d{3,9}$/)],
    }),
    datev_revenue_account_reduced: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.pattern(/^\d{3,9}$/)],
    }),
    datev_revenue_account_free: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.pattern(/^\d{3,9}$/)],
    }),
    datev_revenue_account_reverse: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.pattern(/^\d{3,9}$/)],
    }),
    datev_advance_mode: this.fb.control('ERLOES', { nonNullable: true }),
    datev_advance_account_full: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.pattern(/^\d{3,9}$/)],
    }),
    datev_advance_account_reduced: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.pattern(/^\d{3,9}$/)],
    }),
    datev_advance_account_free: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.pattern(/^\d{3,9}$/)],
    }),
    datev_advance_account_reverse: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.pattern(/^\d{3,9}$/)],
    }),
    // Resturlaubs-Verfall (0072). Ein Select statt zweier Zahlenfelder: der
    // Verfallstag ist in der Praxis der 31.03. oder das Jahresende — freie
    // Tag/Monat-Eingabe lädt nur zu unmöglichen Daten (30.02.) ein.
    vacation_carryover_expiry: this.fb.control('', { nonNullable: true }),
  });

  /**
   * „Kein Verfall" ist der **Default** und steht bewusst an erster Stelle. Es
   * wird nichts weggerechnet, was der Betrieb nicht ausdrücklich einstellt:
   * § 7 Abs. 3 BUrlG *erlaubt* den Verfall zum 31.03., er ordnet ihn nicht an,
   * und nach BAG/EuGH verfällt Urlaub nur, wenn der Arbeitgeber rechtzeitig
   * aufgefordert und belehrt hat.
   */
  protected readonly verfallOptionen: FeldOption[] = [
    { wert: '', label: 'Kein Verfall (Standard)' },
    { wert: '3-31', label: 'Verfall zum 31.03. des Folgejahres' },
    { wert: '6-30', label: 'Verfall zum 30.06. des Folgejahres' },
    { wert: '12-31', label: 'Verfall zum 31.12. des Folgejahres' },
  ];

  protected readonly skrOptionen: FeldOption[] = [
    { wert: 'SKR03', label: 'SKR03' },
    { wert: 'SKR04', label: 'SKR04' },
  ];

  protected readonly abschlagsModi: FeldOption[] = [
    { wert: 'ERLOES', label: 'Erlös (Teilleistung)' },
    { wert: 'ANZAHLUNG', label: 'Erhaltene Anzahlung (Verbindlichkeit)' },
  ];

  /** Anzahlungskonten sind nur im Modus ANZAHLUNG wirksam. */
  protected readonly anzahlungAktiv = signal(false);

  constructor() {
    this.laden();
    this.form.controls.datev_advance_mode.valueChanges
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((m) => this.anzahlungAktiv.set(m === 'ANZAHLUNG'));
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
      datev_consultant_number: p.datev_consultant_number ?? '',
      datev_client_number: p.datev_client_number ?? '',
      datev_chart_of_accounts: p.datev_chart_of_accounts ?? '',
      datev_account_length: p.datev_account_length?.toString() ?? '',
      datev_fiscal_year_start_month: p.datev_fiscal_year_start_month?.toString() ?? '',
      datev_debtor_account: p.datev_debtor_account ?? '',
      datev_revenue_account_full: p.datev_revenue_account_full ?? '',
      datev_revenue_account_reduced: p.datev_revenue_account_reduced ?? '',
      datev_revenue_account_free: p.datev_revenue_account_free ?? '',
      datev_revenue_account_reverse: p.datev_revenue_account_reverse ?? '',
      datev_advance_mode: p.datev_advance_mode ?? 'ERLOES',
      datev_advance_account_full: p.datev_advance_account_full ?? '',
      datev_advance_account_reduced: p.datev_advance_account_reduced ?? '',
      datev_advance_account_free: p.datev_advance_account_free ?? '',
      datev_advance_account_reverse: p.datev_advance_account_reverse ?? '',
      vacation_carryover_expiry:
        p.vacation_carryover_expiry_month && p.vacation_carryover_expiry_day
          ? `${p.vacation_carryover_expiry_month}-${p.vacation_carryover_expiry_day}`
          : '',
    });
    this.anzahlungAktiv.set((p.datev_advance_mode ?? 'ERLOES') === 'ANZAHLUNG');
  }

  absenden(): void {
    if (this.laedt() || !this.darfAendern()) return;
    serverFehlerZuruecksetzen(this.form);
    this.formularMeldung.set(null);
    this.erfolg.set(null);
    felderAlsBeruehrtMarkieren(this.form);
    if (this.form.invalid) return;

    const roh = this.form.getRawValue();
    const { vacation_carryover_expiry, ...rest } = roh;
    // „3-31" → Monat 3, Tag 31. Leer → beide null („kein Verfall"). Der Server
    // verlangt Tag UND Monat oder keins von beidem (DB-CHECK) — deshalb werden
    // sie hier immer als PAAR gesendet, nie einzeln.
    const [monat, tag] = vacation_carryover_expiry
      ? vacation_carryover_expiry.split('-').map(Number)
      : [null, null];
    const payload: CompanyProfileInput = {
      ...rest,
      // Zahlenfelder: leer → null (Feld löschen), sonst Ganzzahl. Der Server
      // erwartet hier int|null, kein Leerstring.
      datev_account_length: ganzzahlOderNull(roh.datev_account_length),
      datev_fiscal_year_start_month: ganzzahlOderNull(
        roh.datev_fiscal_year_start_month,
      ),
      vacation_carryover_expiry_month: monat,
      vacation_carryover_expiry_day: tag,
    };
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
