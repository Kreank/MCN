import { Component, computed, inject, input, output } from '@angular/core';
import {
  AbstractControl,
  FormControl,
  FormGroup,
  ReactiveFormsModule,
  ValidationErrors,
  Validators,
} from '@angular/forms';
import { Dialog } from '../dialog/dialog';
import { PreisKlaerung as PreisKlaerungPos, PreisVorschlag } from '../../core/beleg.model';
import {
  DEZIMAL_UNGUELTIG,
  apiZuDeAnzeige,
  apiZuDeEingabe,
  deZuApiDezimal,
  dezimalValidator,
} from '../formular/dezimal';
import { feldFehlerText } from '../formular/feld-fehler';
import { felderAlsBeruehrtMarkieren } from '../formular/formular.util';

/**
 * Ein Preis ist erst ab **> 0** ein Preis (Server: `abrechnung._ist_preis`).
 *
 * Eine 0 im Eingabefeld wäre eine Gratisleistung — genau der stille Fehler, den
 * dieser ganze Dialog verhindern soll. Der Server lehnt sie ab; das UI tut es
 * schon vorher, damit der Nutzer nicht erst in einen 422 läuft.
 *
 * Der Fehlerschlüssel `min` ist bewusst gewählt: `feldFehlerText` hat dafür
 * bereits eine deutsche Meldung, es entsteht kein zweiter Meldungskanal.
 */
function preisPositivValidator(control: AbstractControl): ValidationErrors | null {
  const api = deZuApiDezimal(control.value);
  if (api === '' || api === DEZIMAL_UNGUELTIG) return null; // andere Validatoren
  return Number(api) > 0 ? null : { min: { min: '0,01 €' } };
}

/**
 * Die **Preisklärungs-Maske**: Ein 422 mit `preis_unbekannt` ist kein roter
 * Fehlerbalken, sondern eine Aufgabe.
 *
 * Der Server rechnet jede Geldzahl — aber für manche Positionen hat er schlicht
 * **keinen** Preis (kein EK, keine VK-Regel, keine Lohngruppe, oder eine 0,00 €,
 * die nur wie ein Preis aussieht). Dann wird die Position **weder mit 0,00 €
 * abgerechnet noch stillschweigend weggelassen** — eine zu niedrige Rechnung, die
 * plausibel aussieht, ist der teuerste Fehler dieses Systems. Stattdessen nennt
 * hier ein Mensch den Einzelpreis, und derselbe Aufruf geht mit `preise` erneut
 * hinaus.
 *
 * Die drei Regeln dieses Dialogs:
 *
 * 1. **Kein „später"-Knopf.** Entweder Preis nennen oder abbrechen. Ein Knopf,
 *    der die Position weglässt, wäre der Weg zurück in die stille Unterfakturierung.
 * 2. **Vorschläge werden NIE vorausgefüllt.** Sie sind anklickbare Lesehilfen
 *    („zuletzt berechnet: 42,00 €") und als Vorschlag gekennzeichnet. Der Mensch
 *    entscheidet — ein vorbelegtes Feld entscheidet für ihn.
 * 3. **Keine Geldberechnung im Client.** Genannt wird nur der Einzelpreis;
 *    Zeilensumme, Steuer und Gesamt rechnet weiterhin ausschließlich der Server.
 *
 * Eingabefelder tragen `apiZuDeEingabe` (**ohne** Tausenderpunkt); nur die reine
 * Anzeige der Vorschläge nutzt `apiZuDeAnzeige` — beides nicht vermischen (der
 * gruppierte Wert wäre beim Zurücklesen mehrdeutig).
 */
@Component({
  selector: 'app-preis-klaerung',
  imports: [Dialog, ReactiveFormsModule],
  templateUrl: './preis-klaerung.html',
  styleUrl: './preis-klaerung.scss',
})
export class PreisKlaerung {
  readonly offen = input(false);
  readonly positionen = input<PreisKlaerungPos[]>([]);
  readonly laedt = input(false);
  /** Fehlermeldung des letzten Versuchs (z. B. ein 422 ohne Klärungsliste). */
  readonly fehler = input<string | null>(null);

  /** {quelle_id → Einzelpreis als API-Punkt-String}. Vollständig, nie lückenhaft. */
  readonly absenden = output<Record<string, string>>();
  readonly abbrechen = output<void>();

  /**
   * Je Klärungsposition genau ein Pflichtfeld. Neu aufgebaut, sobald sich die
   * Positionsliste ändert (ein zweiter Lauf kann neue Lücken aufdecken) — ein
   * Wert aus einer alten Liste darf nicht stehen bleiben.
   */
  protected readonly form = computed(() => {
    const grp = new FormGroup<Record<string, FormControl<string>>>({});
    for (const p of this.positionen()) {
      grp.addControl(
        p.quelle_id,
        new FormControl('', {
          nonNullable: true,
          validators: [Validators.required, dezimalValidator, preisPositivValidator],
        }),
      );
    }
    return grp;
  });

  protected feld(quelleId: string): FormControl<string> | null {
    return this.form().controls[quelleId] ?? null;
  }

  protected fehlerText(quelleId: string): string | null {
    return feldFehlerText(this.feld(quelleId));
  }

  /** Ein **Vorschlag** — anklickbar, nie automatisch gesetzt. */
  protected vorschlagUebernehmen(quelleId: string, v: PreisVorschlag): void {
    const c = this.feld(quelleId);
    if (!c || this.laedt()) return;
    c.setValue(apiZuDeEingabe(v.betrag, 2));
    c.markAsDirty();
    c.markAsTouched();
  }

  protected vorschlagLabel(v: PreisVorschlag): string {
    const art =
      v.art === 'LETZTER_PREIS'
        ? 'letzter berechneter Preis'
        : v.art === 'LISTENPREIS'
          ? 'Listenpreis'
          : 'Lohngruppe';
    return `${art}: ${this.euro(v.betrag)}`;
  }

  /** Reine Anzeige (Tausenderpunkt erlaubt) — nie in ein Eingabefeld. */
  protected euro(betrag: string | null): string {
    if (betrag === null || betrag === '') return '—';
    return `${apiZuDeAnzeige(betrag, 2)} €`;
  }

  protected menge(p: PreisKlaerungPos): string {
    if (p.menge === null) return '—';
    const zahl = apiZuDeAnzeige(p.menge, undefined);
    return p.einheit ? `${zahl} ${p.einheit}` : zahl;
  }

  /** Einheit des Einzelpreises: bei einer Zeitgruppe ein Stundensatz. */
  protected preisLabel(p: PreisKlaerungPos): string {
    return p.quelle_art === 'ZEITGRUPPE' ? 'Stundensatz (€/h)' : 'Einzelpreis (€)';
  }

  protected onAbsenden(): void {
    if (this.laedt()) return;
    const form = this.form();
    felderAlsBeruehrtMarkieren(form);
    if (form.invalid) return;

    const preise: Record<string, string> = {};
    for (const p of this.positionen()) {
      const api = deZuApiDezimal(form.controls[p.quelle_id]?.value ?? '');
      // Fail-loud: eine unlesbare Zahl geht NICHT als leeres Feld hinaus (der
      // Validator hat sie eigentlich schon abgefangen).
      if (api === '' || api === DEZIMAL_UNGUELTIG) return;
      preise[p.quelle_id] = api;
    }
    this.absenden.emit(preise);
  }

  protected onAbbrechen(): void {
    if (!this.laedt()) this.abbrechen.emit();
  }
}
