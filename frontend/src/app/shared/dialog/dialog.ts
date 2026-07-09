import {
  Component,
  ElementRef,
  OnDestroy,
  effect,
  input,
  output,
  viewChild,
} from '@angular/core';

/** Selektor fuer das erste sinnvoll fokussierbare Element im Dialoginhalt. */
const FOKUSSIERBAR =
  'input:not([disabled]),select:not([disabled]),textarea:not([disabled]),' +
  'button:not([disabled]),a[href],[tabindex]:not([tabindex="-1"])';

let dialogSeq = 0;
/** Anzahl gerade offener Dialoge — der Scroll-Lock wird erst beim letzten geloest. */
let offeneDialoge = 0;

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

  private oeffnen(el: HTMLDialogElement): void {
    if (typeof el.showModal === 'function') el.showModal();
    else el.setAttribute('open', ''); // Fallback (z. B. Test-DOM)
    offeneDialoge += 1;
    document.body.style.overflow = 'hidden';
    // Startfokus nach dem Rendern setzen: erstes Feld im Inhalt (nicht der
    // Schliessen-Knopf) bzw. ein explizit markiertes Element, sonst der Titel.
    queueMicrotask(() => this.startfokus(el));
  }

  private schliessenIntern(el: HTMLDialogElement): void {
    if (typeof el.close === 'function') el.close();
    else el.removeAttribute('open');
    offeneDialoge = Math.max(0, offeneDialoge - 1);
    if (offeneDialoge === 0) document.body.style.overflow = '';
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
