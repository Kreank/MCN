import {
  Component,
  ElementRef,
  HostListener,
  OnDestroy,
  computed,
  effect,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Router } from '@angular/router';
import { Subject, catchError, debounceTime, distinctUntilChanged, map, of, switchMap } from 'rxjs';
import { scrollFreigeben, scrollSperren } from '../dialog/dialog';
import {
  SUCHE_KATEGORIE,
  SUCHE_MARK,
  SUCHE_MIN_LAENGE,
  SUCHE_ORDNUNG,
  SUCHE_TRIGRAMM_MIN,
  SUCHE_ZIEL,
  SucheEntityTyp,
  SucheErgebnis,
  SucheService,
  SucheTreffer,
  artikelUebersprungen,
  begriffZuKurz,
} from '../../core/suche.service';

/** Ein Treffer samt seiner Position in der flachen Tastatur-Reihenfolge. */
interface Zeile {
  readonly treffer: SucheTreffer;
  readonly idx: number;
}

interface Gruppe {
  readonly typ: string;
  readonly label: string;
  readonly mark: string;
  readonly zeilen: Zeile[];
  /** Der Server haette mehr — die Ueberschrift sagt es. */
  readonly mehr: boolean;
}

/** Aufbereitetes Ergebnis: Direkttreffer, Gruppen und die flache Navigationsliste. */
interface Ansicht {
  readonly direkt: Zeile | null;
  readonly gruppen: Gruppe[];
  readonly flach: SucheTreffer[];
  /**
   * Kategorien, aus denen KEINE einzige Zeile mehr in die Liste passte
   * (`anzahl = 0`, `mehr_vorhanden = true`). Sie werden benannt, statt sie
   * stillschweigend verschwinden zu lassen — sonst waere „passte nicht mehr rein"
   * von „nichts gefunden" nicht zu unterscheiden.
   */
  readonly gekuerzt: string[];
}

const LEERE_ANSICHT: Ansicht = { direkt: null, gruppen: [], flach: [], gekuerzt: [] };

/**
 * Globale Suche als Kommandopalette (Strg+K / ⌘K).
 *
 * Die Komponente traegt BEIDES: den Such-Trigger in der Kopfzeile und die
 * Palette selbst. So bleiben Auslöser, Zustand, Tastenkürzel und Stil an einem
 * Ort — und die Kopfzeile (app.scss) bekommt keine einzige Zeile Stil dazu; ihr
 * 8-kB-Budget ist ausgereizt.
 *
 * Warum ein EIGENER `<dialog>`-Kern statt `app-dialog`: Die Palette braucht eine
 * andere Geometrie (oben angesetzt, breit, Eingabefeld als Kopf, scrollende
 * Ergebnisliste) als die zentrierte 34-rem-Karte des Dialogs. Dessen Stile sind
 * komponentengekapselt und liessen sich nur mit `::ng-deep` biegen. Der Kern —
 * natives `showModal()` (Top-Layer, Fokusfalle, `inert`-Hintergrund,
 * Fokus-Rueckgabe), `(cancel)` fuer Escape, Backdrop-Klick — ist derselbe; der
 * referenzgezaehlte Body-Scroll-Lock wird sogar geteilt (`scrollSperren`).
 *
 * Tastatur (WAI-ARIA Combobox, Vorbild `referenz-wahl`): ↑/↓ ueber alle Gruppen
 * hinweg, Home/End, Enter oeffnet, Escape schliesst. Der Fokus bleibt im
 * Eingabefeld; die aktive Zeile wird ueber `aria-activedescendant` gemeldet.
 */
@Component({
  selector: 'app-kommandopalette',
  imports: [],
  templateUrl: './kommandopalette.html',
  styleUrl: './kommandopalette.scss',
})
export class Kommandopalette implements OnDestroy {
  private readonly router = inject(Router);
  private readonly suche = inject(SucheService);

  private readonly dlg = viewChild<ElementRef<HTMLDialogElement>>('dlg');
  private readonly feld = viewChild<ElementRef<HTMLInputElement>>('feld');

