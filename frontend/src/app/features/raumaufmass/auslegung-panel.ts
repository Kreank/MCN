import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { FormBuilder, ReactiveFormsModule } from '@angular/forms';
import { Auslegung, AuslegungIn } from '../../core/raum.model';
import { RaumService } from '../../core/raum.service';
import { dezimalValidator } from '../../shared/formular/dezimal';
import { Feld } from '../../shared/formular/feld';
import { fehlerDetail } from '../../shared/http-fehler';
import { apiZahl, eingabe, zeige } from './raum-rechnen';

/**
 * Ergebnis der Formularprüfung: entweder ein sendbarer PATCH-Body — oder ein
 * Klartextfehler. Beides ohne Angular, damit es sich pur testen lässt.
 */
export type AuslegungPruefung =
  | { readonly ok: true; readonly payload: AuslegungIn }
  | { readonly ok: false; readonly fehler: string };

/**
 * Die beiden Eingaben (deutsche Schreibweise) zu einem PATCH-Body machen.
 *
 * **Leer heißt `null` (zurücksetzen), nicht „unverändert".** Das Formular zeigt
 * beide Felder gleichzeitig; wer eines leert, will es löschen. Ein mehrdeutiger
 * Wert („1.500") wird ABGELEHNT, nicht geraten. Ein Kennwert ≤ 0 wird abgelehnt,
 * weil er die Heizlast still auf 0 W rechnen würde — genau die Lüge, gegen die
 * dieser Slice gebaut ist.
 */
export function auslegungPruefen(aussenRoh: string, kennwertRoh: string): AuslegungPruefung {
  const aussen = eingabe(aussenRoh);
  if (aussen.art === 'fehler') {
    return {
      ok: false,
      fehler:
        `Auslegungs-Außentemperatur: „${aussenRoh}" ist nicht eindeutig. Bitte ohne ` +
        'Tausenderpunkt eingeben (z. B. -12 oder -12,5).',
    };
  }
  const kennwert = eingabe(kennwertRoh);
  if (kennwert.art === 'fehler') {
    return {
      ok: false,
      fehler:
        `Gebäudekennwert: „${kennwertRoh}" ist nicht eindeutig. Bitte ohne Tausenderpunkt ` +
        'eingeben (z. B. 80).',
    };
  }
  if (kennwert.art === 'wert' && !(kennwert.zahl > 0)) {
    return {
      ok: false,
      fehler:
        'Gebäudekennwert: Ein Wert von 0 oder weniger W/m² würde eine Heizlast von 0 W ' +
        'ergeben. Bitte einen echten Kennwert eintragen oder das Feld leer lassen.',
    };
  }
  return {
    ok: true,
    payload: {
      design_outdoor_temp_c: aussen.art === 'wert' ? aussen.api : null,
      heat_load_w_per_m2: kennwert.art === 'wert' ? kennwert.api : null,
    },
  };
}

/**
 * **Auslegungsdaten des Objekts** — Auslegungs-Außentemperatur und
 * Gebäudekennwert (`property.design_outdoor_temp_c` / `heat_load_w_per_m2`,
 * Migration 0089).
 *
 * Warum hier und nicht am Raum: Die Außentemperatur ist eine Eigenschaft des
 * **Standorts**, nicht der Rechnung. Solange sie fehlte, meldete das
 * Hüllflächenverfahren für JEDEN Raum „unbekannt" — und niemand konnte sehen,
 * warum. Dieses Panel ist der Ort, an dem die Lücke sichtbar wird und sich
 * schließen lässt.
 *
 * **Keine Vorbelegung mit Normwerten.** Klimadaten und Kennwerte sind Eingaben
 * des Betriebs; das Produkt liefert bewusst keine DIN-Tabellen mit. Ein
 * geratener Wert wäre schlimmer als ein leeres Feld.
 */
