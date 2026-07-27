import {
  Component,
  ElementRef,
  Injector,
  OnDestroy,
  afterNextRender,
  effect,
  inject,
  input,
  output,
  signal,
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
 * ## Eingaben gehen nicht durch einen Fehlklick verloren
 *
 * Saschas Befund beim Testen: *„Wenn ich mich verklicke, wird das Formular
 * geschlossen. Total uncool, wenn man dabei ist etwas anzulegen."* Er hat recht:
 * Ein Klick auf die abgedunkelte Flaeche ist der haeufigste Fehlklick
 * ueberhaupt, und er loeschte die halbe Erfassung.
 *
 * Der Dialog merkt sich deshalb selbst, ob **im Inhalt getippt wurde** (ein
 * `input`- oder `change`-Ereignis aus dem Inhaltsbereich). Danach gilt:
 *
 * | Geste | unberuehrt | mit Eingaben |
 * |---|---|---|
 * | Klick auf den Hintergrund | schliesst | **schliesst nicht**, kurzer Hinweis |
 * | Escape / X-Knopf | schliesst | fragt nach: *verwerfen oder weiter?* |
 *
 * Der Unterschied ist Absicht: Ein Klick daneben ist ein Versehen und wird
 * schlicht ignoriert; Escape und der X-Knopf sind gezielte Gesten und bekommen
 * eine Antwortmoeglichkeit statt einer Blockade. Escape bleibt damit ein Weg
 * hinaus (WCAG 2.1.2), nur eben ein bestaetigter — und das ist genau die
 * Absicherung gegen Datenverlust, die WCAG 2.2 (3.3.4/3.3.6) verlangt.
 *
 * Das braucht **keine Mitarbeit der Aufrufer**: kein zusaetzlicher Input, kein
 * „dirty"-Flag, das jemand vergessen kann. Dialoge ohne Eingabefelder
 * (Bestaetigungen, Infotexte) verhalten sich unveraendert — dort gibt es nichts
 * zu verlieren.
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
   * Zuschnitt des Dialogs. Saschas Vorgabe: *„Muss nicht so ein dünner Schlauch
   * sein — wir haben ja Platz."*
   *
   * * `schmal` (32rem) — eine Frage, zwei Knoepfe. Bestaetigungen.
   * * `normal` (46rem, Standard) — das uebliche Erfassungsformular. Frueher
   *   34rem; das quetschte jede zweispaltige Zeile in eine Spalte und erzeugte
   *   Scrollen, wo Platz war.
   * * `breit` (64rem) — Formulare mit Zeilen (Beteiligte, Positionen), bei
   *   denen mehrere Felder nebeneinander gehoeren.
   *
   * Auf schmalen Schirmen greift ohnehin `width: 100%` — die Stufen wirken nur
   * dort, wo Platz da ist.
   */
  readonly weite = input<'schmal' | 'normal' | 'breit'>('normal');
  /** Escape schliesst (fuer irreversible Dialoge abschaltbar). */
  readonly escapeSchliesst = input(true);
  /** Klick auf den Hintergrund schliesst (abschaltbar). */
  readonly backdropSchliesst = input(true);
  /** Schliessen-Wunsch (Escape, Backdrop, X-Knopf). */
  readonly schliessen = output<void>();

  protected readonly titelId = `dialog-titel-${++dialogSeq}`;
  protected readonly frageId = `dialog-frage-${dialogSeq}`;

  private readonly dlg = viewChild<ElementRef<HTMLDialogElement>>('dlg');
  private readonly weiterBtn = viewChild<ElementRef<HTMLButtonElement>>('weiterBtn');

  /** Wurde im Inhalt getippt? Ab dann sind Eingaben zu schuetzen. */
  protected readonly beruehrt = signal(false);
  /** „Eingaben verwerfen?" — die Rueckfrage auf Escape/X. */
  protected readonly frageOffen = signal(false);
  /** Kurzer Hinweis nach einem Klick daneben. */
  protected readonly hinweisSichtbar = signal(false);
  private hinweisTimer: ReturnType<typeof setTimeout> | null = null;

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

  private readonly injector = inject(Injector);

  ngOnDestroy(): void {
    // Wird die Komponente zerstört, während der Dialog offen ist (Navigation,
    // @if am Eltern-Element), bliebe der Zähler stehen und der Body für immer
    // gesperrt. Der Zustand wird deshalb hier aufgeräumt.
    if (this.hinweisTimer !== null) clearTimeout(this.hinweisTimer);
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
    // Ein frisch geöffneter Dialog hat noch nichts zu verlieren.
    this.beruehrt.set(false);
    this.frageOffen.set(false);
    this.hinweisAbraeumen();
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
    // IMMER abfangen: Ohne `preventDefault` schloesse das native <dialog> sich
    // selbst — der Zustand des Eltern-Teils bliebe auf „offen" stehen.
    event.preventDefault();
    // Steht die Rueckfrage, nimmt Escape zuerst SIE zurueck. Sonst waere die
    // Frage „verwerfen?" mit derselben Taste beantwortbar, die sie ausgeloest
    // hat — und ein zweiter Tastendruck loeschte die Eingaben doch.
    if (this.frageOffen()) {
      this.frageOffen.set(false);
      return;
    }
    if (this.escapeSchliesst()) this.schliessenAnfordern();
  }

  /**
   * Klick auf die abgedunkelte Flaeche (Ziel == Dialogelement selbst).
   *
   * Mit Eingaben im Formular schliesst er **nicht**. Ein Klick daneben ist der
   * haeufigste Fehlklick, und er kostete bis hierher die ganze Erfassung.
   */
  protected onKlick(event: MouseEvent): void {
    if (event.target !== this.dlg()?.nativeElement) return;
    if (!this.backdropSchliesst()) return;
    if (this.beruehrt()) {
      this.hinweisZeigen();
      return;
    }
    this.schliessen.emit();
  }

  /** X-Knopf und Escape: gezielte Gesten — sie duerfen fragen, statt zu blocken. */
  protected schliessenAnfordern(): void {
    if (!this.beruehrt()) {
      this.schliessen.emit();
      return;
    }
    this.frageOffen.set(true);
    // Der Fokus geht auf „Weiter bearbeiten" — die harmlose Antwort. Der Knopf
    // entsteht erst mit dem naechsten Rendern, deshalb `afterNextRender`.
    afterNextRender(() => this.weiterBtn()?.nativeElement.focus(), {
      injector: this.injector,
    });
  }

  protected verwerfen(): void {
    this.frageOffen.set(false);
    this.schliessen.emit();
  }

  /** Merkt sich, dass im Inhalt getippt wurde (Ereignis steigt vom Feld auf). */
  protected onEingabe(): void {
    this.beruehrt.set(true);
  }

  private hinweisZeigen(): void {
    this.hinweisSichtbar.set(true);
    if (this.hinweisTimer !== null) clearTimeout(this.hinweisTimer);
    this.hinweisTimer = setTimeout(() => {
      this.hinweisSichtbar.set(false);
      this.hinweisTimer = null;
    }, 4000);
  }

  private hinweisAbraeumen(): void {
    if (this.hinweisTimer !== null) clearTimeout(this.hinweisTimer);
    this.hinweisTimer = null;
    this.hinweisSichtbar.set(false);
  }
}
