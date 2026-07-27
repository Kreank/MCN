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
 *
 * **Der Eigentümer wird hier gleich mit erfasst.** Saschas Befund beim Testen:
 * *„Bei Belegung kann ich ja auch Eigentümer als bewohnt angeben — das sollte
 * beim Reiter Eigentum übernommen werden, wollen ja keine doppelte Arbeit."*
 * Deshalb der dritte Abschnitt: Wählt man „Eigentümer (bewohnt)" als Rolle,
 * hakt sich die Übernahme von selbst an; vermietet der Eigentümer, steht er als
 * eigener Kontakt daneben — er wohnt ja gerade **nicht** dort und gehört
 * deshalb nicht in die Mieterliste.
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
    // Eigentum — landet NICHT in der Mieterliste, sondern im Reiter „Eigentum".
    eigentuemer_ist_person: this.fb.control(false, { nonNullable: true }),
    eigentuemer_party_id: this.fb.control('', { nonNullable: true }),
  });

  /**
   * Formularzustände als Signale.
   *
   * `computed()` über `form.controls.x.value` würde **nicht** neu rechnen — ein
   * `FormControl` ist kein Signal, und die Falle ist in diesem Repo schon
   * einmal zugeschnappt. Deshalb explizit aus `valueChanges` gespeist.
   */
  protected readonly personGewaehlt = signal(false);
  protected readonly eigentuemerIstPerson = signal(false);

  /** Wird die Übernahme gerade über die gewählte Person gefahren? */
  protected readonly uebernahmeUeberPerson = computed(
    () => this.personGewaehlt() && this.eigentuemerIstPerson(),
  );

  /** Serversuche für die Kontaktauswahl — dieselbe wie überall sonst. */
  protected readonly parteiSuche: RefSuche = (q) =>
    this.partySvc
      .list({ page: 1, page_size: 20, q })
      .pipe(map((p) => p.items.map((o) => ({ id: o.id, label: o.display_name }))));

  constructor() {
    this.form.controls.party_id.valueChanges.subscribe((v) =>
      this.personGewaehlt.set(!!v),
    );
    this.form.controls.eigentuemer_ist_person.valueChanges.subscribe((v) =>
      this.eigentuemerIstPerson.set(v),
    );
    // „Eigentümer (bewohnt)" IST die Aussage „ihm gehört das". Der Haken setzt
    // sich deshalb selbst — niemand soll dieselbe Person zweimal eintragen.
    this.form.controls.role.valueChanges.subscribe((rolle) => {
      if (rolle === 'OWNER_OCCUPANT') {
        this.form.controls.eigentuemer_ist_person.setValue(true);
      }
    });
    // Umgekehrt: „Eigennutzung" legt die Rolle nahe, die dazu gehört.
    this.form.controls.occupancy_type.valueChanges.subscribe((typ) => {
      if (typ === 'OWNER_OCCUPIED' && !this.form.controls.party_id.value) {
        this.form.controls.role.setValue('OWNER_OCCUPANT');
      }
    });

    effect(() => {
      const m = this.modus();
      if (!m) return;
      this.formularMeldung.set(null);
      this.laedt.set(false);
      const heute = new Date().toISOString().slice(0, 10);
      const eigentumLeer = {
        eigentuemer_ist_person: false,
        eigentuemer_party_id: '',
      };
      if (m.art === 'neu') {
        this.form.reset({
          occupancy_type: 'RENTED',
          valid_from: heute,
          valid_until: '',
          contract_reference: '',
          party_id: '',
          role: 'CONTRACTUAL_TENANT',
          ...eigentumLeer,
        });
      } else if (m.art === 'bearbeiten') {
        this.form.reset({
          occupancy_type: m.belegung.occupancy_type,
          valid_from: m.belegung.valid_from,
          valid_until: m.belegung.valid_until ?? '',
          contract_reference: m.belegung.contract_reference ?? '',
          party_id: '',
          role: 'CONTRACTUAL_TENANT',
          ...eigentumLeer,
        });
      } else {
        this.form.reset({
          occupancy_type: m.belegung.occupancy_type,
          valid_from: m.belegung.valid_from,
          valid_until: '',
          contract_reference: '',
          party_id: '',
          role: 'CONTRACTUAL_TENANT',
          ...eigentumLeer,
        });
      }
      // Signale aus den Controls nachziehen, nicht auf `false` zwingen: Das
      // `reset()` löst oben die Kettenreaktion „Eigennutzung → Rolle
      // Eigentümer (bewohnt) → Haken" aus. Ein hart gesetztes `false` würde
      // Anzeige und Formularwert auseinanderlaufen lassen.
      this.personGewaehlt.set(!!this.form.controls.party_id.value);
      this.eigentuemerIstPerson.set(this.form.controls.eigentuemer_ist_person.value);
    });
  }

  /**
   * Der Eigentümer für den Server: entweder die oben gewählte Person (Haken)
   * oder der eigens gewählte Kontakt. Leer = keine Aussage über das Eigentum.
   */
  private eigentuemerId(): string | null {
    const v = this.form.getRawValue();
    if (v.party_id && v.eigentuemer_ist_person) return v.party_id;
    return v.eigentuemer_party_id || null;
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
    const eigentuemer = this.eigentuemerId();
    this.laedt.set(true);

    if (m.art === 'mieter') {
      this.svc
        .addMieter(m.belegung.id, {
          party_id: v.party_id,
          role: v.role,
          eigentuemer_party_id: eigentuemer,
        })
        .subscribe({
          next: () =>
            this.fertig(
              eigentuemer
                ? 'Mieter:in wurde gesetzt — Eigentümer:in steht jetzt auch im Reiter „Eigentum“.'
                : 'Mieter:in wurde gesetzt.',
            ),
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
        eigentuemer_party_id: eigentuemer,
      })
      .subscribe({
        next: () =>
          this.fertig(
            [
              v.party_id
                ? 'Belegung erfasst.'
                : 'Belegung erfasst — ohne Mieter:in (Leerstand).',
              eigentuemer
                ? 'Eigentümer:in steht jetzt auch im Reiter „Eigentum“.'
                : '',
            ]
              .filter(Boolean)
              .join(' '),
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
