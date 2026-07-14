import { Component, computed, effect, inject, input, output, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { map } from 'rxjs';
import { VerwaltungService } from '../../core/verwaltung.service';
import { PartyService } from '../../core/party.service';
import { Building } from '../../core/property.model';
import {
  MANDAT_OPTIONEN,
  Mandat,
  MandateType,
  ResponsibilityType,
  SCOPE_OPTIONEN,
  ScopeType,
  ZUSTAENDIG_OPTIONEN,
} from '../../core/verwaltung.model';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { ReferenzWahl, RefSuche } from '../../shared/formular/referenz-wahl';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';

export type VerwaltungDialogModus =
  | { art: 'neu' }
  | { art: 'bearbeiten'; mandat: Mandat }
  | { art: 'zustaendigkeit'; mandat: Mandat };

/**
 * Mandat anlegen, korrigieren — oder einen weiteren Kontakt am Mandat setzen.
 *
 * **Drei Parteien, drei Rollen, und sie sind nicht dasselbe:**
 * Verwaltung (Stegos) ≠ Auftraggeber (die WEG) ≠ Standardkontakt (die Person,
 * die abnimmt). Das Formular fragt alle drei einzeln ab, statt eine „Verwaltung"
 * zu erfinden, die alles ist — sonst geht die Rechnung an den Falschen.
 *
 * **Der Umfang wird nur beim Anlegen gewählt.** Mandatseinheiten sind in der
 * Datenbank unveränderlich (A-11, `trg_mandate_unit_immutable`); ein anderer
 * Umfang ist ein **Nachfolgemandat**. Deshalb fehlt er im Bearbeiten-Weg — nicht
 * aus Bequemlichkeit, sondern weil es ihn dort nicht gibt.
 *
 * **`SELECTED_UNITS` verlangt mindestens eine Einheit** und `ENTIRE_PROPERTY`
 * verträgt keine — das erzwingen deferred Constraint-Trigger. Das Formular hält
 * sich daran, damit der Nutzer nicht in einen 422 läuft.
 */
@Component({
  selector: 'app-verwaltung-dialog',
  imports: [ReactiveFormsModule, Dialog, Feld, ReferenzWahl],
  templateUrl: './verwaltung-dialog.html',
  styleUrl: './verwaltung-dialog.scss',
})
export class VerwaltungDialog {
  private readonly fb = inject(FormBuilder);
  private readonly svc = inject(VerwaltungService);
  private readonly partySvc = inject(PartyService);

  readonly propertyId = input.required<string>();
  readonly gebaeude = input<readonly Building[]>([]);
  readonly modus = input<VerwaltungDialogModus | null>(null);

  readonly gespeichert = output<string>();
  readonly abbrechen = output<void>();

  protected readonly mandatOptionen: FeldOption[] = MANDAT_OPTIONEN;
  protected readonly scopeOptionen: FeldOption[] = SCOPE_OPTIONEN;
  protected readonly zustaendigOptionen: FeldOption[] = ZUSTAENDIG_OPTIONEN;

  protected readonly laedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);
  /** Bei SELECTED_UNITS gewählte Einheiten (IDs). */
  protected readonly gewaehlteEinheiten = signal<Set<string>>(new Set());

  protected readonly offen = computed(() => this.modus() !== null);
  protected readonly art = computed(() => this.modus()?.art ?? 'neu');
  protected readonly neu = computed(() => this.art() === 'neu');
  protected readonly zustaendigkeit = computed(() => this.art() === 'zustaendigkeit');

  protected readonly titel = computed(() => {
    const a = this.art();
    if (a === 'zustaendigkeit') return 'Weiteren Kontakt am Mandat';
    if (a === 'bearbeiten') return 'Mandat korrigieren';
    return 'Verwaltungsmandat anlegen';
  });

  /**
   * Verwalter und Auftraggeber im Bearbeiten-Weg: **Text, kein Feld.**
   * Ein Eingabefeld, dessen Wert der Server verwirft, lügt den Nutzer an —
   * er ändert den Verwalter, drückt „Speichern", bekommt eine Erfolgsmeldung,
   * und nichts ist passiert.
   */
  private readonly mandat = computed(() => {
    const m = this.modus();
    return m && m.art !== 'neu' ? m.mandat : null;
  });

  protected readonly festVerwaltung = computed(
    () => this.mandat()?.verwaltung.display_name ?? '—',
  );
  protected readonly festAuftraggeber = computed(
    () => this.mandat()?.auftraggeber.display_name ?? '—',
  );

  /** Alle Einheiten der Liegenschaft, flach — Grundlage der Umfangswahl. */
  protected readonly einheiten = computed(() =>
    this.gebaeude().flatMap((g) =>
      g.units.map((u) => ({
        id: u.id,
        label: g.units.length ? `${u.unit_number}` : u.unit_number,
      })),
    ),
  );

  protected readonly teilmandat = computed(
    () => this.form.controls.scope_type.value === 'SELECTED_UNITS',
  );

  protected readonly form = this.fb.group({
    management_party_id: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    principal_party_id: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    default_contact_party_id: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    mandate_type: this.fb.control<MandateType>('WEG_MANAGEMENT', { nonNullable: true }),
    scope_type: this.fb.control<ScopeType>('ENTIRE_PROPERTY', { nonNullable: true }),
    valid_from: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    contract_reference: this.fb.control('', { nonNullable: true }),
    // Zuständigkeits-Weg:
    responsibility_type: this.fb.control<ResponsibilityType>('TECHNICAL_CONTACT', {
      nonNullable: true,
    }),
    responsible_party_id: this.fb.control('', { nonNullable: true }),
    priority: this.fb.control('100', { nonNullable: true }),
  });

  protected readonly parteiSuche: RefSuche = (q) =>
    this.partySvc
      .list({ page: 1, page_size: 20, q })
      .pipe(map((p) => p.items.map((o) => ({ id: o.id, label: o.display_name }))));

  constructor() {
    effect(() => {
      const m = this.modus();
      if (!m) return;
      this.formularMeldung.set(null);
      this.laedt.set(false);
      this.gewaehlteEinheiten.set(new Set());
      const heute = new Date().toISOString().slice(0, 10);
      if (m.art === 'bearbeiten') {
        this.form.reset({
          management_party_id: m.mandat.verwaltung.party_id,
          principal_party_id: m.mandat.auftraggeber.party_id,
          default_contact_party_id: m.mandat.standardkontakt.party_id,
          mandate_type: m.mandat.mandate_type,
          scope_type: m.mandat.scope_type,
          valid_from: m.mandat.valid_from,
          contract_reference: m.mandat.contract_reference ?? '',
          responsibility_type: 'TECHNICAL_CONTACT',
          responsible_party_id: '',
          priority: '100',
        });
      } else {
        this.form.reset({
          management_party_id: '',
          principal_party_id: '',
          default_contact_party_id: '',
          mandate_type: 'WEG_MANAGEMENT',
          scope_type: 'ENTIRE_PROPERTY',
          valid_from: heute,
          contract_reference: '',
          responsibility_type: 'TECHNICAL_CONTACT',
          responsible_party_id: '',
          priority: '100',
        });
      }
    });
  }

  protected einheitUmschalten(id: string): void {
    this.gewaehlteEinheiten.update((alt) => {
      const neu = new Set(alt);
      if (neu.has(id)) neu.delete(id);
      else neu.add(id);
      return neu;
    });
  }

  protected istGewaehlt(id: string): boolean {
    return this.gewaehlteEinheiten().has(id);
  }

  protected schliessen(): void {
    if (this.laedt()) return;
    this.abbrechen.emit();
  }

  protected absenden(): void {
    const m = this.modus();
    if (!m || this.laedt()) return;

    serverFehlerZuruecksetzen(this.form);
    this.formularMeldung.set(null);

    if (m.art === 'zustaendigkeit') {
      if (!this.form.controls.responsible_party_id.value) {
        this.form.controls.responsible_party_id.setErrors({
          server: 'Bitte einen Kontakt wählen.',
        });
        felderAlsBeruehrtMarkieren(this.form);
        return;
      }
      const v = this.form.getRawValue();
      this.laedt.set(true);
      this.svc
        .addZustaendigkeit(m.mandat.id, {
          responsibility_type: v.responsibility_type,
          responsible_party_id: v.responsible_party_id,
          valid_from: v.valid_from || new Date().toISOString().slice(0, 10),
          priority: Number(v.priority) || 100,
        })
        .subscribe({
          next: () => this.fertig('Kontakt am Mandat gesetzt.'),
          error: (err) => this.gescheitert(err),
        });
      return;
    }

    if (m.art === 'bearbeiten') {
      if (!this.form.controls.default_contact_party_id.value) {
        this.form.controls.default_contact_party_id.setErrors({
          server: 'Ein Mandat braucht einen Standardkontakt.',
        });
        felderAlsBeruehrtMarkieren(this.form);
        return;
      }
      const v = this.form.getRawValue();
      this.laedt.set(true);
      this.svc
        .update(m.mandat.id, {
          default_contact_party_id: v.default_contact_party_id,
          contract_reference: v.contract_reference.trim() || null,
        })
        .subscribe({
          next: () => this.fertig('Mandat gespeichert.'),
          error: (err) => this.gescheitert(err),
        });
      return;
    }

    // Anlegen
    if (this.form.invalid) {
      felderAlsBeruehrtMarkieren(this.form);
      return;
    }
    const v = this.form.getRawValue();
    if (v.management_party_id === v.principal_party_id) {
      this.formularMeldung.set(
        'Verwaltung und Auftraggeber dürfen nicht dieselbe Partei sein — die WEG beauftragt, der Verwalter führt aus.',
      );
      return;
    }
    const einheiten = [...this.gewaehlteEinheiten()];
    if (v.scope_type === 'SELECTED_UNITS' && einheiten.length === 0) {
      this.formularMeldung.set(
        'Ein Mandat über ausgewählte Einheiten braucht mindestens eine Einheit.',
      );
      return;
    }

    this.laedt.set(true);
    this.svc
      .create(this.propertyId(), {
        management_party_id: v.management_party_id,
        principal_party_id: v.principal_party_id,
        default_contact_party_id: v.default_contact_party_id,
        mandate_type: v.mandate_type,
        scope_type: v.scope_type,
        valid_from: v.valid_from,
        contract_reference: v.contract_reference.trim() || null,
        // ENTIRE_PROPERTY verträgt KEINE Einheiten (Constraint-Trigger).
        unit_ids: v.scope_type === 'SELECTED_UNITS' ? einheiten : [],
      })
      .subscribe({
        next: () => this.fertig('Verwaltungsmandat angelegt.'),
        error: (err) => this.gescheitert(err),
      });
  }

  private fertig(meldung: string): void {
    this.laedt.set(false);
    this.gespeichert.emit(meldung);
  }

  private gescheitert(err: unknown): void {
    this.laedt.set(false);
    this.formularMeldung.set(apiFehlerZuweisen(err, this.form).formular ?? null);
  }
}
