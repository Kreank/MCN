import { Directive, ElementRef, OnDestroy, OnInit, inject } from '@angular/core';

/**
 * Oeffnet ein Panel, das ein Popover-faehiger Browser in den Top Layer hebt.
 *
 * Wird vor `focus()` gebraucht, damit der Nutzer sieht, wohin der Fokus geht:
 * Betrifft die Rueckwege der Plantafel — das Abbrechen einer Verschiebung und
 * das Schliessen eines Dialogs stellen den Fokus auf den ausloesenden Knopf im
 * Panel zurueck (WCAG 2.4.3). Ohne Aufklappen laege der Fokus auf einem
 * durchsichtigen Knopf.
 */
export function panelOeffnen(el: HTMLElement | null | undefined): void {
  const panel = el?.closest<HTMLElement>('[popover]');
  if (panel && !panel.matches(':popover-open')) panel.showPopover?.();
}

/**
 * Hebt ein Panel in den Top Layer, damit es weder von `overflow` beschnitten
 * noch von einem fremden Stacking-Context verdeckt wird.
 *
 * Warum das noetig ist: Ein Panel, das seine Kachel VERLASSEN muss (Hover-Menue
 * unter einer Plantafel-Kachel), ist DOM-seitig ein Kind dieser Kachel. Die
 * Kachel traegt `container-type: inline-size` — das impliziert `contain: layout`
 * und erzeugt damit einen eigenen Stacking-Context. Ein `z-index` INNERHALB der
 * Kachel wird an deren Grenze abgeschnitten: nach aussen zaehlt nur der z-index
 * der Kachel selbst. Gegen die Nachbarkacheln (gleicher z-index) entscheidet
 * dann die DOM-Reihenfolge — jede weiter unten stehende Kachel gewinnt. Dazu
 * kommt der Scroll-Container der Tafel, der das Panel an seiner Unterkante
 * abschneidet. Beides ist mit CSS allein nicht loesbar.
 *
 * `popover` hebt das Element in den Top Layer: dort gibt es weder Clipping durch
 * Vorfahren noch Stacking-Konkurrenz. Positioniert wird per `position: fixed`
 * anhand der Ankerbox, mit Umklappen nach oben, wenn unten kein Platz ist.
 *
 * `popover="auto"` statt `manual`: Nur `auto` liefert Escape-Dismiss (WCAG
 * 1.4.13 verlangt fuer Hover-/Fokus-Overlays eine Schliessmoeglichkeit, ohne
 * Zeiger oder Fokus zu bewegen) und schliesst automatisch andere offene Panels,
 * sodass kein Geisterpanel ueber fremden Kacheln stehenbleibt. Preis dafuer:
 * Der Browser schliesst auch ohne unser Zutun — der Zustand wird deshalb nicht
 * in einem eigenen Flag gefuehrt, sondern immer aus `:popover-open` gelesen.
 *
 * Tastaturbedienung: Das geschlossene Panel bleibt sichtbar-unsichtbar
 * (`opacity: 0`) statt `display: none` — seine Knoepfe bleiben also im
 * Fokus- und im Vorlesebaum. Das ist kein Versehen, sondern Bedingung: Mit
 * `display: none` waeren sie per Shift+Tab von hinten nie erreichbar und im
 * Browsemodus eines Screenreaders gar nicht vorhanden. `focusin` am Anker
 * klappt das Panel auf, sobald der Fokus die Kachel erreicht; ein offenes
 * Popover liegt weiterhin an seiner DOM-Position in der Tab-Reihenfolge, denn
 * der Top Layer betrifft nur das Zeichnen, nicht die Fokusnavigation.
 *
 * Ohne Popover-Unterstuetzung passiert nichts — dann greift das CSS-Fallback
 * (absolut positioniert an der Kachel, wie zuvor).
 */
