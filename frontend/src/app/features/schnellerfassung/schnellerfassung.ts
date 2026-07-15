import { Component, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Router, RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { map } from 'rxjs';
import { ProjektService } from '../../core/projekt.service';
import { PropertyService } from '../../core/property.service';
import { PartyService } from '../../core/party.service';
import { CasePriority, QuickIntakeIn } from '../../core/projekt.model';
import { PropertyType } from '../../core/property.model';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { ReferenzWahl, RefSuche } from '../../shared/formular/referenz-wahl';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';

/**
 * Schnelleinstieg „Meldung erfassen" — ein EFH-Eigentümer ruft mit einem Defekt
 * an; in EINEM Formular entstehen Person + Liegenschaft + Vorgang (ohne
 * Projekt). Abgesendet wird genau ein atomarer Aufruf (POST
 * /api/workflow/quick-intake); der Server legt alles in einer Transaktion an und
 * rollt bei einem Fehler zurück. Bei Erfolg springt die Ansicht direkt in die
 * neu entstandene Vorgangsmappe.
 */
@Component({
  selector: 'app-schnellerfassung',
  imports: [RouterLink, ReactiveFormsModule, Feld, ReferenzWahl],
  templateUrl: './schnellerfassung.html',
  styleUrl: './schnellerfassung.scss',
})
export class Schnellerfassung {
  private readonly svc = inject(ProjektService);
  private readonly propertySvc = inject(PropertyService);
  private readonly partySvc = inject(PartyService);
  private readonly router = inject(Router);
  private readonly fb = inject(FormBuilder);

  // Liegenschaftstypen inkl. Einfamilienhaus (Default). Labels wie in
  // liegenschaften.ts, damit die Begriffe im UI konsistent bleiben.
  protected readonly typOptionen: FeldOption[] = [
    { wert: 'EINFAMILIENHAUS', label: 'Einfamilienhaus' },
    { wert: 'WEG', label: 'WEG' },
    { wert: 'RENTAL_PROPERTY', label: 'Mietobjekt' },
    { wert: 'COMMERCIAL', label: 'Gewerbe' },
    { wert: 'MIXED', label: 'Gemischt' },
    { wert: 'OTHER', label: 'Sonstige' },
  ];

  protected readonly prioOptionen: FeldOption[] = [
    { wert: 'NORMAL', label: 'Normal' },
    { wert: 'DRINGEND', label: 'Dringend' },
    { wert: 'NOTFALL', label: 'Notfall' },
  ];

  protected readonly neuLaedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);

  /** ID der gewählten BESTEHENDEN Liegenschaft — leer = neue anlegen. Steuert,
   *  ob die Adressfelder gezeigt/geprüft werden (Dedup: kein Duplikat). */
  protected readonly bestehendeId = signal<string>('');
  /** ID des gewählten BESTEHENDEN Kontakts — leer = neuen anlegen. */
  protected readonly bestehenderKontaktId = signal<string>('');

  /** Suche über bestehende Liegenschaften (Name/Nummer/Ort). */
  protected readonly liegenschaftSuche: RefSuche = (q) =>
    this.propertySvc.list({ page: 1, page_size: 20, q }).pipe(
      map((p) =>
        p.items.map((x) => ({
          id: x.id,
          label: x.name,
          sub: `${x.property_number} · ${x.city}`,
        })),
      ),
    );

  /** Suche über bestehende Kontakte (Name). */
  protected readonly kontaktSuche: RefSuche = (q) =>
    this.partySvc.list({ page: 1, page_size: 20, q }).pipe(
      map((p) => p.items.map((x) => ({ id: x.id, label: x.display_name }))),
    );

  protected readonly form = this.fb.group({
    // Bestehende Liegenschaft (Dedup) — leer = neue anlegen (Felder unten).
    existing_property_id: this.fb.control('', { nonNullable: true }),
    // Bestehender Kontakt (Dedup) — leer = neuen anlegen (Personenfelder unten).
    existing_party_id: this.fb.control('', { nonNullable: true }),
    // Person
    salutation: this.fb.control('', { nonNullable: true }),
    first_name: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(200)],
    }),
    last_name: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(200)],
    }),
    // Kontaktwege
    phone: this.fb.control('', { nonNullable: true }),
    email: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.email],
    }),
    // Liegenschaft (Name wird serverseitig abgeleitet)
    property_type: this.fb.control<PropertyType>('EINFAMILIENHAUS', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    street: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    house_number: this.fb.control('', { nonNullable: true }),
    postal_code: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    city: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    // Meldung (Vorgang)
    subject: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(300)],
    }),
    description: this.fb.control('', { nonNullable: true }),
    priority: this.fb.control<CasePriority>('NORMAL', {
      nonNullable: true,
      validators: [Validators.required],
    }),
  });

  constructor() {
    // Wird eine bestehende Liegenschaft gewählt, sind die Adressfelder überflüssig
    // — Pflicht raus, damit das Formular nicht auf leeren Feldern hängen bleibt.
    // Ohne Auswahl gelten sie wieder als Pflicht.
    this.form.controls.existing_property_id.valueChanges
      .pipe(takeUntilDestroyed())
      .subscribe((val) => {
        const gewaehlt = !!val;
        this.bestehendeId.set(val ?? '');
        const pflichtfelder = [
          this.form.controls.street,
          this.form.controls.postal_code,
          this.form.controls.city,
        ];
        for (const f of pflichtfelder) {
          if (gewaehlt) f.clearValidators();
          else f.setValidators([Validators.required]);
          f.updateValueAndValidity({ emitEvent: false });
        }
      });

    // Analog für den Kontakt: bei bestehendem Melder entfallen Vor-/Nachname.
    this.form.controls.existing_party_id.valueChanges
      .pipe(takeUntilDestroyed())
      .subscribe((val) => {
        const gewaehlt = !!val;
        this.bestehenderKontaktId.set(val ?? '');
        for (const f of [this.form.controls.first_name, this.form.controls.last_name]) {
          if (gewaehlt) f.clearValidators();
          else f.setValidators([Validators.required, Validators.maxLength(200)]);
          f.updateValueAndValidity({ emitEvent: false });
        }
      });
  }

  absenden(): void {
    if (this.neuLaedt()) return;
    serverFehlerZuruecksetzen(this.form);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.form);
    if (this.form.invalid) return;

    const v = this.form.getRawValue();
    const bestehend = this.bestehendeId();
    const bestehenderKontakt = this.bestehenderKontaktId();
    const payload: QuickIntakeIn = {
      person: bestehenderKontakt
        ? {
            // Dedup: bestehenden Kontakt als Melder referenzieren.
            existing_party_id: bestehenderKontakt,
            salutation: null,
            first_name: null,
            last_name: null,
          }
        : {
            salutation: v.salutation.trim() || null,
            first_name: v.first_name.trim(),
            last_name: v.last_name.trim(),
          },
      // Kontaktwege nur beim NEUEN Kontakt sinnvoll — der bestehende hat seine.
      contact: bestehenderKontakt
        ? { phone: null, email: null }
        : {
            phone: v.phone.trim() || null,
            email: v.email.trim() || null,
          },
      property: bestehend
        ? {
            // Dedup: bestehende Liegenschaft referenzieren, keine Adresse senden.
            existing_property_id: bestehend,
            property_type: v.property_type,
            name: null,
            street: null,
            house_number: null,
            postal_code: null,
            city: null,
          }
        : {
            property_type: v.property_type,
            name: null,
            street: v.street.trim(),
            house_number: v.house_number.trim() || null,
            postal_code: v.postal_code.trim(),
            city: v.city.trim(),
          },
      meldung: {
        subject: v.subject.trim(),
        description: v.description.trim() || null,
        priority: v.priority,
      },
    };

    this.neuLaedt.set(true);
    this.svc.quickIntake(payload).subscribe({
      next: (res) => {
        this.neuLaedt.set(false);
        // Direkt in die neu entstandene Vorgangsmappe springen.
        this.router.navigate(['/vorgaenge', res.service_case.id]);
      },
      error: (err) => {
        this.neuLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.form).formular);
      },
    });
  }
}