  protected readonly listboxId = 'kp-listbox';

  protected readonly offen = signal(false);
  protected readonly begriff = signal('');
  /** Sichtbarer Ladezustand — erst nach kurzer Frist, damit nichts flackert. */
  protected readonly ladeAnzeige = signal(false);
  protected readonly fehler = signal(false);
  protected readonly aktivIndex = signal(0);
  private readonly ergebnis = signal<SucheErgebnis | null>(null);

  private readonly such$ = new Subject<string>();
  /** Race-Guard: nur die Antwort auf die JUENGSTE Anfrage darf schreiben. */
  private reqId = 0;
  private ladeTimer: ReturnType<typeof setTimeout> | null = null;
  /** true, solange eine Anfrage laeuft (steuert die verzoegerte Anzeige). */
  private laeuft = false;

  /** Mac zeigt ⌘K, alles andere Strg K. */
  protected readonly kuerzel = this.istMac() ? '⌘ K' : 'Strg K';

  protected readonly ansicht = computed<Ansicht>(() => {
    const e = this.ergebnis();
    if (!e) return LEERE_ANSICHT;

    const direktTreffer = e.direkttreffer;
    const flach: SucheTreffer[] = [];
    let direkt: Zeile | null = null;
    if (direktTreffer) {
      direkt = { treffer: direktTreffer, idx: 0 };
      flach.push(direktTreffer);
    }

    // Der Direkttreffer steht oben — in den Gruppen darf er nicht ein zweites
    // Mal auftauchen (der Server liefert ihn laut Vertrag AUCH in `treffer`).
    const rest = (e.treffer ?? []).filter(
      (t) => !direktTreffer || t.id !== direktTreffer.id || t.typ !== direktTreffer.typ,
    );

    const mehrJeTyp = new Map<string, boolean>();
    for (const k of e.kategorien ?? []) mehrJeTyp.set(k.typ, k.mehr_vorhanden);

    // Die Arten in Ankunftsfolge einsammeln (auch dem Server neu hinzugefuegte
    // faellt so nicht unter den Tisch), Zeilen je Art buendeln.
    const jeTyp = new Map<string, SucheTreffer[]>();
    for (const t of rest) {
      const liste = jeTyp.get(t.typ);
      if (liste) liste.push(t);
      else jeTyp.set(t.typ, [t]);
    }

    // Die RANGORDNUNG DES SERVERS entscheidet, nicht die Navigationsreihenfolge.
    // Der Server rankt kategorieuebergreifend (0 = Kennung exakt … 3 = nur ueber
    // eine Beziehung). Wuerde hier stur nach `SUCHE_ORDNUNG` gruppiert, stuende
    // ein Rang-3-Kontakt ueber einem Rang-1-Artikel — und der Nutzer suchte
    // seinen Treffer wieder „irgendwo in der elend langen Liste".
    //
    // Die Gruppierung bleibt (sie hilft beim Scannen), aber die Gruppen ordnen
    // sich nach ihrem BESTEN Rang. Bei Ranggleichheit entscheidet die feste
    // `SUCHE_ORDNUNG` als Tiebreaker — so flackert die Reihenfolge nicht.
    const ordnungsIndex = (typ: string): number => {
      const i = SUCHE_ORDNUNG.indexOf(typ as SucheEntityTyp);
      return i < 0 ? SUCHE_ORDNUNG.length : i; // Unbekannte Arten ans Ende.
    };

    const roh = [...jeTyp.entries()].map(([typ, treffer]) => {
      // Innerhalb der Gruppe nach Rang; bei Gleichstand bleibt die Serverfolge
      // (Array.prototype.sort ist stabil).
      const sortiert = [...treffer].sort((a, b) => a.rang - b.rang);
      return {
        typ,
        treffer: sortiert,
        besterRang: sortiert.length ? sortiert[0].rang : Number.MAX_SAFE_INTEGER,
      };
    });
    roh.sort(
      (a, b) => a.besterRang - b.besterRang || ordnungsIndex(a.typ) - ordnungsIndex(b.typ),
    );

    // Die flache Tastaturliste entsteht ERST JETZT — sie folgt der sichtbaren
    // Reihenfolge exakt, sonst spraenge der Pfeil woandershin als das Auge.
    const gruppen: Gruppe[] = roh.map((g) => ({
      typ: g.typ,
      label: SUCHE_KATEGORIE[g.typ as SucheEntityTyp] ?? g.typ,
      mark: SUCHE_MARK[g.typ as SucheEntityTyp] ?? '··',
      mehr: mehrJeTyp.get(g.typ) === true,
      zeilen: g.treffer.map((t) => {
        const zeile: Zeile = { treffer: t, idx: flach.length };
        flach.push(t);
        return zeile;
      }),
    }));

    // Kategorien, die es gaebe, von denen aber KEINE Zeile in der Liste steht:
    // entweder hat die Gesamtgrenze sie ganz verdraengt (`anzahl = 0`) oder ihre
    // einzige gelieferte Zeile ist der oben stehende Direkttreffer. In beiden
    // Faellen ginge der Hinweis „hier gaebe es mehr" sonst verloren.
    const gekuerzt = (e.kategorien ?? [])
      .filter((k) => k.mehr_vorhanden && !gruppen.some((g) => g.typ === k.typ))
      .map((k) => SUCHE_KATEGORIE[k.typ] ?? k.typ);

    return { direkt, gruppen, flach, gekuerzt };
  });

