import {
  Component,
  ElementRef,
  OnDestroy,
  effect,
  input,
  output,
  viewChild,
} from '@angular/core';
import { panelOeffnen } from '../schwebendes-panel';

/** Selektor fuer das erste sinnvoll fokussierbare Element im Dialoginhalt. */
const FOKUSSIERBAR =
  'input:not([disabled]),select:not([disabled]),textarea:not([disabled]),' +
  'button:not([disabled]),a[href],[tabindex]:not([tabindex="-1"])';

let dialogSeq = 0;
/** Anzahl gerade offener Dialoge — der Scroll-Lock wird erst beim letzten geloest. */
let offeneDialoge = 0;

/**
 * Body-Scroll sperren. Referenzgezaehlt: Wer sperrt, muss genau einmal
 * `scrollFreigeben()` aufrufen.
 *
 * Exportiert, damit ALLE modalen Schichten (dieser Dialog, die Kommandopalette
 * mit ihrem eigenen `<dialog>`-Kern) denselben Zaehler teilen. Mit zwei
 * getrennten Zaehlern wuerde die zuerst geschlossene Schicht den Scroll wieder
 * freigeben, obwohl die andere noch offen ist.
 */
export function scrollSperren(): void {
  offeneDialoge += 1;
  document.body.style.overflow = 'hidden';
}

/** Gegenstueck zu `scrollSperren()`; loest erst beim letzten Halter. */
export function scrollFreigeben(): void {
  offeneDialoge = Math.max(0, offeneDialoge - 1);
  if (offeneDialoge === 0) document.body.style.overflow = '';
}

/**
 * Generische, barrierefreie Dialog-Huelle auf Basis des nativen
 * `<dialog>`-Elements (kein Angular Material, keine Fremd-Abhaengigkeit).
 *
 * Das native `showModal()` liefert von sich aus: Rendern in der Top-Layer
 * (nicht abgeschnitten), Fokus-Trap (Tab/Shift+Tab bleiben im Dialog), den
 * Hintergrund `inert` und Fokus-Rueckgabe an das ausloesende Element beim
 * Schliessen. Ergaenzt wird: expliziter Startfokus (erstes Feld statt
 * Schliessen-Knopf), Backdrop-Klick, Escape und Body-Scroll-Lock — jeweils
 * abschaltbar fuer „gefaehrliche" Dialoge.
 *
 * Der Eltern-Teil besitzt den `offen`-Zustand (Signal-Input); der Dialog meldet
 * ueber `(schliessen)` nur den Wunsch zu schliessen und setzt den Zustand nie
 * selbst — so bleibt eine einzige Quelle der Wahrheit.
 *
 * Verwendung:
 * ```html
 * <app-dialog [offen]="offen()" titel="Titel" (schliessen)="offen.set(false)">
 *   <p>Inhalt …</p>
 *   <div dialog-aktionen class="dialog__aktionen">
 *     <button class="btn" (click)="offen.set(false)">Abbrechen</button>
 *     <button class="btn btn--primary" (click)="…">OK</button>
 *   </div>
 * </app-dialog>
 * ```
 */
@Component({
  selector: 'app-dialog',
  imports: [],
  templateUrl: './dialog.html',
  styleUrl: './dialog.scss',
})
export class Dialog implements OnDestroy {
  /** Sichtbarkeit; der Eltern-Teil steuert Auf/Zu. */
  readonly offen = input(false);
  /** Ueberschrift; leer -> kein Kopf, kein aria-labelledby. */
  readonly titel = input('');
  /**
   * Breiter Zuschnitt fuer Formulare mit Zeilen (Positionen, Beteiligte).
   *
   * Die schmale Grundform (34rem) ist fuer kurze Dialoge richtig. Sobald ein
   * Formular mehrere Felder NEBENEINANDER braucht, quetscht sie sie zu einer
   * Spalte und erzeugt Scrollen — dann ist der breite Zuschnitt der bessere.
   */
  readonly breit = input(false);
  /** Escape schliesst (fuer irreversible Dialoge abschaltbar). */
  readonly escapeSchliesst = input(true);
  /** Klick auf den Hintergrund schliesst (abschaltbar). */
  readonly backdropSchliesst = input(true);
  /** Schliessen-Wunsch (Escape, Backdrop, X-Knopf). */
  readonly schliessen = output<void>();

