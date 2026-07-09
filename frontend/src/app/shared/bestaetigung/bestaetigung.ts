import { Component, computed, effect, input, output, signal } from '@angular/core';
import { Dialog } from '../dialog/dialog';

/**
 * Bestaetigungsdialog fuer irreversible bzw. folgenreiche Aktionen (Rechnung
 * veroeffentlichen, stornieren, Auftrag freigeben, Mitarbeiter austragen,
 * Kuendigung, Ablehnung …).
 *
 * Bewusst NICHT „Bestaetigen" als Standardfokus: ohne Pflicht-Begruendung
 * erhaelt „Abbrechen" den Startfokus; mit Begruendung das Textfeld. So loest
 * ein reflexartiges Enter nie die Aktion aus.
 *
 * ```html
 * <app-bestaetigung
 *   [offen]="fragen()"
 *   titel="Rechnung stornieren?"
 *   text="Die Rechnung wird storniert. Das lässt sich nicht rückgängig machen."
 *   bestaetigenLabel="Stornieren"
 *   [gefahr]="true"
 *   [begruendungPflicht]="true"
 *   [laedt]="laedt()"
 *   (bestaetigen)="stornieren($event)"
 *   (abbrechen)="fragen.set(false)"
 * />
 * ```
 */
@Component({
  selector: 'app-bestaetigung',
  imports: [Dialog],
  templateUrl: './bestaetigung.html',
  styleUrl: './bestaetigung.scss',
})
export class Bestaetigung {
  readonly offen = input(false);
  readonly titel = input('Aktion bestätigen');
  readonly text = input('');
  readonly bestaetigenLabel = input('Bestätigen');
  readonly abbrechenLabel = input('Abbrechen');
  /** Hebt den Bestaetigen-Knopf als folgenreich hervor (Amber statt Navy). */
  readonly gefahr = input(false);
  /** Blendet ein Pflicht-Begruendungsfeld ein; Bestaetigen bleibt bis dahin gesperrt. */
  readonly begruendungPflicht = input(false);
  readonly begruendungLabel = input('Begründung');
  /** Laufende Aktion: Knoepfe sperren, Text „Wird ausgeführt …". */
  readonly laedt = input(false);

  /** Bestaetigt; liefert die getrimmte Begruendung oder `null`. */
  readonly bestaetigen = output<string | null>();
  readonly abbrechen = output<void>();

  protected readonly begruendung = signal('');

  protected readonly kannBestaetigen = computed(
    () => !this.laedt() && (!this.begruendungPflicht() || this.begruendung().trim().length > 0),
  );

  constructor() {
    // Begruendung beim Schliessen zuruecksetzen, damit sie beim naechsten
    // Oeffnen leer ist.
    effect(() => {
      if (!this.offen()) this.begruendung.set('');
    });
  }

  protected onEingabe(wert: string): void {
    this.begruendung.set(wert);
  }

  protected onBestaetigen(): void {
    if (!this.kannBestaetigen()) return;
    const g = this.begruendung().trim();
    this.bestaetigen.emit(g.length ? g : null);
  }

  protected onAbbrechen(): void {
    if (this.laedt()) return;
    this.abbrechen.emit();
  }
}
