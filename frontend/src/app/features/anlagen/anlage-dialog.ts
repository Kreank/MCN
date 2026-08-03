import { Component, computed, effect, inject, input, output, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { AnlageService } from '../../core/anlage.service';
import {
  ART_OPTIONEN,
  Anlage,
  AnlageDetail,
  AnlageIn,
  AnlagePatch,
  AssetType,
  ENERGIE_OPTIONEN,
  EnergySource,
  SUPPLY_OPTIONEN,
  SupplyType,
} from '../../core/anlage.model';
import { Building } from '../../core/property.model';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';

/**
 * Anlage erfassen oder bearbeiten — **ein** Formular für beide Wege (Liste der
 * Liegenschaftsmappe und Anlagen-Detail). Zwei Kopien liefen sonst auseinander.
 *
 * Zwei Dinge, die nicht „vereinfacht" werden dürfen:
 *
 * * **Die Einheit setzt ihr Gebäude voraus.** Die Datenbank erzwingt das über
 *   zusammengesetzte Fremdschlüssel (Migration 0004). Deshalb hängt die
 *   Einheitsauswahl am gewählten Gebäude, und ein Gebäudewechsel leert die
 *   Einheit — sonst schickte das Formular eine Einheit, die zum neuen Gebäude
 *   nicht gehört, und der Server müsste sie mit 422 abweisen.
 * * **Leistung leer heißt unbekannt, nie 0 kW.** Ein leeres Feld wird zu `null`
 *   und nicht zu 0 (der Server weist 0 ausdrücklich ab).
 */
@Component({
  selector: 'app-anlage-dialog',
  imports: [ReactiveFormsModule, Dialog, Feld],
  templateUrl: './anlage-dialog.html',
  styleUrl: './anlage-dialog.scss',
})
export class AnlageDialog {
  private readonly fb = inject(FormBuilder);
  private readonly svc = inject(AnlageService);

  readonly offen = input(false);
  readonly propertyId = input.required<string>();
  /** Gebäude der Liegenschaft — Grundlage der Standortauswahl. */
  readonly gebaeude = input<readonly Building[]>([]);
  /** Gesetzt = Bearbeiten, `null` = Neu erfassen. */
  readonly anlage = input<Anlage | null>(null);

  readonly gespeichert = output<AnlageDetail>();
  readonly abbrechen = output<void>();

  protected readonly artOptionen: FeldOption[] = ART_OPTIONEN;
  protected readonly versorgungOptionen: FeldOption[] = SUPPLY_OPTIONEN;
  protected readonly energieOptionen: FeldOption[] = ENERGIE_OPTIONEN;

  protected readonly laedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);

  /** Wofür das Formular gerade befüllt ist — schützt laufende Eingaben vor einem
   *  erneuten Befüllen mit denselben Daten (Muster aus `shared/berichte`). */
  private formularFuer: string | null = null;

  protected readonly bearbeiten = computed(() => this.anlage() !== null);
  protected readonly titel = computed(() =>
    this.bearbeiten() ? 'Anlage bearbeiten' : 'Anlage erfassen',
  );

  protected readonly form = this.fb.group({
    name: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    asset_type: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    supply_type: this.fb.control('UNBEKANNT', { nonNullable: true }),
    building_id: this.fb.control('', { nonNullable: true }),
    unit_id: this.fb.control('', { nonNullable: true }),
    manufacturer: this.fb.control('', { nonNullable: true }),
    model: this.fb.control('', { nonNullable: true }),
    year_built: this.fb.control('', { nonNullable: true }),
    serial_number: this.fb.control('', { nonNullable: true }),
    location_note: this.fb.control('', { nonNullable: true }),
    energy_source: this.fb.control('', { nonNullable: true }),
    power_kw: this.fb.control('', { nonNullable: true }),
    note: this.fb.control('', { nonNullable: true }),
  });

  protected readonly gebaeudeOptionen = computed<FeldOption[]>(() =>
    this.gebaeude().map((b) => ({
      wert: b.id,
      label: b.name || `Gebäude ${b.building_number}`,
    })),
  );

  /** Gewähltes Gebäude (Signal, damit die Einheitsliste darauf reagiert). */
  private readonly gewaehltesGebaeude = signal('');

  protected readonly einheitOptionen = computed<FeldOption[]>(() => {
    const b = this.gebaeude().find((g) => g.id === this.gewaehltesGebaeude());
    if (!b) return [];
    return b.units.map((u) => ({ wert: u.id, label: u.unit_number }));
  });

  constructor() {
    // Beim Öffnen befüllen (bzw. leeren). Ohne dieses Zurücksetzen stünden beim
    // nächsten „Neu" noch die Werte der zuletzt bearbeiteten Anlage im Formular.
    // Befüllt wird EINMAL je geöffneter Anlage, nicht bei jedem Impuls.
    //
    // `anlage()` ist ein berechtigter Auslöser (ein anderer Datensatz gehört ins
    // Formular), aber ein Nachladen liefert dasselbe Objekt mit neuer Identität.
    // Ohne diesen Merker würfe das die halb getippte Anlage weg — derselbe
    // Fehler, der die Anruf-Maske unbenutzbar gemacht hat. Heute löst kein
    // Aufrufer das aus (beide schließen erst und setzen dann), aber diese
    // Korrektheit hinge an der Reihenfolge zweier Zeilen in fremden Dateien.
    effect(() => {
      if (!this.offen()) {
        this.formularFuer = null;
        return;
      }
      const a = this.anlage();
      const schluessel = a?.id ?? '__neu__';
      if (this.formularFuer === schluessel) return;
      this.formularFuer = schluessel;
      this.formularMeldung.set(null);
      this.form.reset({
        name: a?.name ?? '',
        asset_type: a?.asset_type ?? '',
        supply_type: a?.supply_type ?? 'UNBEKANNT',
        building_id: a?.building_id ?? '',
        unit_id: a?.unit_id ?? '',
        manufacturer: a?.manufacturer ?? '',
        model: a?.model ?? '',
        year_built: a?.year_built != null ? String(a.year_built) : '',
        serial_number: a?.serial_number ?? '',
        location_note: a?.location_note ?? '',
        energy_source: a?.energy_source ?? '',
        power_kw: a?.power_kw != null ? String(a.power_kw) : '',
        note: a?.note ?? '',
      });
      this.gewaehltesGebaeude.set(a?.building_id ?? '');
    });

    // Gebäude gewechselt: die Einheit passt nicht mehr zum neuen Gebäude. Sie wird
    // geleert, statt sie mitzuschleifen — eine Einheit im fremden Gebäude ist laut
    // DB unmöglich (zusammengesetzter FK), der Server müsste sie mit 422 abweisen.
    this.form.controls.building_id.valueChanges
      .pipe(takeUntilDestroyed())
      .subscribe((b) => {
        if (b === this.gewaehltesGebaeude()) return;
        this.gewaehltesGebaeude.set(b);
        this.form.controls.unit_id.setValue('', { emitEvent: false });
      });
  }

  schliessen(): void {
    if (this.laedt()) return;
    this.abbrechen.emit();
  }

  /** Leerer String → `null`. „Nicht erfasst" ist nicht dasselbe wie „leer". */
  private wert(s: string): string | null {
    const t = s.trim();
    return t === '' ? null : t;
  }

  private zahl(s: string): number | null {
    const t = s.trim();
    if (t === '') return null;
    const n = Number(t.replace(',', '.'));
    return Number.isFinite(n) ? n : null;
  }

  absenden(): void {
    if (this.laedt()) return;
    serverFehlerZuruecksetzen(this.form);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.form);
    if (this.form.invalid) return;

    const v = this.form.getRawValue();
    // Die Leistung geht als String über die Leitung (Dezimal, kein Float):
    // `null` = unbekannt. 0 lehnt der Server ab — 0 kW hieße „heizt nicht".
    const leistung = this.wert(v.power_kw.replace(',', '.'));
    const payload: AnlageIn = {
      name: v.name.trim(),
      asset_type: v.asset_type as AssetType,
      supply_type: (v.supply_type || 'UNBEKANNT') as SupplyType,
      building_id: this.wert(v.building_id),
      unit_id: this.wert(v.unit_id),
      manufacturer: this.wert(v.manufacturer),
      model: this.wert(v.model),
      year_built: this.zahl(v.year_built),
      serial_number: this.wert(v.serial_number),
      location_note: this.wert(v.location_note),
      energy_source: (this.wert(v.energy_source) as EnergySource | null) ?? null,
      power_kw: leistung,
      note: this.wert(v.note),
    };

    this.laedt.set(true);
    const a = this.anlage();
    const anfrage = a
      ? this.svc.update(a.id, payload as AnlagePatch)
      : this.svc.create(this.propertyId(), payload);

    anfrage.subscribe({
      next: (gespeichert) => {
        this.laedt.set(false);
        this.gespeichert.emit(gespeichert);
      },
      error: (err) => {
        this.laedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.form).formular);
      },
    });
  }
}