  protected readonly titelId = `dialog-titel-${++dialogSeq}`;

  private readonly dlg = viewChild<ElementRef<HTMLDialogElement>>('dlg');

  constructor() {
    // Zustand -> natives showModal()/close() spiegeln.
    effect(() => {
      const offen = this.offen();
      const el = this.dlg()?.nativeElement;
      if (!el) return;
      if (offen && !el.open) this.oeffnen(el);
      else if (!offen && el.open) this.schliessenIntern(el);
    });
  }

  ngOnDestroy(): void {
    // Wird die Komponente zerstört, während der Dialog offen ist (Navigation,
    // @if am Eltern-Element), bliebe der Zähler stehen und der Body für immer
    // gesperrt. Der Zustand wird deshalb hier aufgeräumt.
    const el = this.dlg()?.nativeElement;
    if (el?.open) this.schliessenIntern(el);
  }

  /**
   * Wer den Dialog geoeffnet hat — fuer die Fokusrueckgabe beim Schliessen.
   *
   * Das native `<dialog>` gibt den Fokus zwar selbst zurueck, aber nur, wenn
   * der Ausloeser noch fokussierbar IST. Liegt er in einem eingeklappten
   * Hover-Panel (Plantafel-Kachel: geschlossenes Popover ist `display: none`),
   * verpufft die native Rueckgabe und der Fokus faellt in den `<body>` — der
   * Nutzer steht am Seitenanfang statt an der Kachel, an der er war
   * (WCAG 2.4.3). Deshalb wird der Ausloeser hier selbst gemerkt und sein
   * Panel vor dem Fokussieren wieder geoeffnet.
   */
  private ausloeser: HTMLElement | null = null;

  private oeffnen(el: HTMLDialogElement): void {
    const aktiv = document.activeElement;
    this.ausloeser = aktiv instanceof HTMLElement ? aktiv : null;
    if (typeof el.showModal === 'function') el.showModal();
    else el.setAttribute('open', ''); // Fallback (z. B. Test-DOM)
    scrollSperren();
    // Startfokus nach dem Rendern setzen: erstes Feld im Inhalt (nicht der
    // Schliessen-Knopf) bzw. ein explizit markiertes Element, sonst der Titel.
    queueMicrotask(() => this.startfokus(el));
  }

  private schliessenIntern(el: HTMLDialogElement): void {
    if (typeof el.close === 'function') el.close();
    else el.removeAttribute('open');
    scrollFreigeben();
    const ziel = this.ausloeser;
    this.ausloeser = null;
    if (!ziel?.isConnected) return;
    panelOeffnen(ziel);
    ziel.focus();
  }

  private startfokus(el: HTMLDialogElement): void {
    if (!el.open) return;
    const explizit = el.querySelector<HTMLElement>('[data-startfokus]');
    const inhalt = el.querySelector('.dialog__inhalt');
    const erstesFeld = inhalt?.querySelector<HTMLElement>(FOKUSSIERBAR) ?? null;
    const titel = el.querySelector<HTMLElement>('.dialog__titel');
    (explizit ?? erstesFeld ?? titel)?.focus();
  }

  /** Escape: nativ abfangen; der Eltern-Teil entscheidet ueber den Zustand. */
  protected onCancel(event: Event): void {
    event.preventDefault();
    if (this.escapeSchliesst()) this.schliessen.emit();
  }

  /** Klick auf die abgedunkelte Flaeche (Ziel == Dialogelement selbst). */
  protected onKlick(event: MouseEvent): void {
    if (this.backdropSchliesst() && event.target === this.dlg()?.nativeElement) {
      this.schliessen.emit();
    }
  }
}
