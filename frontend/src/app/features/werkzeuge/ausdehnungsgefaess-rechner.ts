import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule } from '@angular/forms';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { dezimalValidator } from '../../shared/formular/dezimal';
import { RechnerBasis } from './rechner-basis';
import { zahlAus } from './eingabe';
import {
  MAG_HAFTUNG,
  MAG_HOEHE_STANDARD,
  MAG_NENNGROESSEN,
  MAG_SICHERHEITSVENTILE,
  MAG_SV_STANDARD,
  MAG_TEMPERATUREN,
  MAG_TEMP_STANDARD,
  ausdehnungsgefaess,
  magFehlerText,
  zahlGanz,
  zahlKurz,
} from './rechner';

/** Größte handelsübliche Nenngröße — darüber ist es eine Sonderauslegung. */
const GROESSTE = MAG_NENNGROESSEN[MAG_NENNGROESSEN.length - 1];

/**
 * Membran-Ausdehnungsgefäß (MAG) — portiert aus `NotizApp_Win`
 * (`Controls/AusdehnungsgefaessRechner.xaml[.cs]`), Rechenvorschrift unverändert:
 *
 *     V_n = (V_e + V_wv) · (p_e + 1) / (p_e − p_0)
 *
 * Ausdehnungskoeffizienten, Nenngrößenliste und die Konventionen (Wasservorlage
 * max(0,5 %; 3 l), p_0 = h/10 + 0,3 bar, p_e = SV − 0,5 bar) sind 1:1 die der
 * NotizApp. Das Ergebnis ist eine **Auslegungshilfe**, kein Nachweis — die
 * Fehldimensionierung eines MAG ist ein echter Anlagenschaden.
 */
@Component({
  selector: 'app-ausdehnungsgefaess-rechner',
  imports: [ReactiveFormsModule, Feld],
  templateUrl: './ausdehnungsgefaess-rechner.html',
  styleUrl: './rechner.scss',
})
export class AusdehnungsgefaessRechner extends RechnerBasis {
  private readonly fb = inject(FormBuilder);

  protected readonly haftung = MAG_HAFTUNG;
  protected readonly tempOptionen: FeldOption[] = MAG_TEMPERATUREN.map((t) => ({
    wert: t.wert,
    label: t.label,
  }));
  protected readonly svOptionen: FeldOption[] = MAG_SICHERHEITSVENTILE.map((s) => ({
    wert: s.wert,
    label: s.label,
  }));

  protected readonly form = this.fb.group({
    inhalt: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
    temp: this.fb.control(MAG_TEMP_STANDARD, { nonNullable: true }),
    hoehe: this.fb.control(MAG_HOEHE_STANDARD, {
      nonNullable: true,
      validators: [dezimalValidator],
    }),
    sv: this.fb.control(MAG_SV_STANDARD, { nonNullable: true }),
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
    const temp = MAG_TEMPERATUREN.find((t) => t.wert === v.temp) ?? MAG_TEMPERATUREN[2];
    const sv = MAG_SICHERHEITSVENTILE.find((s) => s.wert === v.sv) ?? MAG_SICHERHEITSVENTILE[1];
    return {
      inhalt: zahlAus(v.inhalt),
      hoehe: zahlAus(v.hoehe),
      temp,
      sv,
      svWert: Number(sv.wert),
    };
  });

  private readonly rechnung = computed(() => {
    const { inhalt, hoehe, temp, svWert } = this.werte();
    return ausdehnungsgefaess(inhalt, temp.beta, svWert, hoehe);
  });

  protected readonly ergebnis = computed(() => {
    const r = this.rechnung();
    return r.ok ? r.ergebnis : null;
  });

  /** Fehlertext (Druckdifferenz zu klein). Bei fehlender Eingabe kein Alarm. */
  protected readonly fehler = computed(() => {
    const r = this.rechnung();
    return !r.ok && r.fehler === 'DRUCK' ? magFehlerText(r.fehler) : '';
  });

  /** „25 Liter" bzw. „> 1.000 l (Sonderfall)" — wie in der NotizApp. */
  protected readonly groesseText = computed(() => {
    const e = this.ergebnis();
    if (!e) return '—';
    return e.empfohlen != null ? `${e.empfohlen} Liter` : `> ${zahlGanz(GROESSTE)} l (Sonderfall)`;
  });

  protected readonly detailText = computed(() => {
    const e = this.ergebnis();
    if (!e) return '';
    return `Rechnerisch nötig: ${zahlKurz(e.vn)} l`;
  });

  protected readonly istSonderfall = computed(
    () => !!this.ergebnis() && !this.ergebnis()!.empfohlen,
  );

  protected z(n: number): string {
    return zahlKurz(n);
  }

  protected override ergebnisText(): string {
    const e = this.ergebnis();
    if (!e) return '';
    const { inhalt, temp } = this.werte();
    return [
      'Ausdehnungsgefäß (MAG), überschlägig',
      this.kontextZeile(),
      `- Anlageninhalt: ${zahlKurz(inhalt!)} l, max. Vorlauf: ${temp.label}`,
      `- Ausdehnung V_e: ${zahlKurz(e.ve)} l, Wasservorlage V_wv: ${zahlKurz(e.vwv)} l`,
      `- Vordruck p_0: ${zahlKurz(e.p0)} bar, Enddruck p_e: ${zahlKurz(e.pe)} bar`,
      `- Rechnerisch nötig: ${zahlKurz(e.vn)} l → empfohlen: ` +
        (e.empfohlen != null ? `${e.empfohlen} l` : 'Sonderauslegung'),
      `- Hinweis: ${MAG_HAFTUNG}`,
    ]
      .filter((z): z is string => z !== null)
      .join('\n');
  }

  protected override positionsText(): string {
    const e = this.ergebnis();
    if (!e) return '';
    const { inhalt, temp } = this.werte();
    return (
      `Ausdehnungsgefäß (überschlägig): Anlageninhalt ${zahlKurz(inhalt!)} l, max. Vorlauf ` +
      `${temp.label}, p_0 ${zahlKurz(e.p0)} bar, p_e ${zahlKurz(e.pe)} bar → nötig ` +
      `${zahlKurz(e.vn)} l, empfohlen ` +
      (e.empfohlen != null ? `${e.empfohlen} l` : 'Sonderauslegung') +
      `. ${MAG_HAFTUNG}`
    );
  }
}
