import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule } from '@angular/forms';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { dezimalValidator } from '../../shared/formular/dezimal';
import { RechnerBasis } from './rechner-basis';
import { zahlAus } from './eingabe';
import {
  FBH_STANDARD,
  FBH_TYPEN,
  HK_INHALT_STANDARD,
  ROHR_STANDARD,
  ROHR_TYPEN,
  wasserinhalt,
  zahlLiter,
} from './rechner';

/**
 * Anlagenwasserinhalt — portiert aus `NotizApp_Win`
 * (`Controls/WasserinhaltRechner.xaml[.cs]`), Rechenlogik unverändert:
 *
 *     Summe = Rohr(L × l/m) + FBH(L × l/m) + HK(Anzahl × Inhalt)
 *             + Wärmeerzeuger + Pufferspeicher
 *
 * Die l/m-Kennwerte, die Vorauswahlen (Kupfer 22×1, FBH 16×2) und die
 * Vorbelegung „5 l je Heizkörper" sind 1:1 die der NotizApp. Ein leeres oder
 * nicht positives Feld zählt dort als 0 — hier ebenso.
 */
@Component({
  selector: 'app-wasserinhalt-rechner',
  imports: [ReactiveFormsModule, Feld],
  templateUrl: './wasserinhalt-rechner.html',
  styleUrl: './rechner.scss',
})
export class WasserinhaltRechner extends RechnerBasis {
  private readonly fb = inject(FormBuilder);

  protected readonly rohrOptionen: FeldOption[] = ROHR_TYPEN.map((r) => ({
    wert: r.wert,
    label: r.label,
  }));
  protected readonly fbhOptionen: FeldOption[] = FBH_TYPEN.map((r) => ({
    wert: r.wert,
    label: r.label,
  }));

  protected readonly form = this.fb.group({
    rohrLaenge: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
    rohrTyp: this.fb.control(ROHR_STANDARD, { nonNullable: true }),
    fbhLaenge: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
    fbhTyp: this.fb.control(FBH_STANDARD, { nonNullable: true }),
    hkAnzahl: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
    hkInhalt: this.fb.control(HK_INHALT_STANDARD, {
      nonNullable: true,
      validators: [dezimalValidator],
    }),
    erzeuger: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
    puffer: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
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
    const rohr = ROHR_TYPEN.find((r) => r.wert === v.rohrTyp) ?? ROHR_TYPEN[0];
    const fbh = FBH_TYPEN.find((r) => r.wert === v.fbhTyp) ?? FBH_TYPEN[0];
    return {
      rohrTyp: rohr,
      fbhTyp: fbh,
      eingabe: {
        rohrLaenge: zahlAus(v.rohrLaenge),
        rohrLProM: rohr.lProM,
        fbhLaenge: zahlAus(v.fbhLaenge),
        fbhLProM: fbh.lProM,
        hkAnzahl: zahlAus(v.hkAnzahl),
        hkInhalt: zahlAus(v.hkInhalt),
        erzeuger: zahlAus(v.erzeuger),
        puffer: zahlAus(v.puffer),
      },
    };
  });

  protected readonly ergebnis = computed(() => wasserinhalt(this.werte().eingabe));

  protected readonly summeText = computed(() => {
    const e = this.ergebnis();
    return e ? `${zahlLiter(e.summe)} Liter` : '—';
  });

  /** Aufschlüsselung wie in der NotizApp: „Rohr 37,7 l  +  Heizkörper 40 l". */
  protected readonly aufschluesselung = computed(
    () =>
      this.ergebnis()
        ?.teile.map((t) => `${t.label} ${zahlLiter(t.liter)} l`)
        .join('  +  ') ?? '',
  );

  protected override ergebnisText(): string {
    const e = this.ergebnis();
    if (!e) return '';
    return [
      'Anlagenwasserinhalt (Grundlage Befüllmenge/Nachspeisung, VDI 2035)',
      this.kontextZeile(),
      ...e.teile.map((t) => `- ${t.label}: ${zahlLiter(t.liter)} l`),
      `- Summe: ${zahlLiter(e.summe)} Liter`,
    ]
      .filter((z): z is string => z !== null)
      .join('\n');
  }

  protected override positionsText(): string {
    const e = this.ergebnis();
    if (!e) return '';
    const teile = e.teile.map((t) => `${t.label} ${zahlLiter(t.liter)} l`).join(' + ');
    return (
      `Anlagenwasserinhalt: ${teile} = ${zahlLiter(e.summe)} Liter ` +
      '(Grundlage für Befüllmenge/Nachspeisung, VDI 2035).'
    );
  }
}