@Directive({
  selector: '[appSchwebendesPanel]',
  standalone: true,
})
export class SchwebendesPanel implements OnInit, OnDestroy {
  private readonly el = inject(ElementRef<HTMLElement>).nativeElement as HTMLElement;
  /** Der Anker — die Kachel, unter der das Panel haengt. Erst ab `ngOnInit`. */
  private anker: HTMLElement | null = null;
  private schliessTimer: ReturnType<typeof setTimeout> | undefined;
  private entsperrTimer: ReturnType<typeof setTimeout> | undefined;
  /** Siehe `beiToggle`: unterdrueckt das Wiederaufklappen direkt nach Escape. */
  private fokusIgnorieren = false;

  /**
   * Kein Abstand zur Kachel. Eine Luecke waere eine Totzone: Das Panel ist zwar
   * DOM-Kind (der Weg Kachel->Panel loest deshalb kein `mouseleave` aus), aber
   * nur solange der Zeiger dabei keinen fremden Knoten ueberstreicht. Schon
   * 2 px genuegen, damit auf dem Weg nach unten kurz die Nachbarzelle getroffen
   * wird und das Panel beim Anfahren zuklappt. Optischer Abstand gehoert ins
   * Padding, nicht in die Geometrie.
   */
  private static readonly LUFT = 0;
  /** Mindestabstand zum Viewport-Rand, damit nichts am Bildschirmrand klebt. */
  private static readonly RAND = 8;

  private readonly auf = (e: Event) => {
    // Nur der Fokusweg wird unterdrueckt: Wer die Kachel mit der Maus neu
    // anfaehrt, will das Panel sehen — auch unmittelbar nach einem Escape.
    if (this.fokusIgnorieren && e.type === 'focusin') return;
    this.oeffnen();
  };

  /**
   * Schliesst verzoegert — und nur, wenn weder Zeiger noch Fokus im Anker sind.
   *
   * Zwei Fallen stecken hier drin. Erstens das Tabben von der Kachel auf den
   * ersten Knopf: Da feuert `focusout` (Kachel) VOR `focusin` (Knopf). Wer im
   * `focusout` blind schliesst, setzt das Panel auf `display: none` und nimmt
   * dem Knopf, der den Fokus gerade bekommen soll, den Boden weg. Zweitens der
   * ruhende Tastaturfokus: Liegt er in einem Knopf und die Maus verlaesst die
   * Kachel (oder die Tafel scrollt unter dem Zeiger weg), wuerde ein sofortiges
   * `mouseleave`-Schliessen das fokussierte Element ausblenden und den Fokus in
   * den `<body>` werfen.
   *
   * Deshalb: `relatedTarget` faengt den unmittelbaren Fall ab, und der
   * verzoegerte Lauf prueft `document.activeElement`, sobald der Fokuswechsel
   * abgeschlossen ist. Das deckt zugleich `relatedTarget === null` beim
   * Fenster-Blur (Alt-Tab) ab.
   */
  private readonly zu = (e: FocusEvent | MouseEvent) => {
    const ziel = e.relatedTarget;
    if (ziel instanceof Node && this.anker?.contains(ziel)) return;
    clearTimeout(this.schliessTimer);
    this.schliessTimer = setTimeout(() => {
      if (this.anker?.contains(document.activeElement)) return;
      this.schliessen();
    });
  };

  /**
   * Waehrend einer Drag-Operation feuert der Browser KEINE Mausereignisse — ein
   * offenes Panel bliebe fuer die gesamte Dauer im Top Layer stehen, deckte die
   * naheliegenden Drop-Zellen ab und schluckte deren `dragover`/`drop`. Hier
   * also ohne Fokus-Ruecksicht schliessen.
   */
  private readonly beiDragStart = () => {
    clearTimeout(this.schliessTimer);
    this.schliessen();
  };

  /**
   * Beim Scrollen mitwandern statt schliessen: Die Tafel wird waehrend des
   * Hoverns gescrollt (Trackpad, Shift+Rad), und ein Panel, das dabei
   * verschwindet, waere nicht bedienbar.
   *
   * `capture: true` ist Pflicht: Scroll-Events eines Elements BUBBELN NICHT.
   * Der Scroll-Container der Tafel (`.board-wrap`) wuerde ohne Capture-Phase am
   * document nie ankommen — das Panel bliebe stehen, waehrend seine Kachel
   * darunter wegwandert.
   */
  private readonly nachfuehren = () => this.positionieren();

