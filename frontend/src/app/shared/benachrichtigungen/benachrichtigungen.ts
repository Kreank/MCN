import {
  Component,
  ElementRef,
  OnDestroy,
  computed,
  effect,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { Router } from '@angular/router';
import { AuthService } from '../../core/auth.service';
import { BenachrichtigungService } from '../../core/benachrichtigung.service';
import {
  Benachrichtigung,
  zielRoute,
} from '../../core/benachrichtigung.model';
import { scrollFreigeben, scrollSperren } from '../dialog/dialog';
import { isoDatumDe } from '../datum';

/** Wie oft im Hintergrund nach neuen Meldungen gefragt wird. */
const TAKT_MS = 60_000;

/**
 * Die Glocke in der Kopfzeile — der aktive Teil der Aufgaben-Rückmeldung.
 *
 * **Warum ein modaler `<dialog>` und kein schwebendes Menü.** Ein handgebautes
 * Dropdown müsste Fokusfalle, Escape, Klick-außerhalb und die Fokusrückgabe
 * selbst führen — vier Stellen, an denen Tastaturbedienung still kaputtgeht.
 * `showModal()` bringt alle vier vom Browser mit; dasselbe Argument steht schon
 * an der Kommandopalette. Das Panel wird oben rechts unter der Glocke verankert
 * und liest sich damit weiter als deren Auszug, nicht als Seitenwechsel.
 *
 * **Abfragetakt statt Push.** Alle 60 Sekunden wird nur der Zähler geholt
 * (`/zaehler`, ein Index-Zugriff) — und nur, solange der Reiter sichtbar ist.
 * Ein liegengelassener Leitstand fragt nichts. Websockets wären für „ein
 * Zahlwert alle Minute" die deutlich teurere Antwort auf dieselbe Frage.
 */
@Component({
  selector: 'app-benachrichtigungen',
  templateUrl: './benachrichtigungen.html',
  styleUrl: './benachrichtigungen.scss',
})
export class Benachrichtigungen implements OnDestroy {
  private readonly svc = inject(BenachrichtigungService);
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  private readonly dlg = viewChild<ElementRef<HTMLDialogElement>>('dlg');

  protected readonly offen = signal(false);
  protected readonly laedt = signal(false);
  protected readonly fehler = signal(false);
  protected readonly items = signal<Benachrichtigung[]>([]);
  protected readonly ungelesen = this.svc.ungelesen;

  protected readonly hatUngelesen = computed(() => this.ungelesen() > 0);
  /** Ab 100 wird die Zahl zur Marke — dreistellig sprengt die Plakette. */
  protected readonly zaehlerText = computed(() => {
    const n = this.ungelesen();
    return n > 99 ? '99+' : String(n);
  });
  protected readonly glockenLabel = computed(() => {
    const n = this.ungelesen();
    if (n === 0) return 'Benachrichtigungen, keine ungelesenen';
    return `Benachrichtigungen, ${n} ungelesen`;
  });

  private takt?: ReturnType<typeof setInterval>;
  private readonly aufSichtbarkeit = () => {
    if (document.visibilityState === 'visible') this.zaehlerHolen();
  };

  constructor() {
    // An die Sitzung gekoppelt: Erst mit Profil gibt es ein Postfach, und beim
    // Abmelden muss der Zähler weg — sonst stünde auf der Anmeldeseite noch
    // die Zahl des vorigen Kontos.
    effect(() => {
      if (this.auth.istAngemeldet()) {
        this.taktStarten();
      } else {
        this.taktStoppen();
        this.svc.ungelesen.set(0);
        this.items.set([]);
      }
    });
  }

  ngOnDestroy(): void {
    this.taktStoppen();
    if (this.offen()) scrollFreigeben();
  }

  private taktStarten(): void {
    this.zaehlerHolen();
    if (this.takt !== undefined) return;
    this.takt = setInterval(() => {
      if (document.visibilityState === 'visible') this.zaehlerHolen();
    }, TAKT_MS);
    document.addEventListener('visibilitychange', this.aufSichtbarkeit);
  }

  private taktStoppen(): void {
    if (this.takt !== undefined) {
      clearInterval(this.takt);
      this.takt = undefined;
    }
    document.removeEventListener('visibilitychange', this.aufSichtbarkeit);
  }

  private zaehlerHolen(): void {
    // Fehler bleiben still: Eine Kopfzeile, die bei jedem Netzwackler eine
    // Fehlermeldung wirft, ist schlimmer als eine kurz veraltete Zahl.
    this.svc.zaehler().subscribe({ error: () => {} });
  }

  // --- Panel ---------------------------------------------------------------

  umschalten(): void {
    if (this.offen()) this.schliessen();
    else this.oeffnen();
  }

  oeffnen(): void {
    this.offen.set(true);
    scrollSperren();
    const el = this.dlg()?.nativeElement;
    if (el) {
      // Der Fallback deckt Test-DOMs ab, die showModal nicht kennen.
      if (typeof el.showModal === 'function') el.showModal();
      else el.setAttribute('open', '');
    }
    this.laden();
  }

  schliessen(): void {
    if (!this.offen()) return;
    this.offen.set(false);
    scrollFreigeben();
    const el = this.dlg()?.nativeElement;
    if (el) {
      if (typeof el.close === 'function') el.close();
      else el.removeAttribute('open');
    }
  }

  protected onCancel(event: Event): void {
    event.preventDefault();
    this.schliessen();
  }

  protected onKlick(event: MouseEvent): void {
    if (event.target === this.dlg()?.nativeElement) this.schliessen();
  }

  private laden(): void {
    this.laedt.set(true);
    this.fehler.set(false);
    this.svc.liste(1, 20).subscribe({
      next: (seite) => {
        this.items.set(seite.items);
        this.laedt.set(false);
      },
      error: () => {
        this.laedt.set(false);
        this.fehler.set(true);
      },
    });
  }

  erneutVersuchen(): void {
    this.laden();
  }

  // --- Aktionen ------------------------------------------------------------

  /**
   * Anspringen: erst als gelesen markieren, dann navigieren — und das Panel
   * schließen, bevor der Router die Seite wechselt (ein offener modaler Dialog
   * über der Zielseite wäre eine Sackgasse).
   *
   * Die Navigation wartet NICHT auf den Server: Der Klick soll sich sofort
   * anfühlen, und ob die Zeile schon als gelesen zählt, ist zweitrangig.
   */
  anspringen(n: Benachrichtigung): void {
    const route = zielRoute(n);
    this.markieren(n);
    this.schliessen();
    if (route) this.router.navigate(route);
  }

  markieren(n: Benachrichtigung): void {
    if (n.read_at) return;
    // Optimistisch: die Zeile verliert ihre Hervorhebung sofort.
    this.items.update((liste) =>
      liste.map((x) => (x.id === n.id ? { ...x, read_at: new Date().toISOString() } : x)),
    );
    this.svc.gelesen(n.id).subscribe({ error: () => {} });
  }

  alleGelesen(): void {
    const jetzt = new Date().toISOString();
    this.items.update((liste) =>
      liste.map((x) => (x.read_at ? x : { ...x, read_at: jetzt })),
    );
    this.svc.alleGelesen().subscribe({ error: () => this.laden() });
  }

  // --- Darstellung ---------------------------------------------------------

  protected istAnklickbar(n: Benachrichtigung): boolean {
    return zielRoute(n) !== null;
  }

  /**
   * Zeitangabe: heute die Uhrzeit, sonst das Datum. „vor 3 Minuten" wäre eine
   * Angabe, die ohne Neuzeichnen still veraltet.
   */
  protected zeit(iso: string): string {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    const heute = new Date();
    const gleicherTag =
      d.getFullYear() === heute.getFullYear() &&
      d.getMonth() === heute.getMonth() &&
      d.getDate() === heute.getDate();
    if (gleicherTag) {
      return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')} Uhr`;
    }
    return isoDatumDe(iso.slice(0, 10));
  }

  /** Kurzwort der Art — Status nie allein über Farbe (WCAG 2.2 AA). */
  protected artLabel(kind: string): string {
    switch (kind) {
      case 'AUFGABE_ZUGEWIESEN':
        return 'Zugewiesen';
      case 'AUFGABE_ENTZOGEN':
        return 'Übertragen';
      case 'AUFGABE_ERLEDIGT':
        return 'Erledigt';
      case 'AUFGABE_WIEDEROFFEN':
        return 'Wieder offen';
      case 'AUFGABE_VERWORFEN':
        return 'Verworfen';
      case 'AUFGABE_KOMMENTAR':
        return 'Rückfrage';
      case 'ANGEBOT_ANGENOMMEN':
        return 'Angenommen';
      case 'ANGEBOT_ABGELEHNT':
        return 'Abgelehnt';
      default:
        return 'Hinweis';
    }
  }

  protected artClass(kind: string): string {
    if (kind === 'AUFGABE_ERLEDIGT' || kind === 'ANGEBOT_ANGENOMMEN') {
      return 'stamp--positive';
    }
    if (kind === 'AUFGABE_VERWORFEN') return 'stamp--warn';
    if (kind === 'ANGEBOT_ABGELEHNT') return 'stamp--negativ';
    return '';
  }
}
