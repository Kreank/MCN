import {
  AfterViewInit,
  Component,
  DestroyRef,
  ElementRef,
  computed,
  inject,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';

/**
 * Unterschriftenfeld: eine Zeichenfläche (Canvas), auf der der Kunde mit Maus,
 * Finger oder Stift (Pointer Events) unterschreibt. Liefert die Unterschrift als
 * PNG-Base64 (`alsBase64()`), meldet Änderungen über `veraendert` und lässt sich
 * über die Schaltfläche „Löschen" bzw. `leeren()` zurücksetzen.
 *
 * Barrierefreiheit: Eine handschriftliche Unterschrift ist der Lehrbuchfall der
 * WCAG-2.2-Ausnahme SC 2.1.1 („path-dependent input") — sie lässt sich nicht per
 * Tastatur nachbilden, weil sie genau der Zeigergeste des Kunden entspricht. Die
 * Fläche trägt `role="img"` mit Beschriftung und einen sichtbaren Hinweis. Der
 * Name des Unterzeichnenden wird SEPARAT als reguläres, tastaturbedienbares
 * Pflicht-Textfeld erfasst (kein Ersatz für die Unterschrift, sondern ein
 * zusätzliches Feld). `prefers-reduced-motion` ist hier nicht relevant (keine
 * Animation).
 *
 * ```html
 * <app-unterschrift-pad #pad (veraendert)="…" />
 * … pad.alsBase64() …
 * ```
 */
@Component({
  selector: 'app-unterschrift-pad',
  templateUrl: './unterschrift-pad.html',
  styleUrl: './unterschrift-pad.scss',
})
export class UnterschriftPad implements AfterViewInit {
  private readonly destroyRef = inject(DestroyRef);

  /** Beschriftung für Screenreader (role="img"). */
  readonly label = input<string>('Unterschriftenfeld');

  private readonly canvasRef = viewChild.required<ElementRef<HTMLCanvasElement>>('canvas');

  /** Es wurde etwas gezeichnet (für „Löschen"/Absenden-Zustände im Eltern-UI). */
  protected readonly gezeichnet = signal(false);
  readonly leer = computed(() => !this.gezeichnet());

  /** Feuert nach jedem Strich bzw. nach dem Löschen. */
  readonly veraendert = output<void>();

  private ctx: CanvasRenderingContext2D | null = null;
  private zeichnet = false;
  private letzterPunkt: { x: number; y: number } | null = null;

  ngAfterViewInit(): void {
    const canvas = this.canvasRef().nativeElement;
    // Auflösung an die tatsächliche Anzeigegröße koppeln (scharfe Linien auf
    // HiDPI). Die Fläche hat eine feste CSS-Höhe; die Breite folgt dem Container.
    this.flaecheEinrichten();
    const ro = new ResizeObserver(() => this.flaecheEinrichten());
    ro.observe(canvas);
    this.destroyRef.onDestroy(() => ro.disconnect());
  }

  private flaecheEinrichten(): void {
    const canvas = this.canvasRef().nativeElement;
    const rect = canvas.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;
    const dpr = window.devicePixelRatio || 1;
    // Beim Umskalieren geht der Inhalt verloren; das ist akzeptabel, weil die
    // Fläche vor der Erfassung leer ist. Nach dem Zeichnen ändert sich die
    // Größe im Dialog nicht mehr.
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(rect.height * dpr);
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.scale(dpr, dpr);
    ctx.lineWidth = 2;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.strokeStyle = '#1c3244'; // Marineblau (Brandfarbe)
    this.ctx = ctx;
    // Ein Umskalieren löscht die Bitmap (canvas.width neu gesetzt). Das melden,
    // damit ein Eltern-Zustand wie `unterschriftLeer` nicht auf „gezeichnet"
    // stehen bleibt, während die Fläche in Wahrheit leer ist.
    this.gezeichnet.set(false);
    this.veraendert.emit();
  }

  private punkt(ev: PointerEvent): { x: number; y: number } {
    const rect = this.canvasRef().nativeElement.getBoundingClientRect();
    return { x: ev.clientX - rect.left, y: ev.clientY - rect.top };
  }

  onPointerDown(ev: PointerEvent): void {
    if (!this.ctx) return;
    ev.preventDefault();
    this.canvasRef().nativeElement.setPointerCapture(ev.pointerId);
    this.zeichnet = true;
    this.letzterPunkt = this.punkt(ev);
    // Ein einzelner Tipp soll einen Punkt setzen.
    const p = this.letzterPunkt;
    this.ctx.beginPath();
    this.ctx.moveTo(p.x, p.y);
    this.ctx.lineTo(p.x + 0.1, p.y + 0.1);
    this.ctx.stroke();
    this.gezeichnet.set(true);
    this.veraendert.emit();
  }

  onPointerMove(ev: PointerEvent): void {
    if (!this.zeichnet || !this.ctx || !this.letzterPunkt) return;
    ev.preventDefault();
    const p = this.punkt(ev);
    this.ctx.beginPath();
    this.ctx.moveTo(this.letzterPunkt.x, this.letzterPunkt.y);
    this.ctx.lineTo(p.x, p.y);
    this.ctx.stroke();
    this.letzterPunkt = p;
  }

  onPointerUp(ev: PointerEvent): void {
    if (!this.zeichnet) return;
    this.zeichnet = false;
    this.letzterPunkt = null;
    const canvas = this.canvasRef().nativeElement;
    if (canvas.hasPointerCapture(ev.pointerId)) {
      canvas.releasePointerCapture(ev.pointerId);
    }
  }

  leeren(): void {
    const canvas = this.canvasRef().nativeElement;
    this.ctx?.clearRect(0, 0, canvas.width, canvas.height);
    this.gezeichnet.set(false);
    this.veraendert.emit();
  }

  /** PNG-Base64 der Unterschrift (ohne data:-Präfix), oder null wenn leer. */
  alsBase64(): string | null {
    if (this.leer()) return null;
    const dataUrl = this.canvasRef().nativeElement.toDataURL('image/png');
    const komma = dataUrl.indexOf(',');
    return komma >= 0 ? dataUrl.slice(komma + 1) : null;
  }
}
