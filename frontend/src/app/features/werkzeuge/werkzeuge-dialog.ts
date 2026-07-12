import { Component, computed, model, output, signal } from '@angular/core';
import { Dialog } from '../../shared/dialog/dialog';
import { HeizlastRechner } from './heizlast-rechner';
import { EinheitenUmrechner } from './einheiten-umrechner';
import { VolumenstromRechner } from './volumenstrom-rechner';
import { HeizkoerperRechner } from './heizkoerper-rechner';
import { WERKZEUGE } from './werkzeuge';

/**
 * Die Werkzeuge als Dialog — für den Beleg-Editor. Das Ergebnis wird als
 * **Textzeile** zurückgegeben (`uebernehmen`), nie als Menge oder Preis: der
 * Editor rechnet keine Summen, der Server bleibt die verbindliche Rechenstelle.
 */
@Component({
  selector: 'app-werkzeuge-dialog',
  imports: [Dialog, HeizlastRechner, EinheitenUmrechner, VolumenstromRechner, HeizkoerperRechner],
  templateUrl: './werkzeuge-dialog.html',
  styleUrl: './werkzeuge-dialog.scss',
})
export class WerkzeugeDialog {
  /** Zwei-Wege-Bindung: der Editor besitzt den Zustand. */
  readonly offen = model(false);
  /** Freier Kontext (z. B. Belegtitel) für die Ausgabe. */
  readonly kontext = model('');
  /** Einzeiliger Ergebnistext für eine Textposition. */
  readonly uebernehmen = output<string>();

  protected readonly tabs = WERKZEUGE;
  protected readonly aktiv = signal(WERKZEUGE[0].id);
  protected readonly aktiverTab = computed(
    () => this.tabs.find((t) => t.id === this.aktiv()) ?? this.tabs[0],
  );

  protected onTabKey(event: KeyboardEvent, index: number): void {
    const letzter = this.tabs.length - 1;
    let ziel: number | null = null;
    if (event.key === 'ArrowRight') ziel = index === letzter ? 0 : index + 1;
    else if (event.key === 'ArrowLeft') ziel = index === 0 ? letzter : index - 1;
    else if (event.key === 'Home') ziel = 0;
    else if (event.key === 'End') ziel = letzter;
    if (ziel === null) return;
    event.preventDefault();
    const id = this.tabs[ziel].id;
    this.aktiv.set(id);
    queueMicrotask(() => document.getElementById(`wzd-tab-${id}`)?.focus());
  }

  /** Ergebnis weiterreichen und den Dialog schließen. */
  protected weitergeben(text: string): void {
    this.uebernehmen.emit(text);
    this.offen.set(false);
  }
}