  /**
   * Anzeigezustand des Ergebnisbereichs.
   *
   * `treffer` gilt auch, WAEHREND eine neue Anfrage laeuft: die alte Liste
   * bleibt stehen, statt bei jedem Tastendruck auf einen Ladezustand zu
   * springen. Der Ladehinweis sitzt dezent im Kopf.
   */
  protected readonly zustand = computed<
    'hinweis' | 'zu_kurz' | 'wartet' | 'fehler' | 'leer' | 'treffer'
  >(() => {
    if (!this.begriff().trim()) return 'hinweis';
    if (this.fehler()) return 'fehler';
    // Vor 'leer': der Server hat hier nicht gesucht, also darf die Palette auch
    // nicht „nichts gefunden" behaupten.
    if (this.zuKurz()) return 'zu_kurz';
    if (!this.ergebnis()) return 'wartet';
    return this.ansicht().flach.length ? 'treffer' : 'leer';
  });

  /**
   * Der Begriff traegt kein Token mit zwei Zeichen — der Server sucht dann GAR
   * NICHT. „Keine Treffer" waere hier gelogen: es wurde nicht gesucht.
   */
  protected readonly zuKurz = computed(() => begriffZuKurz(this.begriff()));

  /**
   * Der Artikelstamm bleibt aussen vor (kein Token mit drei Zeichen, kein
   * Hero-Operator). Betrifft NUR Artikel — Leistungen, Belege und Kontakte
   * werden weiter durchsucht, ihr Serverzweig kennt die Trigramm-Grenze nicht.
   */
  protected readonly ohneArtikel = computed(() => artikelUebersprungen(this.begriff()));

  protected readonly minLaenge = SUCHE_MIN_LAENGE;
  protected readonly trigrammMin = SUCHE_TRIGRAMM_MIN;

  /** Screenreader-Meldung: nur, wenn wirklich gesucht wurde. */
  protected readonly statusText = computed(() => {
    if (!this.begriff().trim()) return '';
    if (this.ladeAnzeige()) return 'Suche läuft …';
    if (this.fehler()) return 'Die Suche ist fehlgeschlagen.';
    if (this.zuKurz()) return `Bitte mindestens ${SUCHE_MIN_LAENGE} Zeichen eingeben.`;
    if (!this.ergebnis()) return '';
    const a = this.ansicht();
    const n = a.flach.length;
    if (n === 0) return 'Keine Treffer.';
    const direkt = a.direkt ? ' Direkttreffer an erster Stelle.' : '';
    // Gekuerzt heisst gekuerzt — auch fuer den Screenreader.
    const mehr =
      a.gekuerzt.length || a.gruppen.some((g) => g.mehr) ? ' Liste gekürzt.' : '';
    return `${n} Treffer.${direkt}${mehr}`;
  });

