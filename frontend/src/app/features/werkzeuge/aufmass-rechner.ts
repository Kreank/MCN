import { Component, ElementRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormBuilder, FormControl, FormGroup, ReactiveFormsModule } from '@angular/forms';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { dezimalValidator } from '../../shared/formular/dezimal';
import { RechnerBasis, RechnerPosition } from './rechner-basis';
import { zahlAus } from './eingabe';
import {
  MASS_LABEL,
  MESS_ARTEN,
  MESS_ART_STANDARD,
  MassFeld,
  MessArt,
  MessArtDef,
  TeilmassEingabe,
  aufmass,
  gebindeAus,
  mengeApi,
  teilmass,
  zahlKurz,
  zahlMenge,
} from './rechner';

/** Ein Teilmaß-Formular (eine Zeile des Aufmaßes). Zahlen bleiben Strings. */
type TeilForm = FormGroup<{
  bezeichnung: FormControl<string>;
  anzahl: FormControl<string>;
  laenge: FormControl<string>;
  breite: FormControl<string>;
  hoehe: FormControl<string>;
  abzug: FormControl<boolean>;
}>;

/**
 * Aufmaß / Mengenermittlung mit Verschnitt.
 *
 * Das einzige Werkzeug hier, das nicht im Display endet, sondern **im Vorgang**:
 * die ermittelte Bestellmenge geht als bepreisbare Belegposition in den Editor,
 * samt Rechenaufstellung im Positionstext — damit später nachvollziehbar bleibt,
 * wie die Menge zustande kam.
 *
 * **Zur Invariante „der Server rechnet verbindlich":** hier entsteht eine
 * MENGE, kein Betrag. Sie wird als Punkt-String übergeben (nie als `number`),
 * und der Server rechnet daraus Positionsnetto, Steuer und Summen. Der
 * Einzelpreis wird bewusst NICHT gesetzt — den trägt der Anwender im
 * Positionsdialog nach (oder er kommt aus dem Artikelstamm).
 */
@Component({
  selector: 'app-aufmass-rechner',
  imports: [ReactiveFormsModule, Feld],
  templateUrl: './aufmass-rechner.html',
  styleUrl: './rechner.scss',
})
export class AufmassRechner extends RechnerBasis {
  private readonly fb = inject(FormBuilder);
  private readonly host = inject(ElementRef<HTMLElement>);

  protected readonly artOptionen: FeldOption[] = MESS_ARTEN.map((a) => ({
    wert: a.wert,
    label: `${a.label} (${a.einheit})`,
  }));
  protected readonly massLabel = MASS_LABEL;

  protected readonly form = this.fb.group({
    bezeichnung: this.fb.control('', { nonNullable: true }),
    art: this.fb.control<MessArt>(MESS_ART_STANDARD, { nonNullable: true }),
    verschnitt: this.fb.control('5', { nonNullable: true, validators: [dezimalValidator] }),
    gebinde: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
    teile: this.fb.array<TeilForm>([]),
  });

  private readonly aenderung = signal(0);

  constructor() {
    super();
    this.teilHinzufuegen(false);
    this.form.valueChanges.pipe(takeUntilDestroyed()).subscribe(() => {
      this.rueckmeldung.set('');
      this.aenderung.update((n) => n + 1);
    });
  }

  protected get teile() {
    return this.form.controls.teile;
  }

  /** Die gewählte Messart (Fläche/Länge/Stück/Volumen) mit ihren Maßfeldern. */
  protected readonly art = computed<MessArtDef>(() => {
    this.aenderung();
    const wert = this.form.controls.art.value;
    return MESS_ARTEN.find((a) => a.wert === wert) ?? MESS_ARTEN[0];
  });

  /** Ob ein Maßfeld für die aktuelle Messart gebraucht wird (sonst ausgeblendet). */
  protected zeigtMass(feld: MassFeld): boolean {
    return this.art().masse.includes(feld);
  }

  private teilGruppe(): TeilForm {
    return this.fb.group({
      bezeichnung: this.fb.control('', { nonNullable: true }),
      anzahl: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
      laenge: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
      breite: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
      hoehe: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
      abzug: this.fb.control(false, { nonNullable: true }),
    });
  }

