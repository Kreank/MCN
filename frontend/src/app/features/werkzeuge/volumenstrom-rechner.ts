import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule } from '@angular/forms';
import { Feld } from '../../shared/formular/feld';
import { dezimalValidator } from '../../shared/formular/dezimal';
import { RechnerBasis } from './rechner-basis';
import { zahlAus } from './eingabe';
import {
  C_WASSER,
  SPREIZUNG_PRESETS,
  volumenstrom,
  zahlFein,
  zahlGanz,
  zahlKurz,
  zahlZwei,
} from './rechner';

/**
 * Volumenstrom aus Heizleistung und Spreizung — portiert aus `NotizApp_Win`
 * (`Controls/VolumenstromRechner.xaml[.cs]`), Rechenlogik unverändert:
 *
 *     V̇ [l/h] = Q [W] / (c · ΔT)   mit c = 1,163 Wh/(l·K)
 *
 * `1,163 Wh/(l·K)` ist ρ·c_p für Wasser in der im Handwerk üblichen Form; das
 * ist dieselbe Physik wie V̇ = Q / (ρ · c · ΔT). Reine Physik, kein Normbezug.
 */
@Component({
  selector: 'app-volumenstrom-rechner',
  imports: [ReactiveFormsModule, Feld],
  templateUrl: './volumenstrom-rechner.html',
  styleUrl: './rechner.scss',
})
export class VolumenstromRechner extends RechnerBasis {
  private readonly fb = inject(FormBuilder);

  protected readonly presets = SPREIZUNG_PRESETS;
  // `zahlFein` (bis 4 Nachkommastellen) — `zahlKurz` würde auf „1,16" runden und
  // damit eine andere Konstante behaupten, als die Formelzeile zeigt.
  protected readonly cWasser = zahlFein(C_WASSER);

  protected readonly form = this.fb.group({
    leistung: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
    // Vorbelegung 20 K — wie in der NotizApp (`SpreizungBox.Text = "20"`).
    spreizung: this.fb.control('20', { nonNullable: true, validators: [dezimalValidator] }),
  });

  private readonly aenderung = signal(0);

  constructor() {
    super();
    this.form.valueChanges.pipe(takeUntilDestroyed()).subscribe(() => {
      this.rueckmeldung.set('');
      this.aenderung.update((n) => n + 1);
    });
  }

  private readonly werte = computed(() => {
    this.aenderung();
    const v = this.form.getRawValue();
    return { kw: zahlAus(v.leistung), dt: zahlAus(v.spreizung), roh: v.spreizung.trim() };
  });

  protected readonly ergebnis = computed(() => {
    const { kw, dt } = this.werte();
    if (kw == null || dt == null) return null;
    return volumenstrom(kw, dt);
  });

  protected readonly lhText = computed(() => {
    const e = this.ergebnis();
    return e ? `${zahlGanz(e.lh)} l/h` : '—';
  });

  protected readonly m3hText = computed(() => {
    const e = this.ergebnis();
    return e ? `${zahlZwei(e.m3h)} m³/h` : '';
  });

  protected readonly formelText = computed(() => {
    const e = this.ergebnis();
    if (!e) return '';
    const { kw, dt } = this.werte();
    return `${zahlKurz(kw!)} kW ÷ (${this.cWasser} · ${zahlKurz(dt!)} K) = ${zahlGanz(e.lh)} l/h`;
  });

  /** Ist dieses Preset gerade eingestellt? (Text bleibt Träger, nicht die Farbe.) */
  protected istPreset(wert: string): boolean {
    return this.werte().roh === wert;
  }

  protected presetWaehlen(wert: string): void {
    this.form.controls.spreizung.setValue(wert);
  }

  protected override ergebnisText(): string {
    const e = this.ergebnis();
    if (!e) return '';
    const { kw, dt } = this.werte();
    return [
      'Volumenstrom (aus Heizleistung)',
      this.kontextZeile(),
      `- Heizleistung: ${zahlKurz(kw!)} kW`,
      `- Spreizung ΔT: ${zahlKurz(dt!)} K`,
      `- Volumenstrom: ${zahlGanz(e.lh)} l/h (${zahlZwei(e.m3h)} m³/h)`,
      `- Formel: V̇ = Q / (c · ΔT) mit c = ${this.cWasser} Wh/(l·K) für Wasser`,
    ]
      .filter((z): z is string => z !== null)
      .join('\n');
  }

  protected override positionsText(): string {
    const e = this.ergebnis();
    if (!e) return '';
    const { kw, dt } = this.werte();
    return (
      `Volumenstrom: ${zahlKurz(kw!)} kW bei ΔT ${zahlKurz(dt!)} K = ` +
      `${zahlGanz(e.lh)} l/h (${zahlZwei(e.m3h)} m³/h), Wasser (c = ${this.cWasser} Wh/(l·K)).`
    );
  }
}
