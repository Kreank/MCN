import { Component, computed, effect, inject, input, output, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { map } from 'rxjs';
import { BelegungService } from '../../core/belegung.service';
import { PartyService } from '../../core/party.service';
import {
  Belegung,
  MieterRolle,
  NUTZUNG_OPTIONEN,
  OccupancyType,
  ROLLE_OPTIONEN,
} from '../../core/belegung.model';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { ReferenzWahl, RefSuche } from '../../shared/formular/referenz-wahl';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';

/** Was der Dialog gerade tut. Drei Wege, ein Formularrahmen. */
export type BelegungDialogModus =
  | { art: 'neu'; unitId: string; unitNummer: string }
  | { art: 'bearbeiten'; belegung: Belegung; unitNummer: string }
  | { art: 'mieter'; belegung: Belegung; unitNummer: string };

/**
 * Belegung erfassen, ändern — oder einen weiteren Mieter setzen.
 *
 * **Der Mieter wird hier nicht angelegt, sondern ausgewählt.** Er ist ein
 * normaler Kontakt (`identity.party`); ihn hier „schnell" anzulegen erzeugte
 * Karteileichen **ohne Telefonnummer** — und genau die Nummer ist der Grund für
 * diesen ganzen Slice. Wer neu ist, wird unter „Kontakte" angelegt, mit Adresse
 * und Nummer. Die Auswahl läuft über `app-referenz-wahl` (Serversuche), nicht
 * über eine Liste der ersten 100 Kontakte — ein Betrieb mit 800 Kontakten findet
 * Robco darin nicht.
 *
 * **Leerstand** ist die Nutzungsart `VACANT` **ohne** Mieter — ausdrücklich
 * zulässig und im Formular kein Fehler.
 *
 * **Das Enddatum beendet die Belegung.** Setzt man es, ziehen die offenen
 * Mietverhältnisse auf dem Server **mit** (in derselben Transaktion) — ein
 * offener Mieter passt sonst nicht mehr in eine geschlossene Belegung. Der
 * Dialog sagt das, statt es passieren zu lassen.
 */
@Component({
  selector: 'app-belegung-dialog',
  imports: [ReactiveFormsModule, Dialog, Feld, ReferenzWahl],
  templateUrl: './belegung-dialog.html',
  styleUrl: './belegung-dialog.scss',
})
export class BelegungDialog {
  private readonly fb = inject(FormBuilder);
  private readonly svc = inject(BelegungService);
  private readonly partySvc = inject(PartyService);

  readonly propertyId = input.required<string>();
  readonly modus = input<BelegungDialogModus | null>(null);

  /** Meldung für den Aufrufer (er lädt neu und sagt sie an). */
  readonly gespeichert = output<string>();
  readonly abbrechen = output<void>();

  protected readonly nutzungOptionen: FeldOption[] = NUTZUNG_OPTIONEN;
  protected readonly rollenOptionen: FeldOption[] = ROLLE_OPTIONEN;

  protected readonly laedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);

  protected readonly offen = computed(() => this.modus() !== null);
  protected readonly art = computed(() => this.modus()?.art ?? 'neu');
  protected readonly nurMieter = computed(() => this.art() === 'mieter');
  protected readonly bearbeiten = computed(() => this.art() === 'bearbeiten');

  protected readonly titel = computed(() => {
    const m = this.modus();
    if (!m) return '';
    if (m.art === 'mieter') return `Weitere:n Mieter:in setzen — ${m.unitNummer}`;
    if (m.art === 'bearbeiten') return `Belegung ändern — ${m.unitNummer}`;
    return `Belegung erfassen — ${m.unitNummer}`;
  });

  protected readonly form = this.fb.group({
    occupancy_type: this.fb.control<OccupancyType | ''>('RENTED', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    valid_from: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    valid_until: this.fb.control('', { nonNullable: true }),
    contract_reference: this.fb.control('', { nonNullable: true }),
    // Der Mieter: optional (Leerstand!), aber wenn gesetzt, mit Rolle.
    party_id: this.fb.control('', { nonNullable: true }),
    role: this.fb.control<MieterRolle>('CONTRACTUAL_TENANT', { nonNullable: true }),
  });

  /** Serversuche für die Kontaktauswahl — dieselbe wie überall sonst. */
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
      const heute = new Date().toISOString().slice(0, 10);
      if (m.art === 'neu') {
        this.form.reset({
          occupancy_type: 'RENTED',
          valid_from: heute,
          valid_until: '',
          contract_reference: '',
          party_id: '',
          role: 'CONTRACTUAL_TENANT',
        });
      } else if (m.art === 'bearbeiten') {
        this.form.reset({
          occupancy_type: m.belegung.occupancy_type,
          valid_from: m.belegung.valid_from,
          valid_until: m.belegung.valid_until ?? '',
          contract_reference: m.belegung.contract_reference ?? '',
          party_id: '',
          role: 'CONTRACTUAL_TENANT',
        });
      } else {
        this.form.reset({
          occupancy_type: m.belegung.occupancy_type,
          valid_from: m.belegung.valid_from,
          valid_until: '',
          contract_reference: '',
          party_id: '',
          role: 'CONTRACTUAL_TENANT',
        });
      }
    });
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

    // Beim Mieter-Weg ist der Kontakt Pflicht — sonst gäbe es nichts zu tun.
    if (m.art === 'mieter' && !this.form.controls.party_id.value) {
      this.form.controls.party_id.setErrors({ server: 'Bitte einen Kontakt wählen.' });
      felderAlsBeruehrtMarkieren(this.form);
      return;
    }
    if (this.form.invalid) {
      felderAlsBeruehrtMarkieren(this.form);
      return;
    }

    const v = this.form.getRawValue();
    this.laedt.set(true);

    if (m.art === 'mieter') {
      this.svc
        .addMieter(m.belegung.id, {
          party_id: v.party_id,
          role: v.role,
        })
        .subscribe({
          next: () => this.fertig('Mieter:in wurde gesetzt.'),
          error: (err) => this.gescheitert(err),
        });
      return;
    }

    if (m.art === 'bearbeiten') {
      this.svc
        .update(m.belegung.id, {
          occupancy_type: v.occupancy_type as OccupancyType,
          valid_from: v.valid_from,
          valid_until: v.valid_until || null,
          contract_reference: v.contract_reference.trim() || null,
        })
        .subscribe({
          next: () =>
            this.fertig(
              v.valid_until
                ? 'Belegung beendet. Offene Mietverhältnisse enden zum selben Tag.'
                : 'Belegung gespeichert.',
            ),
          error: (err) => this.gescheitert(err),
        });
      return;
    }

    this.svc
      .create(this.propertyId(), {
        unit_id: m.unitId,
        occupancy_type: v.occupancy_type as OccupancyType,
        valid_from: v.valid_from,
        valid_until: v.valid_until || null,
        contract_reference: v.contract_reference.trim() || null,
        mieter: v.party_id ? [{ party_id: v.party_id, role: v.role }] : [],
      })
      .subscribe({
        next: () =>
          this.fertig(
            v.party_id
              ? 'Belegung erfasst.'
              : 'Belegung erfasst — ohne Mieter:in (Leerstand).',
          ),
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