  /** Teilmaß anhängen; der Fokus wandert in das neue Bezeichnungsfeld. */
  protected teilHinzufuegen(fokus = true): void {
    this.teile.push(this.teilGruppe());
    if (!fokus) return;
    const index = this.teile.length - 1;
    this.rueckmeldung.set(`Teilmaß ${index + 1} hinzugefügt.`);
    queueMicrotask(() => {
      const el = this.host.nativeElement as HTMLElement;
      const zeile = el.querySelectorAll<HTMLElement>('[data-teil]')[index];
      zeile?.querySelector<HTMLInputElement>('input')?.focus();
    });
  }

  /** Letztes Teilmaß bleibt stehen (leeres Formular statt gar keinem). */
  protected teilEntfernen(index: number): void {
    if (this.teile.length <= 1) {
      this.teile
        .at(0)
        .reset({ bezeichnung: '', anzahl: '', laenge: '', breite: '', hoehe: '', abzug: false });
      this.rueckmeldung.set('Teilmaß geleert.');
      return;
    }
    this.teile.removeAt(index);
    this.rueckmeldung.set(`Teilmaß ${index + 1} entfernt.`);
  }

  /** Zeilenwerte als Zahlen — mehrdeutige Eingaben („1.500") ergeben `null`. */
  private readonly eingaben = computed<TeilmassEingabe[]>(() => {
    this.aenderung();
    return this.teile.controls.map((g) => {
      const v = g.getRawValue();
      return {
        bezeichnung: v.bezeichnung,
        anzahl: zahlAus(v.anzahl),
        laenge: zahlAus(v.laenge),
        breite: zahlAus(v.breite),
        hoehe: zahlAus(v.hoehe),
        abzug: v.abzug,
      };
    });
  });

  private readonly kopf = computed(() => {
    this.aenderung();
    const v = this.form.getRawValue();
    // Leer = „keine Gebindegröße" (gültig). Eine ausgefüllte, aber unlesbare,
    // mehrdeutige oder nicht positive Eingabe ist ein Fehler — siehe `gebindeAus`.
    const g = gebindeAus(v.gebinde);
    return {
      bezeichnung: v.bezeichnung.trim(),
      verschnitt: zahlAus(v.verschnitt) ?? (v.verschnitt.trim() === '' ? 0 : null),
      gebinde: g.gebinde,
      gebindeUngueltig: g.ungueltig,
    };
  });

  /** Sichtbarer Grund, warum die Gebindegröße nicht gilt (nie nur stumm ignorieren). */
  protected readonly gebindeFehler = computed(() =>
    this.kopf().gebindeUngueltig
      ? 'Die Gebinde-/Verpackungsgröße ist keine gültige Zahl größer als 0. Bitte korrigieren ' +
        'oder das Feld leeren — sonst wird nicht auf volle Gebinde aufgerundet.'
      : '',
  );

  protected readonly ergebnis = computed(() => {
    const { verschnitt, gebinde, gebindeUngueltig } = this.kopf();
    if (verschnitt == null || verschnitt < 0 || gebindeUngueltig) return null;
    return aufmass(this.art(), this.eingaben(), verschnitt, gebinde);
  });

  /**
   * Alle Teilmaße — bewusst UNABHÄNGIG vom Gesamtergebnis ausgewertet: ein
   * unvollständiges Teilmaß muss auch dann sichtbar sein, wenn die Nettomenge
   * (noch) 0 ist und `aufmass()` deshalb null liefert.
   */
  private readonly alleTeile = computed(() => this.eingaben().map((t) => teilmass(this.art(), t)));

  /** Zeilen, die etwas enthalten (leere Zeilen tauchen in der Aufstellung nicht auf). */
  protected readonly zeilen = computed(() => this.alleTeile().filter((t) => t.status !== 'LEER'));

  /**
   * Ein angefangenes, aber unvollständiges Teilmaß blockiert die Übernahme:
   * eine still übergangene Zeile wäre eine falsche Menge im Angebot.
   */
  protected readonly blockiert = computed(() =>
    this.alleTeile().some((t) => t.status === 'UNVOLLSTAENDIG'),
  );