@Component({
  selector: 'app-auslegung-panel',
  imports: [ReactiveFormsModule, Feld],
  templateUrl: './auslegung-panel.html',
  styleUrl: './auslegung-panel.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AuslegungPanel {
  private readonly fb = inject(FormBuilder);
  private readonly svc = inject(RaumService);

  readonly propertyId = input.required<string>();
  /** Der gespeicherte Stand (aus dem Aufmaß). `null` = noch nicht geladen. */
  readonly auslegung = input<Auslegung | null>(null);
  readonly darfAendern = input(false);

  /** Gespeicherte Werte — der Aufrufer lädt danach die Kennzahlen neu. */
  readonly gespeichert = output<Auslegung>();

  private readonly kopf = viewChild<ElementRef<HTMLElement>>('kopf');

  protected readonly speichert = signal(false);
  protected readonly fehler = signal<string | null>(null);
  protected readonly erfolg = signal<string | null>(null);
  protected readonly ansage = signal('');

  protected readonly form = this.fb.group({
    design_outdoor_temp_c: this.fb.control('', {
      nonNullable: true,
      validators: [dezimalValidator],
    }),
    heat_load_w_per_m2: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
  });

  /** Fehlt die Außentemperatur, ist die raumweise Heizlast NICHT berechenbar. */
  protected readonly aussenFehlt = computed(() => {
    const a = this.auslegung();
    return a != null && (a.design_outdoor_temp_c == null || a.design_outdoor_temp_c === '');
  });

  protected readonly kennwertFehlt = computed(() => {
    const a = this.auslegung();
    return a != null && (a.heat_load_w_per_m2 == null || a.heat_load_w_per_m2 === '');
  });

  /**
   * Der Stand, der zuletzt ins Formular übernommen wurde — **inhaltlich**, nicht
   * als Objektidentität. Das Eltern-`computed` baut bei jedem Nachladen (auch
   * nach jedem Raum-Speichern) ein neues Objektliteral; ohne diesen Vergleich
   * feuerte der Effekt bei gleichem Inhalt und warf die laufende Eingabe sowie
   * die Erfolgsmeldung weg.
   */
  private uebernommen: readonly [string | null, string | null] | null = null;

  constructor() {
    // Der gespeicherte Stand ist die Wahrheit — aber nur, wenn er sich WIRKLICH
    // geändert hat und der Anwender nicht gerade tippt. Eine halb getippte Zahl
    // gehört ihm; ein Refresh im Hintergrund darf sie nicht überschreiben.
    effect(() => {
      const stand = this.kanonisch(this.auslegung());
      const alt = this.uebernommen;

      // Inhaltlich nichts Neues → nichts anfassen (auch keine Meldung löschen).
      if (alt !== null && alt[0] === stand[0] && alt[1] === stand[1]) return;
      // Laufende Eingabe gewinnt: sie wird nicht stillschweigend überschrieben.
      if (this.form.dirty) return;

      this.uebernommen = stand;
      this.form.setValue(
        {
          design_outdoor_temp_c: this.feldWert(stand[0]),
          heat_load_w_per_m2: this.feldWert(stand[1]),
        },
        { emitEvent: false },
      );
      this.fehler.set(null);
      this.erfolg.set(null);
    });
  }

  /**
   * Inhaltlicher Fingerabdruck der Auslegungsdaten — auf denselben Zahlenwert
   * normiert („-12.000" und „-12.0" sind derselbe Stand). Nur so lässt sich ein
   * Nachladen von einer echten Änderung unterscheiden.
   */
  private kanonisch(a: Auslegung | null): readonly [string | null, string | null] {
    const eins = (w: string | null | undefined): string | null => {
      if (w == null || w === '') return null;
      const n = Number(w);
      return Number.isFinite(n) ? apiZahl(n) : String(w);
    };
    return [eins(a?.design_outdoor_temp_c), eins(a?.heat_load_w_per_m2)];
  }

  /** API-Wert → Eingabefeld: Komma, **ohne** Tausenderpunkt (sonst mehrdeutig). */
  private feldWert(w: string | null | undefined): string {
    if (w == null || w === '') return '';
    const n = Number(w);
    if (!Number.isFinite(n)) return String(w);
    return apiZahl(n).replace('.', ',');
  }

  /** Fokus auf dieses Panel — der Weg aus der „unbekannt"-Meldung der Raumliste. */
  fokus(): void {
    const el = this.kopf()?.nativeElement;
    if (!el) return;
    el.scrollIntoView({ block: 'center', behavior: 'auto' });
    el.focus();
  }

  speichern(): void {
    if (this.speichert() || !this.darfAendern()) return;
    this.form.markAllAsTouched();
    const v = this.form.getRawValue();
    const geprueft = auslegungPruefen(v.design_outdoor_temp_c, v.heat_load_w_per_m2);
    if (!geprueft.ok) {
      this.fehler.set(geprueft.fehler);
      this.erfolg.set(null);
      return;
    }

    this.speichert.set(true);
    this.fehler.set(null);
    this.erfolg.set(null);
    this.svc.setAuslegung(this.propertyId(), geprueft.payload).subscribe({
      next: (a) => {
        this.speichert.set(false);
        // Der gespeicherte Stand ist ab jetzt der bekannte: das Nachladen des
        // Elternteils reicht ihn gleich wieder herein — inhaltsgleich, also
        // fasst der Effekt weder Formular noch Erfolgsmeldung an.
        this.uebernommen = this.kanonisch(a);
        this.form.markAsPristine();
        const text =
          a.design_outdoor_temp_c == null
            ? 'Auslegungsdaten gespeichert. Ohne Außentemperatur bleibt die Heizlast unbekannt.'
            : `Auslegungsdaten gespeichert (Außentemperatur ${zeige(
                Number(a.design_outdoor_temp_c),
                1,
              )} °C). Die Kennzahlen werden neu geholt.`;
        this.erfolg.set(text);
        this.ansage.set(text);
        this.gespeichert.emit(a);
      },
      error: (err) => {
        this.speichert.set(false);
        this.fehler.set(
          fehlerDetail(err) ?? 'Die Auslegungsdaten konnten nicht gespeichert werden.',
        );
      },
    });
  }
}
