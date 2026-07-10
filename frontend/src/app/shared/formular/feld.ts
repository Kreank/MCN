import { Component, computed, input } from '@angular/core';
import { FormControl, ReactiveFormsModule } from '@angular/forms';
import { feldFehlerText } from './feld-fehler';

export interface FeldOption {
  wert: string;
  label: string;
}

export type FeldTyp =
  | 'text'
  | 'email'
  | 'tel'
  | 'password'
  | 'textarea'
  | 'zahl'
  | 'datum'
  | 'select'
  | 'checkbox';

let feldSeq = 0;

/**
 * Ein Formularfeld mit sichtbarem Label, Pflicht-Kennzeichnung (nicht nur
 * farblich), optionalem Hinweis und barrierefreier Fehleranzeige
 * (`aria-describedby` + `aria-invalid`). Basiert auf Reactive Forms: der
 * Aufrufer uebergibt ein `FormControl`.
 *
 * Typen: text/email/tel, textarea, zahl (Dezimal als String, deutsche
 * Komma-Eingabe, `inputmode=decimal`), datum, select, checkbox.
 *
 * ```html
 * <app-feld [control]="form.controls.titel" label="Titel" [pflicht]="true" />
 * <app-feld [control]="form.controls.betrag" label="Betrag" typ="zahl"
 *           hinweis="Betrag in Euro, z. B. 1.234,56" />
 * <app-feld [control]="form.controls.art" label="Art" typ="select"
 *           platzhalter="Bitte wählen" [optionen]="arten" />
 * ```
 * Fuer Dezimalfelder gilt: das Control haelt den String wie eingegeben; vor
 * dem Senden mit `deZuApiDezimal()` (aus `dezimal.ts`) in den Punkt-String
 * wandeln.
 */
@Component({
  selector: 'app-feld',
  imports: [ReactiveFormsModule],
  templateUrl: './feld.html',
})
export class Feld {
  readonly control = input.required<FormControl>();
  readonly label = input('');
  readonly typ = input<FeldTyp>('text');
  readonly pflicht = input(false);
  readonly hinweis = input<string | null>(null);
  readonly platzhalter = input('');
  readonly optionen = input<FeldOption[]>([]);
  readonly autocomplete = input('');
  readonly zeilen = input(3);
  /** Ueberschreibt `inputmode`; bei typ 'zahl' ist 'decimal' voreingestellt. */
  readonly inputmode = input<string | null>(null);

  protected readonly id = `feld-${++feldSeq}`;
  protected readonly hinweisId = `${this.id}-hinweis`;
  protected readonly fehlerId = `${this.id}-fehler`;

  protected readonly inputTyp = computed(() => {
    const t = this.typ();
    if (t === 'datum') return 'date';
    if (t === 'zahl') return 'text'; // text statt number: Komma-Eingabe, String-Wert
    return t; // text | email | tel | password
  });

  protected readonly inputmodeAttr = computed(() => {
    if (this.typ() === 'zahl') return this.inputmode() ?? 'decimal';
    return this.inputmode();
  });

  /** Frisch je Change-Detection ausgewertet (Methode, kein memoisiertes Signal). */
  protected fehlerText(): string | null {
    return feldFehlerText(this.control());
  }

  protected beschriebenVon(): string | null {
    const ids: string[] = [];
    if (this.hinweis()) ids.push(this.hinweisId);
    if (this.fehlerText()) ids.push(this.fehlerId);
    return ids.length ? ids.join(' ') : null;
  }

  /** Server-Fehler verschwindet, sobald der Nutzer das Feld bearbeitet. */
  protected serverFehlerLoeschen(): void {
    const c = this.control();
    const e = c.errors;
    if (e && e['server'] != null) {
      const { server, ...rest } = e as Record<string, unknown>;
      c.setErrors(Object.keys(rest).length ? rest : null);
    }
  }
}
