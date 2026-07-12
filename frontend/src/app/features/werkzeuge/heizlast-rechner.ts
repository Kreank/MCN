import { Component, computed, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule } from '@angular/forms';
import { inject } from '@angular/core';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { dezimalValidator } from '../../shared/formular/dezimal';
import { RechnerBasis } from './rechner-basis';
import { zahlAus } from './eingabe';
import {
  GEBAEUDE_TYPEN,
  HEIZLAST_HAFTUNG,
  HEIZLAST_STANDARD_TYP,
  heizlast,
  zahlEine,
  zahlGanz,
  zahlKurz,
} from './rechner';

/**
 * Überschlägige Heizlast — portiert aus `NotizApp_Win`
 * (`Controls/HeizlastRechner.xaml[.cs]`), Rechenlogik unverändert:
 *
 *     Heizlast [W] = beheizte Fläche [m²] × spezifische Heizlast [W/m²]
 *
 * Die Kennwerte (80 / 100 / 120 / 150 W/m²) sind die vom Anwender selbst in der
 * NotizApp gepflegten Werte. Es wurde KEINE Normtabelle ergänzt — der Kennwert
 * bleibt frei überschreibbar, so wie dort.
 */
@Component({
  selector: 'app-heizlast-rechner',
  imports: [ReactiveFormsModule, Feld],
  templateUrl: './heizlast-rechner.html',
  styleUrl: './rechner.scss',
})
export class HeizlastRechner extends RechnerBasis {
  private readonly fb = inject(FormBuilder);

  protected readonly haftung = HEIZLAST_HAFTUNG;
  protected readonly typOptionen: FeldOption[] = GEBAEUDE_TYPEN.map((t) => ({
    wert: t.wert,
    label: `${t.label} — ${t.kennwert} W/m²`,
  }));

  protected readonly form = this.fb.group({
    flaeche: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
    typ: this.fb.control(HEIZLAST_STANDARD_TYP, { nonNullable: true }),
    kennwert: this.fb.control(kennwertVon(HEIZLAST_STANDARD_TYP), {
      nonNullable: true,
      validators: [dezimalValidator],
    }),
  });

  /** Zählt jede Formularänderung — Auslöser für die Neuberechnung. */
  private readonly aenderung = signal(0);

  constructor() {
    super();
    // Wie `SetzeKennwertAusTyp()` in der NotizApp: die Auswahl belegt den
    // Kennwert vor, danach bleibt er frei überschreibbar.
    this.form.controls.typ.valueChanges.pipe(takeUntilDestroyed()).subscribe((typ) => {
      this.form.controls.kennwert.setValue(kennwertVon(typ));
    });
    this.form.valueChanges.pipe(takeUntilDestroyed()).subscribe(() => {
      this.rueckmeldung.set('');
      this.aenderung.update((n) => n + 1);
    });
  }

  private readonly werte = computed(() => {
    this.aenderung();
    const v = this.form.getRawValue();
    return {
      flaeche: zahlAus(v.flaeche),
      kennwert: zahlAus(v.kennwert),
      typLabel: GEBAEUDE_TYPEN.find((t) => t.wert === v.typ)?.label ?? '',
    };
  });

  protected readonly ergebnis = computed(() => {
    const { flaeche, kennwert } = this.werte();
    if (flaeche == null || kennwert == null) return null;
    return heizlast(flaeche, kennwert);
  });

  /** Anzeige: „14,4 kW" — C#-Muster `0.0`. */
  protected readonly kwText = computed(() => {
    const e = this.ergebnis();
    return e ? `${zahlEine(e.kw)} kW` : '—';
  });

  /** Anzeige: „120 m² × 120 W/m² = 14.400 W" — wie die NotizApp-Formelzeile. */
  protected readonly formelText = computed(() => {
    const e = this.ergebnis();
    if (!e) return '';
    const { flaeche, kennwert } = this.werte();
    return `${zahlKurz(flaeche!)} m² × ${zahlKurz(kennwert!)} W/m² = ${zahlGanz(e.watt)} W`;
  });

  protected override ergebnisText(): string {
    const e = this.ergebnis();
    if (!e) return '';
    const { flaeche, kennwert, typLabel } = this.werte();
    return [
      'Überschlägige Heizlast (Flächenverfahren)',
      this.kontextZeile(),
      `- Beheizte Fläche: ${zahlKurz(flaeche!)} m²`,
      `- Gebäude/Dämmung: ${typLabel} (${zahlKurz(kennwert!)} W/m²)`,
      `- Heizlast: ${zahlKurz(flaeche!)} m² × ${zahlKurz(kennwert!)} W/m² = ` +
        `${zahlGanz(e.watt)} W (${zahlEine(e.kw)} kW)`,
      `- Hinweis: ${HEIZLAST_HAFTUNG}`,
    ]
      .filter((z): z is string => z !== null)
      .join('\n');
  }

  protected override positionsText(): string {
    const e = this.ergebnis();
    if (!e) return '';
    const { flaeche, kennwert, typLabel } = this.werte();
    return (
      `Überschlägige Heizlast: ${zahlKurz(flaeche!)} m² × ${zahlKurz(kennwert!)} W/m² ` +
      `(${typLabel}) = ${zahlGanz(e.watt)} W / ${zahlEine(e.kw)} kW. ${HEIZLAST_HAFTUNG}`
    );
  }
}

function kennwertVon(typ: string): string {
  return GEBAEUDE_TYPEN.find((t) => t.wert === typ)?.kennwert ?? '';
}
