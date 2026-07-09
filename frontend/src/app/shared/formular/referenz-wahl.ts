import {
  Component,
  DestroyRef,
  ElementRef,
  effect,
  inject,
  input,
  signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormControl, ReactiveFormsModule } from '@angular/forms';
import {
  Observable,
  Subject,
  catchError,
  debounceTime,
  distinctUntilChanged,
  of,
  switchMap,
} from 'rxjs';
import { feldFehlerText } from './feld-fehler';

/** Eine auswählbare Referenz (Kontakt, Auftrag, Liegenschaft …). */
export interface RefOption {
  id: string;
  label: string;
  /** Optionale Zweitzeile (Nummer, Ort …). */
  sub?: string | null;
}

/** Ladefunktion: liefert zum Suchbegriff die passenden Optionen. */
export type RefSuche = (q: string) => Observable<RefOption[]>;

let refSeq = 0;

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

  private readonly such$ = new Subject<string>();
  private readonly destroyRef = inject(DestroyRef);
  private readonly sucheInput = viewChild<ElementRef<HTMLInputElement>>('sucheInput');
  private valueSub = false;

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
        this.offen.set(true);
      });

    // Externer Reset (Aufrufer setzt das Control auf '') räumt die Anzeige ab.
    effect(() => {
      const c = this.control();
      if (this.valueSub) return;
      this.valueSub = true;
      c.valueChanges.pipe(takeUntilDestroyed(this.destroyRef)).subscribe((v) => {
        if (!v && this.gewaehlt()) {
          this.gewaehlt.set(null);
          this.suchtext.set('');
          this.offen.set(false);
        }
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

  protected aktivOptionId(): string | null {
    const i = this.aktivIndex();
    return i >= 0 && i < this.optionen().length ? `${this.id}-opt-${i}` : null;
  }

  protected onFokus(): void {
    if (this.gewaehlt()) return;
    if (this.optionen().length === 0 && !this.ladend()) {
      this.such$.next(this.suchtext());
    } else {
      this.offen.set(true);
    }
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
