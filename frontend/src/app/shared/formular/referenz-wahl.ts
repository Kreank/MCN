import {
  Component,
  DestroyRef,
  ElementRef,
  Injector,
  afterNextRender,
  effect,
  inject,
  input,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormControl, ReactiveFormsModule } from '@angular/forms';
import {
  Observable,
  Subject,
  Subscription,
  catchError,
  debounceTime,
  distinctUntilChanged,
  of,
  switchMap,
} from 'rxjs';
import { feldFehlerText } from './feld-fehler';

/**
 * Ein Entscheidungsmerkmal an einer Referenz (Eigentümer, Telefon, Einheiten …).
 * Merkmale beantworten die Frage „ist das WIRKLICH das gesuchte Objekt?" — sie
 * erscheinen in der Trefferzeile UND am gewählten Chip, damit die Entscheidung
 * auch nach der Auswahl nachprüfbar bleibt.
 */
export interface RefMerkmal {
  label: string;
  wert: string;
}

/** Eine auswählbare Referenz (Kontakt, Auftrag, Liegenschaft …). */
export interface RefOption {
  id: string;
  label: string;
  /** Optionale Zweitzeile (Nummer, Ort …). */
  sub?: string | null;
  /** Optionale Merkmale. Leere/unbekannte Werte gehören NICHT hierher (kein
   *  „—"-Rauschen) — der Aufrufer filtert sie weg. */
  merkmale?: RefMerkmal[];
}

/** Ladefunktion: liefert zum Suchbegriff die passenden Optionen. */
export type RefSuche = (q: string) => Observable<RefOption[]>;

let refSeq = 0;

/**
 * Platzbedarf der Trefferliste in Pixeln (`max-height: 15rem` + Rahmen/Polster).
 * Grober Richtwert — es geht nur um „passt nach unten oder nicht".
 */
const LISTE_HOEHE = 250;

/**
 * Barrierefreie Referenz-Auswahl (Combobox mit Serversuche) für Felder, die
 * eine Fremd-ID halten (party_id, work_order_id, property_id …). Statt einer
 * rohen UUID-Eingabe: Suchfeld → Trefferliste (Listbox) → Auswahl als Chip.
 *
 * Der Aufrufer übergibt ein `FormControl<string>` (hält die gewählte ID, '' =
 * nichts gewählt) und eine `suche`-Funktion, die debounced aufgerufen wird.
 * Setzt der Aufrufer das Control von außen auf '' (z. B. `form.reset()`), wird
 * die sichtbare Auswahl automatisch abgeräumt.
 *
 * Tastatur: ↓/↑ bewegen die Aktivmarke, Enter wählt, Escape schließt die Liste
 * (WAI-ARIA Combobox mit `aria-activedescendant`; der Fokus bleibt im Eingabe-
 * feld, die Optionen erhalten ihn nicht).
 *
 * ```html
 * <app-referenz-wahl
 *   [control]="form.controls.party_id" label="Kontakt" [pflicht]="true"
 *   [suche]="parteiSuche" />
 * ```
 */
@Component({
  selector: 'app-referenz-wahl',
  imports: [ReactiveFormsModule],
  templateUrl: './referenz-wahl.html',
  styleUrl: './referenz-wahl.scss',
})
export class ReferenzWahl {
  readonly control = input.required<FormControl<string>>();
  readonly label = input('');
  readonly pflicht = input(false);
  readonly hinweis = input<string | null>(null);
  readonly platzhalter = input('Suchen …');
  readonly suche = input.required<RefSuche>();
  readonly leerText = input('Keine Treffer.');

  protected readonly id = `ref-${++refSeq}`;
  protected readonly listboxId = `${this.id}-listbox`;
  protected readonly hinweisId = `${this.id}-hinweis`;
  protected readonly fehlerId = `${this.id}-fehler`;

  protected readonly offen = signal(false);
  protected readonly ladend = signal(false);
  protected readonly optionen = signal<RefOption[]>([]);
  protected readonly aktivIndex = signal(-1);
  protected readonly gewaehlt = signal<RefOption | null>(null);
  protected readonly suchtext = signal('');
  /** Trefferliste nach OBEN klappen, wenn unten kein Platz ist (siehe `platzPruefen`). */
  protected readonly nachOben = signal(false);

  private readonly such$ = new Subject<string>();
  private readonly destroyRef = inject(DestroyRef);
  private readonly injector = inject(Injector);
  private readonly sucheInput = viewChild<ElementRef<HTMLInputElement>>('sucheInput');
  private readonly aendernBtn = viewChild<ElementRef<HTMLButtonElement>>('aendernBtn');
  /** Das laufende Abo auf das aktuelle Control — bei einem Tausch abbestellt. */
  private valueSub?: Subscription;

