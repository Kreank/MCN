import { Component, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { ProjektService } from '../../core/projekt.service';
import { CasePriority, QuickIntakeIn } from '../../core/projekt.model';
import { PropertyType } from '../../core/property.model';
import { Feld, FeldOption } from '../../shared/formular/feld';
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
  imports: [RouterLink, ReactiveFormsModule, Feld],
  templateUrl: './schnellerfassung.html',
  styleUrl: './schnellerfassung.scss',
})
export class Schnellerfassung {
  private readonly svc = inject(ProjektService);
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

  protected readonly form = this.fb.group({
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

  absenden(): void {
    if (this.neuLaedt()) return;
    serverFehlerZuruecksetzen(this.form);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.form);
    if (this.form.invalid) return;

    const v = this.form.getRawValue();
    const payload: QuickIntakeIn = {
      person: {
        salutation: v.salutation.trim() || null,
        first_name: v.first_name.trim(),
        last_name: v.last_name.trim(),
      },
      contact: {
        phone: v.phone.trim() || null,
        email: v.email.trim() || null,
      },
      property: {
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