  /**
   * Der Browser schliesst `auto`-Popover auch von sich aus (Escape,
   * Light-Dismiss, ein anderes Panel oeffnet). Die Scroll-/Resize-Listener
   * haengen deshalb am tatsaechlichen Zustand und nicht an unseren Aufrufen —
   * sonst bliebe pro Kachel ein Listener am `document` haengen (bei mehreren
   * hundert Kacheln laeuft das bei jedem Scroll-Tick mit).
   */
  private readonly beiToggle = (e: Event) => {
    if ((e as ToggleEvent).newState === 'open') {
      this.positionieren();
      document.addEventListener('scroll', this.nachfuehren, { capture: true, passive: true });
      window.addEventListener('resize', this.nachfuehren, { passive: true });
      return;
    }
    this.scrollLauschenBeenden();
  };

  /**
   * Das Aufraeumen MUSS hier haengen, nicht am `toggle`.
   *
   * `toggle` wird als eigener Task EINGEREIHT, die Fokusrueckgabe beim
   * Schliessen laeuft dagegen SYNCHRON im selben Task. Ein eingereihter Task
   * kann erst danach laufen — `focusin` kommt also immer zuerst. Schlimmer: Da
   * dieses `focusin` das Panel sofort wieder oeffnet, verwirft der Browser den
   * eingereihten Close-Task (er koalesziert Toggle-Ereignisse). Bei Escape
   * liefe der Close-Zweig von `toggle` deshalb ueberhaupt nie.
   *
   * `beforetoggle` feuert synchron VOR der Fokusrueckgabe und wird nicht
   * koalesziert — hier greifen beide Massnahmen verlaesslich.
   */
  private readonly beiBeforeToggle = (e: Event) => {
    if ((e as ToggleEvent).newState !== 'closed') return;
    // Die Viewport-Koordinaten MUESSEN weg: Geschlossen haengt das Panel wieder
    // absolut in der Kachel, wo dieselben Zahlen als Offsets INNERHALB der
    // Kachel wirken wuerden — der Browser scrollte beim Hintabben an eine
    // Phantomposition irgendwo neben der Tafel.
    this.el.style.left = '';
    this.el.style.top = '';
    // Escape (und Light-Dismiss) geben den Fokus auf den Ausloeser zurueck —
    // den Kachel-Link. Dieser Fokus bubbelt als `focusin` an den Anker und
    // riefe sofort wieder `auf()`: Escape waere wirkungslos, das Panel klappte
    // im selben Moment wieder auf. Der naechste Fokus-Impuls wird deshalb
    // verworfen; ein Makrotask spaeter (nach der Fokusrueckgabe) zaehlt wieder
    // jeder Impuls, damit Hineintabben unveraendert oeffnet.
    this.fokusIgnorieren = true;
    clearTimeout(this.entsperrTimer);
    this.entsperrTimer = setTimeout(() => (this.fokusIgnorieren = false));
  };

  /**
   * Erst hier, NICHT im Konstruktor: `.tile__aktionen` ist der Wurzelknoten
   * eines `@if`-Blocks. Angular erzeugt eingebettete Views in zwei Schritten —
   * Direktiven-Konstruktoren laufen im Creation-Pass, das Einhaengen ins DOM
   * folgt erst danach. Im Konstruktor ist `parentElement` deshalb `null`, und
   * die Direktive waere wirkungslos.
   */
  ngOnInit(): void {
    this.anker = this.el.parentElement;
    if (!this.unterstuetzt() || !this.anker) return;
    this.el.setAttribute('popover', 'auto');
    this.el.addEventListener('toggle', this.beiToggle);
    this.el.addEventListener('beforetoggle', this.beiBeforeToggle);
    this.anker.addEventListener('mouseenter', this.auf);
    this.anker.addEventListener('mouseleave', this.zu);
    // `focusin`/`focusout` statt `focus`/`blur`: nur die bubbeln, und der Fokus
    // landet auf einem NACHFAHREN (Link/Knopf), nie auf der Kachel selbst.
    this.anker.addEventListener('focusin', this.auf);
    this.anker.addEventListener('focusout', this.zu);
    this.anker.addEventListener('dragstart', this.beiDragStart);
  }