  constructor() {
    this.such$
      .pipe(
        debounceTime(250),
        distinctUntilChanged(),
        switchMap((q) => {
          this.ladend.set(true);
          // Fehler (z. B. 403) innen abfangen, damit der Suchstrom weiterlebt
          // und die nächste Eingabe wieder eine Anfrage auslöst.
          return this.suche()(q).pipe(catchError(() => of([] as RefOption[])));
        }),
        takeUntilDestroyed(),
      )
      .subscribe((opts) => {
        this.optionen.set(opts);
        this.ladend.set(false);
        this.aktivIndex.set(opts.length ? 0 : -1);
        this.aufklappen();
      });

    /**
     * Externer Reset (Aufrufer setzt das Control auf '') räumt die Anzeige ab —
     * und ein WECHSEL des Controls wird mitgemacht.
     *
     * Die frühere Fassung hatte hier eine einmalige Sperre (`if (valueSub)
     * return`). Sie verhinderte das Doppelabo, machte den Effekt damit aber
     * blind für einen Control-Tausch: Das alte Abo lief weiter, das neue
     * Control wurde nie beobachtet, und der angezeigte Chip blieb auf dem alten
     * Wert stehen.
     *
     * Das ist keine Theorie. Im Eigentümer-Dialog laufen die Zeilen über
     * `@for (… ; track $index)`: Wer die mittlere Zeile entfernt, bekommt an
     * ihrem Platz die Gruppe der nächsten Zeile gebunden. Die gewöhnlichen
     * Felder zogen nach, dieser Chip nicht — im Formular stand „Schulz",
     * gespeichert wurde Weber. Ein falscher Eigentümer an einer Angabe, für die
     * das Modul ausdrücklich Quellenpflicht verlangt.
     *
     * `untracked` um den Rumpf: Gelesen werden dort `gewaehlt()` und die
     * Anzeige-Signale, die der Rumpf selbst schreibt — ohne die Kapselung wäre
     * der Effekt sein eigener Auslöser.
     */
    effect(() => {
      const c = this.control();
      untracked(() => {
        this.valueSub?.unsubscribe();
        // Erst angleichen, dann abonnieren: Der Chip gehört zum ALTEN Control,
        // wenn seine Id nicht zum Wert des neuen passt.
        const g = this.gewaehlt();
        if (g && g.id !== c.value) {
          this.gewaehlt.set(null);
          this.suchtext.set('');
          this.offen.set(false);
        }
        this.valueSub = c.valueChanges
          .pipe(takeUntilDestroyed(this.destroyRef))
          .subscribe((v) => {
            if (!v && this.gewaehlt()) {
              this.gewaehlt.set(null);
              this.suchtext.set('');
              this.offen.set(false);
            }
          });
      });
    });
  }

  protected fehlerText(): string | null {
    return feldFehlerText(this.control());
  }

  protected beschriebenVon(): string | null {
    const ids: string[] = [];
    if (this.hinweis()) ids.push(this.hinweisId);
    if (this.fehlerText()) ids.push(this.fehlerId);
    return ids.length ? ids.join(' ') : null;
  }

  /**
   * Zugänglicher Name einer Trefferzeile. Die Merkmale werden bewusst IN den
   * Namen gezogen (statt sie per `aria-describedby` anzuhängen): Bei
   * `aria-activedescendant` liest der Screenreader die aktive Option vor, und
   * die Beschreibung folgt dort je nach Kombination verzögert oder gar nicht.
   * Ein expliziter Name hält außerdem Dekor-Trenner („·") aus der Ansage
   * heraus und macht aus den Merkmalen lesbare „Label: Wert"-Sätze.
   *
   * Der sichtbare Text steht am Anfang des Namens — WCAG 2.5.3 („Label in
   * Name") bleibt gewahrt.
   */
  protected optionName(o: RefOption): string {
    const teile: string[] = [o.label];
    if (o.sub) teile.push(o.sub);
    for (const m of o.merkmale ?? []) teile.push(`${m.label}: ${m.wert}`);
    return teile.join('. ');
  }

  protected aktivOptionId(): string | null {
    const i = this.aktivIndex();
    return i >= 0 && i < this.optionen().length ? `${this.id}-opt-${i}` : null;
  }

  protected onFokus(): void {
    if (this.gewaehlt()) return;
    if (this.optionen().length === 0 && !this.ladend()) {
      this.such$.next(this.suchtext());
    } else {
      this.aufklappen();
    }
  }

  /**
   * Liste aufklappen — und zwar dorthin, wo sie auch zu sehen ist.
   *
   * Die Liste liegt `position: absolute` im Feld. In einem **scrollenden**
   * Behälter (jeder Dialog: `.dialog__panel` hat `overflow-y: auto`) wird sie
   * an dessen Kante abgeschnitten. Steht das Feld unten im Dialog, sah man
   * deshalb nur einen Streifen — ein Dropdown, das sich nicht bedienen ließ.
   * Genau dieser Befund kam aus dem Test des Eigentums-Dialogs.
   *
   * Zwei Maßnahmen: erst das Feld in den sichtbaren Bereich holen, dann — wenn
   * unten immer noch zu wenig Platz ist — die Liste nach oben klappen.
   */
  private aufklappen(): void {
    const el = this.sucheInput()?.nativeElement;
    // `block: 'nearest'` scrollt nur, wenn es nötig ist — sonst spränge das
    // Formular bei jedem Tastendruck. Optional aufgerufen: Im Test-DOM gibt es
    // `scrollIntoView` nicht, und daran soll keine Suche scheitern.
    el?.scrollIntoView?.({ block: 'nearest' });
    this.platzPruefen();
    this.offen.set(true);
  }