  constructor() {
    this.such$
      .pipe(
        debounceTime(200),
        distinctUntilChanged(),
        switchMap((q) => {
          const id = ++this.reqId;
          // Der leere Begriff laeuft bewusst DURCH den Strom (statt nur am Feld
          // abgefangen zu werden): sonst merkte sich `distinctUntilChanged` den
          // alten Begriff, und „abc" → leeren → „abc" loeste keine Suche mehr aus.
          if (!q) return of({ id, res: null as SucheErgebnis | null, leer: true });
          this.anfrageBeginnt();
          // Fehler innen abfangen, sonst stirbt der Suchstrom beim ersten 500.
          return this.suche.suchen(q).pipe(
            catchError(() => of(null)),
            // Die Antwort traegt ihre Anfrage-Nummer mit: eine ueberholte
            // (langsamere, aeltere) Antwort darf die juengste nicht ueberschreiben.
            map((res) => ({ id, res, leer: false })),
          );
        }),
        takeUntilDestroyed(),
      )
      .subscribe(({ id, res, leer }) => {
        if (id !== this.reqId) return;
        this.anfrageEndet();
        this.fehler.set(!leer && res === null);
        this.ergebnis.set(res);
        this.aktivIndex.set(0);
      });

    // Die aktive Zeile muss sichtbar bleiben, auch wenn die Liste scrollt — der
    // Fokus bleibt ja im Eingabefeld und zieht sie nicht mit.
    effect(() => {
      const id = this.aktivOptionId();
      if (!id || !this.offen()) return;
      queueMicrotask(() => {
        // `?.` auch auf der Methode: das Test-DOM kennt scrollIntoView nicht.
        document.getElementById(id)?.scrollIntoView?.({ block: 'nearest' });
      });
    });
  }

  ngOnDestroy(): void {
    // Wird die Komponente zerstoert, waehrend die Palette offen ist, bliebe der
    // Scroll-Lock sonst fuer immer stehen.
    const el = this.dlg()?.nativeElement;
    if (el?.open) this.dialogSchliessen(el);
    if (this.ladeTimer) clearTimeout(this.ladeTimer);
  }

  // --- Oeffnen / Schliessen -------------------------------------------------

  /**
   * Globales Tastenkuerzel. Strg+K / ⌘K feuert bewusst AUCH aus Eingabefeldern
   * heraus (Konvention bei Kommandopaletten). `preventDefault()` verhindert die
   * Browser-Belegung (Suchleiste); im Projekt haengt an Strg+K nichts.
   */
  @HostListener('document:keydown', ['$event'])
  protected onGlobaleTaste(event: KeyboardEvent): void {
    if (event.key !== 'k' && event.key !== 'K') return;
    if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
    event.preventDefault();
    if (this.offen()) this.schliessen();
    else this.oeffnen();
  }

  protected oeffnen(): void {
    if (this.offen()) return;
    this.offen.set(true);
    const el = this.dlg()?.nativeElement;
    if (!el) return;
    if (typeof el.showModal === 'function') el.showModal();
    else el.setAttribute('open', ''); // Fallback (Test-DOM)
    scrollSperren();
    // Nach dem Rendern in den Eingabestrang; der bestehende Begriff wird
    // markiert, damit Tippen ihn ersetzt und Weitertippen moeglich bleibt.
    queueMicrotask(() => this.feld()?.nativeElement.select());
  }

  protected schliessen(): void {
    if (!this.offen()) return;
    this.offen.set(false);
    const el = this.dlg()?.nativeElement;
    if (el?.open || el?.hasAttribute('open')) this.dialogSchliessen(el);
    // Der Fokus geht nativ an das Element zurueck, das ihn vor `showModal()`
    // hatte (Trigger bzw. das Feld, aus dem heraus Strg+K kam).
  }

