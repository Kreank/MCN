import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule } from '@angular/forms';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { dezimalValidator } from '../../shared/formular/dezimal';
import { RechnerBasis } from './rechner-basis';
import { zahlAus } from './eingabe';
import { GEWINDE_DN, KATEGORIEN, umrechnen, zahlFein } from './rechner';

/**
 * Einheiten-Umrechner — portiert aus `NotizApp_Win`
 * (`Controls/EinheitenUmrechner.xaml[.cs]`): ein Wert in einer Einheit → alle
 * Einheiten derselben Größe. Faktoren, Reihenfolge und Formatierung der fünf
 * Bestandskategorien (Leistung, Druck, Temperatur, Volumenstrom, Energie) sind
 * unverändert übernommen.
 *
 * Ergänzt (in der NotizApp nicht vorhanden): „Länge / Zoll" und „Wasserhärte".
 */
@Component({
  selector: 'app-einheiten-umrechner',
  imports: [ReactiveFormsModule, Feld],
  templateUrl: './einheiten-umrechner.html',
  styleUrl: './rechner.scss',
})
export class EinheitenUmrechner extends RechnerBasis {
  private readonly fb = inject(FormBuilder);

  protected readonly gewindeDn = GEWINDE_DN;

  protected readonly kategorieOptionen: FeldOption[] = KATEGORIEN.map((k, i) => ({
    wert: String(i),
    label: k.name,
  }));

  protected readonly form = this.fb.group({
    kategorie: this.fb.control('0', { nonNullable: true }),
    einheit: this.fb.control('0', { nonNullable: true }),
    wert: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
  });

  private readonly aenderung = signal(0);

  constructor() {
    super();
    // Kategoriewechsel setzt die Quell-Einheit zurück (wie `Kategorie_Changed`
    // in der NotizApp: `VonBox.SelectedIndex = 0`).
    this.form.controls.kategorie.valueChanges.pipe(takeUntilDestroyed()).subscribe(() => {
      this.form.controls.einheit.setValue('0');
    });
    this.form.valueChanges.pipe(takeUntilDestroyed()).subscribe(() => {
      this.rueckmeldung.set('');
      this.aenderung.update((n) => n + 1);
    });
  }

  private readonly auswahl = computed(() => {
    this.aenderung();
    const v = this.form.getRawValue();
    const kat = KATEGORIEN[Number(v.kategorie)] ?? KATEGORIEN[0];
    const vonIndex = Math.min(Math.max(Number(v.einheit) || 0, 0), kat.einheiten.length - 1);
    return { kat, vonIndex, wert: zahlAus(v.wert) };
  });

  protected readonly kategorie = computed(() => this.auswahl().kat);

  protected readonly einheitOptionen = computed<FeldOption[]>(() =>
    this.auswahl().kat.einheiten.map((e, i) => ({ wert: String(i), label: e.name })),
  );

  /** Nachschlagliste DN einblenden — Flag an der Kategorie, nicht am Anzeigenamen
   *  (ein umbenanntes Label würde die Liste sonst kommentarlos verschwinden lassen). */
  protected readonly zeigtGewinde = computed(() => this.auswahl().kat.zeigtGewinde === true);

  protected readonly zeilen = computed(() => {
    const { kat, vonIndex, wert } = this.auswahl();
    if (wert == null) return [];
    return umrechnen(kat, vonIndex, wert);
  });

  protected override ergebnisText(): string {
    const { kat, vonIndex, wert } = this.auswahl();
    const zeilen = this.zeilen();
    if (wert == null || zeilen.length === 0) return '';
    const vonName = kat.einheiten[vonIndex].name;
    // Format wie in der NotizApp: „12 kW = 12.000 W = 10.318,1428 kcal/h = …"
    return (
      `${zahlFein(wert)} ${vonName} = ` + zeilen.map((z) => `${z.wert} ${z.name}`).join(' = ')
    );
  }

  protected override positionsText(): string {
    return this.ergebnisText();
  }
}
