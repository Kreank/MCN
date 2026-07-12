import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule } from '@angular/forms';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { dezimalValidator } from '../../shared/formular/dezimal';
import { RechnerBasis } from './rechner-basis';
import { zahlAus } from './eingabe';
import {
  BAUARTEN,
  DELTA_NORM,
  HEIZKOERPER_HAFTUNG,
  NORM_BEDINGUNG,
  heizkoerper,
  heizkoerperFehlerText,
  zahlEine,
  zahlGanz,
  zahlKurz,
} from './rechner';

/**
 * Heizkörper-Umrechnung auf einen anderen Betriebspunkt — der Klassiker bei der
 * Umstellung auf eine Wärmepumpe: Was leistet ein Heizkörper, dessen
 * Katalogleistung für 75/65/20 gilt, bei z. B. 55/45/20?
 *
 *     Q = Q_norm · (ΔΘ_ln / 49,83)^n
 *     ΔΘ_ln = (T_VL − T_RL) / ln((T_VL − T_Raum) / (T_RL − T_Raum))
 *
 * Nicht aus der NotizApp portiert (dort nicht vorhanden) — neu gebaut.
 * Der Exponent n ist ein Erfahrungs-/Herstellerwert, KEINE Normtabelle; er ist
 * vorbelegt und frei überschreibbar. DIN EN 442 wird als Bezug der Normleistung
 * genannt, es werden keine Normwerte abgedruckt.
 */
@Component({
  selector: 'app-heizkoerper-rechner',
  imports: [ReactiveFormsModule, Feld],
  templateUrl: './heizkoerper-rechner.html',
  styleUrl: './rechner.scss',
})
export class HeizkoerperRechner extends RechnerBasis {
  private readonly fb = inject(FormBuilder);

  protected readonly haftung = HEIZKOERPER_HAFTUNG;
  protected readonly norm = NORM_BEDINGUNG;
  protected readonly deltaNorm = zahlKurz(DELTA_NORM);

  protected readonly bauartOptionen: FeldOption[] = BAUARTEN.map((b) => ({
    wert: b.wert,
    label: `${b.label} — n = ${b.exponent}`,
  }));

  protected readonly form = this.fb.group({
    normleistung: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
    vl: this.fb.control('55', { nonNullable: true, validators: [dezimalValidator] }),
    rl: this.fb.control('45', { nonNullable: true, validators: [dezimalValidator] }),
    raum: this.fb.control('20', { nonNullable: true, validators: [dezimalValidator] }),
    bauart: this.fb.control('PLATTE', { nonNullable: true }),
    exponent: this.fb.control('1,30', { nonNullable: true, validators: [dezimalValidator] }),
  });

  private readonly aenderung = signal(0);

  constructor() {
    super();
    // Bauart belegt den Exponenten vor; er bleibt überschreibbar (das Datenblatt
    // des Herstellers schlägt jeden Erfahrungswert).
    this.form.controls.bauart.valueChanges.pipe(takeUntilDestroyed()).subscribe((b) => {
      const e = BAUARTEN.find((x) => x.wert === b)?.exponent;
      if (e) this.form.controls.exponent.setValue(e);
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
      q: zahlAus(v.normleistung),
      vl: zahlAus(v.vl),
      rl: zahlAus(v.rl),
      raum: zahlAus(v.raum),
      n: zahlAus(v.exponent),
      bauartLabel: BAUARTEN.find((b) => b.wert === v.bauart)?.label ?? '',
    };
  });

  /** `null` = Eingabe unvollständig; sonst Erfolg oder fachlicher Fehler. */
  private readonly rechnung = computed(() => {
    const { q, vl, rl, raum, n } = this.werte();
    if (q == null || vl == null || rl == null || raum == null || n == null) return null;
    return heizkoerper(q, vl, rl, raum, n);
  });

  protected readonly ergebnis = computed(() => {
    const r = this.rechnung();
    return r?.ok ? r.ergebnis : null;
  });

  /** Fachlicher Fehler (z. B. Vorlauf unter Raumtemperatur) — als Klartext. */
  protected readonly fehler = computed(() => {
    const r = this.rechnung();
    return r && !r.ok ? heizkoerperFehlerText(r.fehler) : null;
  });

  protected readonly wattText = computed(() => {
    const e = this.ergebnis();
    return e ? `${zahlGanz(e.watt)} W` : '—';
  });

  protected readonly kwText = computed(() => {
    const e = this.ergebnis();
    return e ? `${zahlEine(e.kw)} kW` : '';
  });

  /** „62 %" — wie viel der Normleistung am neuen Betriebspunkt übrig bleibt. */
  protected readonly faktorText = computed(() => {
    const e = this.ergebnis();
    return e ? `${zahlKurz(e.faktor * 100)} %` : '—';
  });

  protected readonly deltaLnText = computed(() => {
    const e = this.ergebnis();
    return e ? `${zahlKurz(e.deltaLn)} K` : '—';
  });

  protected readonly betriebspunkt = computed(() => {
    const { vl, rl, raum } = this.werte();
    if (vl == null || rl == null || raum == null) return '';
    return `${zahlKurz(vl)}/${zahlKurz(rl)}/${zahlKurz(raum)}`;
  });

  protected readonly formelText = computed(() => {
    const e = this.ergebnis();
    if (!e) return '';
    const { q, n } = this.werte();
    return (
      `${zahlGanz(q!)} W × (${zahlKurz(e.deltaLn)} K ÷ ${this.deltaNorm} K)^${zahlKurz(n!)} ` +
      `= ${zahlGanz(e.watt)} W`
    );
  });

  protected override ergebnisText(): string {
    const e = this.ergebnis();
    if (!e) return '';
    const { q, n, bauartLabel } = this.werte();
    return [
      'Heizkörperleistung am geänderten Betriebspunkt',
      this.kontextZeile(),
      `- Normleistung (${this.norm.vl}/${this.norm.rl}/${this.norm.raum}): ${zahlGanz(q!)} W`,
      `- Neuer Betriebspunkt: ${this.betriebspunkt()}`,
      `- Bauart / Exponent: ${bauartLabel} (n = ${zahlKurz(n!)})`,
      `- Übertemperatur ΔΘ_ln: ${zahlKurz(e.deltaLn)} K (Norm: ${this.deltaNorm} K)`,
      `- Leistung: ${zahlGanz(e.watt)} W (${zahlEine(e.kw)} kW) = ${zahlKurz(e.faktor * 100)} % der Normleistung`,
      `- Hinweis: ${HEIZKOERPER_HAFTUNG}`,
    ]
      .filter((z): z is string => z !== null)
      .join('\n');
  }

  protected override positionsText(): string {
    const e = this.ergebnis();
    if (!e) return '';
    const { q, n } = this.werte();
    return (
      `Heizkörper ${zahlGanz(q!)} W (${this.norm.vl}/${this.norm.rl}/${this.norm.raum}) leistet bei ` +
      `${this.betriebspunkt()} noch ${zahlGanz(e.watt)} W / ${zahlEine(e.kw)} kW ` +
      `(${zahlKurz(e.faktor * 100)} %, n = ${zahlKurz(n!)}). ${HEIZKOERPER_HAFTUNG}`
    );
  }
}
