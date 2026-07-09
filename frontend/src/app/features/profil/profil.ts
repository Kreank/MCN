import { Component, computed, inject, signal } from '@angular/core';
import {
  AbstractControl,
  FormBuilder,
  ReactiveFormsModule,
  ValidationErrors,
  Validators,
} from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import { AuthService } from '../../core/auth.service';
import { feldFehlerText } from '../../shared/formular/feld-fehler';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';

/**
 * „Mein Profil" — read-only Stammdaten des angemeldeten Kontos plus
 * Passwortwechsel. Passwörter werden niemals geloggt, nie in Storage gehalten;
 * die Felder werden nach Erfolg sofort geleert. Die Sitzung bleibt gültig
 * (der Server ruft update_session_auth_hash) — kein erneutes Anmelden.
 *
 * Bewusst kein `shared/formular/Feld`: Passwörter brauchen `type=password`
 * samt korrektem `autocomplete` (current-/new-password), das die generische
 * Feld-Komponente nicht führt. Deshalb hier eigene Eingaben mit denselben
 * globalen `feld-gruppe`-Klassen und derselben Fehlerlogik (`feldFehlerText`).
 */
@Component({
  selector: 'app-profil',
  imports: [ReactiveFormsModule],
  templateUrl: './profil.html',
  styleUrl: './profil.scss',
})
export class Profil {
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  protected readonly user = this.auth.user;
  protected readonly rollenText = computed(() => {
    const rollen = this.user()?.roles ?? [];
    return rollen.length ? rollen.join(' · ') : 'Ohne Rolle';
  });

  protected readonly laedt = signal(false);
  protected readonly erfolg = signal<string | null>(null);
  protected readonly formularMeldung = signal<string | null>(null);

  protected readonly form = this.fb.group(
    {
      old_password: this.fb.control('', {
        nonNullable: true,
        validators: [Validators.required],
      }),
      new_password: this.fb.control('', {
        nonNullable: true,
        validators: [Validators.required, Validators.minLength(12)],
      }),
      new_password_repeat: this.fb.control('', {
        nonNullable: true,
        validators: [Validators.required],
      }),
    },
    { validators: [passwoerterGleich] },
  );

  /** Fehlertext eines Feldes (Server- oder Client-Validierung). */
  fehler(name: 'old_password' | 'new_password' | 'new_password_repeat'): string | null {
    return feldFehlerText(this.form.controls[name]);
  }

  /** Mismatch nur zeigen, wenn das Wiederholungsfeld berührt wurde. */
  ungleichSichtbar(): boolean {
    const rep = this.form.controls.new_password_repeat;
    return this.form.hasError('ungleich') && (rep.touched || rep.dirty);
  }

  /** Server-Fehler eines Feldes verschwindet, sobald der Nutzer es bearbeitet. */
  serverFehlerLoeschen(name: 'old_password' | 'new_password' | 'new_password_repeat'): void {
    const c = this.form.controls[name];
    const e = c.errors;
    if (e && e['server'] != null) {
      const { server, ...rest } = e as Record<string, unknown>;
      c.setErrors(Object.keys(rest).length ? rest : null);
    }
  }

  absenden(): void {
    if (this.laedt()) return;
    serverFehlerZuruecksetzen(this.form);
    this.formularMeldung.set(null);
    this.erfolg.set(null);
    felderAlsBeruehrtMarkieren(this.form);
    if (this.form.invalid) return;

    const { old_password, new_password } = this.form.getRawValue();
    this.laedt.set(true);
    this.auth.passwortAendern(old_password, new_password).subscribe({
      next: () => {
        this.laedt.set(false);
        // Klartext-Passwörter sofort aus dem Formular entfernen.
        this.form.reset({ old_password: '', new_password: '', new_password_repeat: '' });
        this.erfolg.set('Ihr Passwort wurde geändert. Die Sitzung bleibt bestehen.');
      },
      error: (err: unknown) => {
        this.laedt.set(false);
        this.fehlerZuordnen(err);
      },
    });
  }

  /**
   * 400 (altes Passwort falsch) auf das alte-Passwort-Feld; 422 (zu schwach)
   * mit den deutschen Validator-Meldungen aufs neue Passwort; sonst allgemeine
   * Formularmeldung.
   */
  private fehlerZuordnen(err: unknown): void {
    if (err instanceof HttpErrorResponse) {
      const detail = (err.error as { detail?: unknown } | null)?.detail;
      const text = typeof detail === 'string' && detail.trim() ? detail : null;
      if (err.status === 400) {
        const c = this.form.controls.old_password;
        c.setErrors({ ...(c.errors ?? {}), server: text ?? 'Das aktuelle Passwort ist falsch.' });
        c.markAsTouched();
        return;
      }
      if (err.status === 422) {
        const c = this.form.controls.new_password;
        c.setErrors({
          ...(c.errors ?? {}),
          server: text ?? 'Das neue Passwort erfüllt die Vorgaben nicht.',
        });
        c.markAsTouched();
        return;
      }
      if (err.status === 0) {
        this.formularMeldung.set('Keine Verbindung zum Server. Bitte erneut versuchen.');
        return;
      }
      if (text) {
        this.formularMeldung.set(text);
        return;
      }
    }
    this.formularMeldung.set('Das Passwort konnte nicht geändert werden. Bitte erneut versuchen.');
  }
}

/** Gruppen-Validator: neues Passwort und Wiederholung müssen übereinstimmen. */
function passwoerterGleich(group: AbstractControl): ValidationErrors | null {
  const np = group.get('new_password')?.value;
  const wh = group.get('new_password_repeat')?.value;
  if (wh && np !== wh) return { ungleich: true };
  return null;
}