  protected readonly uebernehmbar = computed(() => !!this.ergebnis() && !this.blockiert());

  protected readonly mengeText = computed(() => {
    const e = this.ergebnis();
    return e ? `${zahlMenge(e.bestellmenge)} ${e.einheit}` : '—';
  });

  /** „17,82 m² + 10 % Verschnitt (1,782 m²) = 19,602 m²". */
  protected readonly verschnittText = computed(() => {
    const e = this.ergebnis();
    if (!e) return '';
    return (
      `${zahlMenge(e.netto)} ${e.einheit} netto + ${zahlKurz(e.verschnittProzent)} % Verschnitt ` +
      `(${zahlMenge(e.verschnittMenge)} ${e.einheit}) = ${zahlMenge(e.brutto)} ${e.einheit}`
    );
  });

  /** „14 Gebinde à 1,44 m² = 20,16 m²" — nur wenn eine Gebindegröße angegeben ist. */
  protected readonly gebindeText = computed(() => {
    const e = this.ergebnis();
    if (!e || e.gebinde == null || e.gebindeAnzahl == null) return '';
    return (
      `${e.gebindeAnzahl} Gebinde à ${zahlMenge(e.gebinde)} ${e.einheit} = ` +
      `${zahlMenge(e.bestellmenge)} ${e.einheit}`
    );
  });

  protected mengeAnzeige(n: number): string {
    return zahlMenge(n);
  }

  /** Erste Zeile des Positionstexts (Bezeichnung, sonst neutraler Fallback). */
  private positionsTitel(): string {
    return this.kopf().bezeichnung || 'Aufmaß';
  }

  /** Die Rechenaufstellung — identisch in Zwischenablage und Positionstext. */
  private aufstellung(): string[] {
    const e = this.ergebnis();
    if (!e) return [];
    const teile = e.teile
      .filter((t) => t.status === 'OK')
      .map((t) => `- ${t.bezeichnung || 'Teilmaß'}: ${t.rechenweg}${t.abzug ? ' (Abzug)' : ''}`);
    const gebinde = this.gebindeText();
    return [
      ...teile,
      `- Nettomenge: ${zahlMenge(e.netto)} ${e.einheit}`,
      `- Verschnitt ${zahlKurz(e.verschnittProzent)} %: + ${zahlMenge(e.verschnittMenge)} ` +
        `${e.einheit} → ${zahlMenge(e.brutto)} ${e.einheit}`,
      ...(gebinde ? [`- Gebinde: ${gebinde}`] : []),
      `- Bestellmenge: ${zahlMenge(e.bestellmenge)} ${e.einheit}`,
    ];
  }

  protected override ergebnisText(): string {
    if (!this.uebernehmbar()) return '';
    return [`Aufmaß — ${this.positionsTitel()}`, this.kontextZeile(), ...this.aufstellung()]
      .filter((z): z is string => z !== null)
      .join('\n');
  }

  /** Fallback-Textzeile (wird im Editor nicht genutzt — dort geht die Position). */
  protected override positionsText(): string {
    const e = this.ergebnis();
    if (!e || !this.uebernehmbar()) return '';
    return (
      `Aufmaß ${this.positionsTitel()}: ${zahlMenge(e.netto)} ${e.einheit} netto + ` +
      `${zahlKurz(e.verschnittProzent)} % Verschnitt = ${zahlMenge(e.bestellmenge)} ${e.einheit}.`
    );
  }

  /**
   * Die Belegposition. `menge` ist ein **String** (`mengeApi`) — kein `number`
   * verlässt diesen Rechner Richtung Datenmodell. Ein Preis wird nicht gesetzt.
   */
  protected override position(): RechnerPosition | null {
    const e = this.ergebnis();
    if (!e || !this.uebernehmbar() || !(e.bestellmenge > 0)) return null;
    return {
      beschreibung: [this.positionsTitel(), ...this.aufstellung()].join('\n'),
      menge: mengeApi(e.bestellmenge),
      einheit: e.einheit,
    };
  }
}