  private platzPruefen(): void {
    const el = this.sucheInput()?.nativeElement;
    if (!el || typeof el.getBoundingClientRect !== 'function') return;
    const feld = el.getBoundingClientRect();
    const { oben, unten } = this.sichtRahmen(el);
    const platzUnten = unten - feld.bottom;
    const platzOben = feld.top - oben;
    this.nachOben.set(platzUnten < LISTE_HOEHE && platzOben > platzUnten);
  }

  /**
   * Der sichtbare Ausschnitt: das Fenster, verengt um jeden scrollenden oder
   * klippenden Vorfahren. Genau diese Kanten schneiden die Liste ab.
   */
  private sichtRahmen(el: HTMLElement): { oben: number; unten: number } {
    let oben = 0;
    let unten = typeof window !== 'undefined' ? window.innerHeight : 0;
    let p = el.parentElement;
    while (p) {
      const stil = getComputedStyle(p);
      if (stil.overflowY !== 'visible' && stil.overflowY !== '') {
        const r = p.getBoundingClientRect();
        oben = Math.max(oben, r.top);
        unten = Math.min(unten, r.bottom);
      }
      p = p.parentElement;
    }
    return { oben, unten };
  }

  protected onEingabe(wert: string): void {
    this.suchtext.set(wert);
    this.serverFehlerLoeschen();
    this.such$.next(wert.trim());
  }

  protected onTaste(event: KeyboardEvent): void {
    const opts = this.optionen();
    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault();
        if (!this.offen()) {
          this.onFokus();
          return;
        }
        if (opts.length) this.aktivIndex.update((i) => (i + 1) % opts.length);
        break;
      case 'ArrowUp':
        event.preventDefault();
        if (opts.length) this.aktivIndex.update((i) => (i - 1 + opts.length) % opts.length);
        break;
      case 'Home':
        if (this.offen() && opts.length) {
          event.preventDefault();
          this.aktivIndex.set(0);
        }
        break;
      case 'End':
        if (this.offen() && opts.length) {
          event.preventDefault();
          this.aktivIndex.set(opts.length - 1);
        }
        break;
      case 'Enter': {
        const i = this.aktivIndex();
        if (this.offen() && i >= 0 && i < opts.length) {
          event.preventDefault();
          this.waehlen(opts[i]);
        }
        break;
      }
      case 'Escape':
        if (this.offen()) {
          event.preventDefault();
          this.offen.set(false);
        }
        break;
    }
  }

  protected onBlur(): void {
    // Nach dem Klick auf eine Option (mousedown) erst schließen, damit die
    // Auswahl noch greift.
    setTimeout(() => this.offen.set(false), 120);
  }

  protected waehlen(o: RefOption): void {
    this.gewaehlt.set(o);
    this.offen.set(false);
    this.optionen.set([]);
    const c = this.control();
    c.setValue(o.id);
    c.markAsDirty();
    c.markAsTouched();
  }

  /**
   * Auswahl von AUSSEN setzen (z. B. „Übernehmen" aus einer Dublettenwarnung).
   * Nötig, weil ein bloßes `control.setValue(id)` zwar die ID hielte, die
   * sichtbare Auswahl aber leer ließe — Wert und Anzeige liefen auseinander.
   *
   * Der Fokus wandert auf „Ändern": Der auslösende Knopf verschwindet in aller
   * Regel mit der Übernahme, und Fokus auf `<body>` wäre ein Bruch (WCAG 2.4.3).
   */
  auswahlSetzen(o: RefOption): void {
    this.waehlen(o);
    // `afterNextRender` statt `queueMicrotask`: Der Chip mitsamt „Ändern"-Knopf
    // entsteht erst mit dem nächsten Rendern. Zoneless taktet die Erkennung
    // gegen rAF/Timeout — ein Microtask könnte davor laufen und liefe dann ins
    // Leere (Fokus fiele auf <body>).
    afterNextRender(() => this.aendernBtn()?.nativeElement.focus(), {
      injector: this.injector,
    });
  }

  protected zuruecksetzen(): void {
    this.gewaehlt.set(null);
    this.suchtext.set('');
    this.optionen.set([]);
    const c = this.control();
    c.setValue('');
    c.markAsTouched();
    queueMicrotask(() => this.sucheInput()?.nativeElement.focus());
  }

  private serverFehlerLoeschen(): void {
    const c = this.control();
    const e = c.errors;
    if (e && e['server'] != null) {
      const { server, ...rest } = e as Record<string, unknown>;
      c.setErrors(Object.keys(rest).length ? rest : null);
    }
  }
}
