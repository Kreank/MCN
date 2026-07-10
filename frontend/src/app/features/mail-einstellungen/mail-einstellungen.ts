import { Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { AuthService } from '../../core/auth.service';
import { MailService } from '../../core/mail.service';
import { MailAccount, MailAccountInput } from '../../core/mail.model';
import { Feld, FeldOption } from '../../shared/formular/feld';
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
  | { kind: 'ready'; data: MailAccount }
  | VerbotenState
  | { kind: 'error' };

/**
 * Mailversand — SMTP-Absenderkonto konfigurieren und eine Testmail senden.
 *
 * Das Passwort ist **write-only**: es wird nie angezeigt (der Server liefert es
 * nie aus). Ist eins hinterlegt, steht im Feld der Platzhalter „•••• unverändert";
 * leer lassen = unverändert, nur ein eingegebener Wert wird gesendet. Ohne
 * `company/AENDERN` ist alles schreibgeschützt (der Server setzt das ohnehin
 * durch).
 */
@Component({
  selector: 'app-mail-einstellungen',
  imports: [ReactiveFormsModule, Feld, EinstellungenNav, KeinZugriff],
  templateUrl: './mail-einstellungen.html',
  styleUrl: './mail-einstellungen.scss',
})
export class MailEinstellungen {
  private readonly svc = inject(MailService);
  private readonly fb = inject(FormBuilder);
  private readonly auth = inject(AuthService);

  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly laedt = signal(false);
  protected readonly erfolg = signal<string | null>(null);
  protected readonly formularMeldung = signal<string | null>(null);
  protected readonly hatPasswort = signal(false);

  // Testmail
  protected readonly testLaedt = signal(false);
  protected readonly testErfolg = signal<string | null>(null);
  protected readonly testFehler = signal<string | null>(null);

  protected readonly darfAendern = computed(() => this.auth.darf('company', 'AENDERN'));

  protected readonly sicherheitOptionen: FeldOption[] = [
    { wert: 'NONE', label: 'Keine (unverschlüsselt)' },
    { wert: 'STARTTLS', label: 'STARTTLS' },
    { wert: 'SSL', label: 'SSL/TLS' },
  ];

  protected readonly form = this.fb.group({
    label: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    host: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    port: this.fb.control('587', { nonNullable: true, validators: [Validators.required] }),
    security: this.fb.control('STARTTLS', { nonNullable: true, validators: [Validators.required] }),
    username: this.fb.control('', { nonNullable: true }),
    password: this.fb.control('', { nonNullable: true }),
    from_address: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.email],
    }),
    from_name: this.fb.control('', { nonNullable: true }),
  });

  protected readonly testAdresse = this.fb.control('', {
    nonNullable: true,
    validators: [Validators.required, Validators.email],
  });

  constructor() {
    this.laden();
  }

  private laden(): void {
    this.state.set({ kind: 'loading' });
    this.svc.getAccount().subscribe({
      next: (a) => {
        this.füllen(a);
        this.state.set({ kind: 'ready', data: a });
        if (!this.darfAendern()) {
          this.form.disable();
          this.testAdresse.disable();
        }
      },
      error: (err: unknown) => this.state.set(fehlerState(err)),
    });
  }

  private füllen(a: MailAccount): void {
    this.hatPasswort.set(a.has_password);
    this.form.patchValue({
      label: a.label ?? '',
      host: a.host ?? '',
      port: a.port != null ? String(a.port) : '587',
      security: a.security ?? 'STARTTLS',
      username: a.username ?? '',
      password: '', // write-only: nie befüllen
      from_address: a.from_address ?? '',
      from_name: a.from_name ?? '',
    });
  }

  /** Platzhalter fürs Passwortfeld: Hinweis, dass ein Passwort hinterlegt ist. */
  protected passwortPlatzhalter(): string {
    return this.hatPasswort() ? '•••• unverändert' : '';
  }

  absenden(): void {
    if (this.laedt() || !this.darfAendern()) return;
    serverFehlerZuruecksetzen(this.form);
    this.formularMeldung.set(null);
    this.erfolg.set(null);
    felderAlsBeruehrtMarkieren(this.form);
    if (this.form.invalid) return;

    const roh = this.form.getRawValue();
    const payload: MailAccountInput = {
      label: roh.label,
      host: roh.host,
      port: Number(roh.port),
      security: roh.security as MailAccountInput['security'],
      username: roh.username || null,
      from_address: roh.from_address,
      from_name: roh.from_name || null,
    };
    // Passwort nur senden, wenn eingegeben (leer = unverändert lassen).
    if (roh.password) payload.password = roh.password;

    this.laedt.set(true);
    this.svc.saveAccount(payload).subscribe({
      next: (a) => {
        this.laedt.set(false);
        this.füllen(a);
        this.state.set({ kind: 'ready', data: a });
        this.erfolg.set('Das Absenderkonto wurde gespeichert.');
      },
      error: (err: unknown) => {
        this.laedt.set(false);
        const ergebnis = apiFehlerZuweisen(err, this.form);
        this.formularMeldung.set(ergebnis.formular);
      },
    });
  }

  testSenden(): void {
    if (this.testLaedt() || !this.darfAendern()) return;
    this.testErfolg.set(null);
    this.testFehler.set(null);
    this.testAdresse.markAsTouched();
    if (this.testAdresse.invalid) {
      this.testFehler.set('Bitte eine gültige Ziel-Adresse angeben.');
      return;
    }
    const ziel = this.testAdresse.getRawValue();
    this.testLaedt.set(true);
    this.svc.sendTest(ziel).subscribe({
      next: () => {
        this.testLaedt.set(false);
        this.testErfolg.set(`Testmail an ${ziel} versendet.`);
      },
      error: (err: unknown) => {
        this.testLaedt.set(false);
        const detail =
          err && typeof err === 'object' && 'error' in err
            ? (err as { error?: { detail?: string } }).error?.detail
            : null;
        this.testFehler.set(
          detail || 'Die Testmail konnte nicht versendet werden. Bitte die Zugangsdaten prüfen.',
        );
      },
    });
  }
}