  private dialogSchliessen(el: HTMLDialogElement): void {
    if (typeof el.close === 'function') el.close();
    else el.removeAttribute('open');
    scrollFreigeben();
  }

  /** Escape (nativ) — Zustand bleibt in EINER Hand. */
  protected onCancel(event: Event): void {
    event.preventDefault();
    this.schliessen();
  }

  /** Klick auf die abgedunkelte Flaeche. */
  protected onKlick(event: MouseEvent): void {
    if (event.target === this.dlg()?.nativeElement) this.schliessen();
  }

  // --- Suche ----------------------------------------------------------------

  protected onEingabe(wert: string): void {
    this.begriff.set(wert);
    const q = wert.trim();
    if (!q) {
      // Sofort zurueck zum Hinweis (ohne auf den Debounce zu warten) und die
      // laufende Anfrage entwerten. Der leere Begriff geht trotzdem in den Strom
      // — siehe Begruendung im Konstruktor.
      this.reqId += 1;
      this.anfrageEndet();
      this.ergebnis.set(null);
      this.fehler.set(false);
      this.aktivIndex.set(0);
    }
    this.such$.next(q);
  }

  private anfrageBeginnt(): void {
    this.laeuft = true;
    this.fehler.set(false);
    if (this.ladeTimer) return;
    // Erst nach 180 ms zeigen: schnelle Antworten sollen nicht aufblitzen.
    this.ladeTimer = setTimeout(() => {
      this.ladeTimer = null;
      if (this.laeuft) this.ladeAnzeige.set(true);
    }, 180);
  }

  private anfrageEndet(): void {
    this.laeuft = false;
    if (this.ladeTimer) {
      clearTimeout(this.ladeTimer);
      this.ladeTimer = null;
    }
    this.ladeAnzeige.set(false);
  }

  // --- Tastatur in der Liste ------------------------------------------------

  /**
   * Zugaenglicher Name der Kategoriegruppe. Die sichtbare Ueberschrift ist
   * `aria-hidden` (unter `listbox` laesst ARIA nur option/group zu) — die
   * Kuerzungs-Information muss deshalb HIER stehen, sonst hoert ein
   * Screenreader-Nutzer nichts davon.
   */
  protected gruppeAria(g: Gruppe): string {
    const n = g.zeilen.length;
    return g.mehr ? `${g.label} — ${n} von mehr Treffern` : `${g.label} — ${n} Treffer`;
  }

  protected optionId(idx: number): string {
    return `kp-opt-${idx}`;
  }

  protected aktivOptionId(): string | null {
    const n = this.ansicht().flach.length;
    const i = this.aktivIndex();
    return n > 0 && i >= 0 && i < n ? this.optionId(i) : null;
  }

  protected onTaste(event: KeyboardEvent): void {
    const n = this.ansicht().flach.length;
    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault();
        if (n) this.aktivIndex.update((i) => (i + 1) % n);
        break;
      case 'ArrowUp':
        event.preventDefault();
        if (n) this.aktivIndex.update((i) => (i - 1 + n) % n);
        break;
      case 'Home':
        if (n) {
          event.preventDefault();
          this.aktivIndex.set(0);
        }
        break;
      case 'End':
        if (n) {
          event.preventDefault();
          this.aktivIndex.set(n - 1);
        }
        break;
      case 'Enter': {
        const t = this.ansicht().flach[this.aktivIndex()];
        if (t) {
          event.preventDefault();
          this.springen(t);
        }
        break;
      }
      case 'Escape':
        event.preventDefault();
        this.schliessen();
        break;
    }
  }

  protected springen(t: SucheTreffer): void {
    const basis = SUCHE_ZIEL[t.typ];
    if (!basis) return; // Unbekannte Art: nicht ins Leere navigieren.
    this.schliessen();
    void this.router.navigate([basis, t.id]);
  }

  private istMac(): boolean {
    if (typeof navigator === 'undefined') return false;
    return /Mac|iPhone|iPad/i.test(navigator.platform || navigator.userAgent);
  }
}