  ngOnDestroy(): void {
    clearTimeout(this.schliessTimer);
    clearTimeout(this.entsperrTimer);
    this.scrollLauschenBeenden();
    this.el.removeEventListener('toggle', this.beiToggle);
    this.el.removeEventListener('beforetoggle', this.beiBeforeToggle);
    if (!this.anker) return;
    this.anker.removeEventListener('mouseenter', this.auf);
    this.anker.removeEventListener('mouseleave', this.zu);
    this.anker.removeEventListener('focusin', this.auf);
    this.anker.removeEventListener('focusout', this.zu);
    this.anker.removeEventListener('dragstart', this.beiDragStart);
  }

  private scrollLauschenBeenden(): void {
    document.removeEventListener('scroll', this.nachfuehren, { capture: true });
    window.removeEventListener('resize', this.nachfuehren);
  }

  private unterstuetzt(): boolean {
    return typeof (this.el as HTMLElement & { showPopover?: unknown }).showPopover === 'function';
  }

  /**
   * Der Zustand wird immer frisch aus `:popover-open` gelesen, nie in einem
   * Flag gefuehrt: Angular entfernt beim Zerstoeren einer View die DOM-Knoten
   * VOR den Destroy-Hooks, und die Removing-Steps des Popovers setzen den
   * Zustand implizit auf „geschlossen". Ein mitgefuehrtes Flag waere dann
   * veraltet, und `hidePopover()` auf einem nicht offenen Popover wirft
   * `InvalidStateError` — mitten im Destroy-Lauf.
   */
  private get offen(): boolean {
    return this.el.isConnected && this.el.matches(':popover-open');
  }

  private oeffnen(): void {
    clearTimeout(this.schliessTimer);
    if (this.offen || !this.el.isConnected) return;
    this.el.showPopover();
    // Nicht auf `toggle` warten: Das Ereignis wird asynchron zugestellt, das
    // Panel waere bis dahin einen Frame lang an der alten Stelle sichtbar.
    this.positionieren();
  }

  private schliessen(): void {
    if (!this.offen) return;
    this.el.hidePopover();
  }

  /**
   * Setzt das Panel unter den Anker; kein Platz nach unten -> darueber. Die
   * Koordinaten sind Viewport-Koordinaten, weil ein Top-Layer-Element von
   * keinem Vorfahren mehr positioniert wird (`position: fixed`).
   */
  private positionieren(): void {
    if (!this.anker || !this.offen) return;

    // Vor dem Messen an den Viewport-Ursprung setzen — NICHT auf '' raeumen:
    // Das Panel ist `fit-content` breit, der Abstand zum rechten Rand begrenzt
    // also seine Breite. Stuende noch ein `left` vom letzten Mal, wuerde ein
    // gewachsenes Panel zu schmal gemessen. Ein fester, freier Ursprung ist
    // dafuer verlaesslicher als das Zurueckfallen auf eine CSS-Regel, die
    // jemand spaeter aendert.
    this.el.style.left = '0';
    this.el.style.top = '0';

    const a = this.anker.getBoundingClientRect();
    const p = this.el.getBoundingClientRect();
    const { LUFT, RAND } = SchwebendesPanel;

    const platzUnten = window.innerHeight - a.bottom;
    const nachOben = platzUnten < p.height + LUFT + RAND && a.top > p.height + LUFT + RAND;
    const top = nachOben ? a.top - p.height - LUFT : a.bottom + LUFT;

    // Linksbuendig am Anker, aber nie ueber den rechten Rand hinaus. Das Panel
    // ist `nowrap` und damit breiter als eine schmale Kachel.
    const maxLinks = window.innerWidth - p.width - RAND;
    const left = Math.max(RAND, Math.min(a.left, maxLinks));

    this.el.style.left = `${Math.round(left)}px`;
    this.el.style.top = `${Math.round(top)}px`;
  }
}
