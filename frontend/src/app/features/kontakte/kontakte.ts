import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Subject, debounceTime, distinctUntilChanged } from 'rxjs';
import { PartyService } from '../../core/party.service';
import { AuthService } from '../../core/auth.service';
import {
  OrganizationIn,
  OrganizationTypeCode,
  Party,
  PartyPage,
  PartyStatus,
  PartyType,
  PersonIn,
} from '../../core/party.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: PartyPage }
  | VerbotenState
  | { kind: 'error' };

type Segment = { value: PartyType | null; label: string };
type Meldung = { art: 'erfolg' | 'fehler'; text: string };

@Component({
  selector: 'app-kontakte',
  imports: [RouterLink, ReactiveFormsModule, KeinZugriff, Dialog, Feld],
  templateUrl: './kontakte.html',
  styleUrl: './kontakte.scss',
})
export class Kontakte {
  private readonly svc = inject(PartyService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  protected readonly pageSize = 20;
  protected readonly segments: Segment[] = [
    { value: null, label: 'Alle' },
    { value: 'PERSON', label: 'Personen' },
    { value: 'ORGANIZATION', label: 'Organisationen' },
  ];

  protected readonly orgTypen: FeldOption[] = [
    { wert: 'PROPERTY_MANAGEMENT', label: 'Hausverwaltung' },
    { wert: 'WEG', label: 'WEG' },
    { wert: 'COMPANY', label: 'Unternehmen' },
    { wert: 'AUTHORITY', label: 'Behörde' },
    { wert: 'INSURER', label: 'Versicherung' },
    { wert: 'OTHER', label: 'Sonstige' },
  ];

  protected readonly query = signal('');
  protected readonly partyType = signal<PartyType | null>(null);
  protected readonly page = signal(1);
  protected readonly state = signal<ViewState>({ kind: 'loading' });

  // Fuer das Laden-Skelett.
  protected readonly skeletons = Array.from({ length: 6 });

  // --- Rechte (nur UI-Sichtbarkeit; der Server setzt sie durch) -----------
  protected readonly darfAnlegen = computed(() => this.auth.darf('identity', 'ANLEGEN'));

  // --- Meldung (Erfolg/Fehler) --------------------------------------------
  protected readonly meldung = signal<Meldung | null>(null);

  // --- Anlage-Dialoge ------------------------------------------------------
  protected readonly personOffen = signal(false);
  protected readonly orgOffen = signal(false);
  protected readonly neuLaedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);

  protected readonly personForm = this.fb.group({
    salutation: this.fb.control('', { nonNullable: true }),
    title: this.fb.control('', { nonNullable: true }),
    first_name: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(200)],
    }),
    last_name: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(200)],
    }),
    birth_date: this.fb.control('', { nonNullable: true }),
  });

  protected readonly orgForm = this.fb.group({
    legal_name: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(300)],
    }),
    organization_type: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    display_name: this.fb.control('', { nonNullable: true }),
    legal_form: this.fb.control('', { nonNullable: true }),
    registration_number: this.fb.control('', { nonNullable: true }),
    tax_number: this.fb.control('', { nonNullable: true }),
    vat_id: this.fb.control('', { nonNullable: true }),
  });

  private readonly searchInput$ = new Subject<string>();
  private reqId = 0;

  protected readonly totalPages = computed(() => {
    const s = this.state();
    if (s.kind !== 'ready') return 1;
    return Math.max(1, Math.ceil(s.data.total / s.data.page_size));
  });

  protected readonly resultSummary = computed(() => {
    const s = this.state();
    if (s.kind === 'loading') return 'Kontakte werden geladen.';
    if (s.kind === 'forbidden') return 'Keine Berechtigung für die Kontakte.';
    if (s.kind === 'error') return 'Kontakte konnten nicht geladen werden.';
    const t = s.data.total;
    if (t === 0) return 'Keine Kontakte gefunden.';
    return `${t} ${t === 1 ? 'Kontakt' : 'Kontakte'} gefunden, Seite ${s.data.page} von ${this.totalPages()}.`;
  });

  constructor() {
    this.searchInput$
      .pipe(debounceTime(300), distinctUntilChanged(), takeUntilDestroyed())
      .subscribe((v) => {
        this.query.set(v);
        this.page.set(1);
        this.fetch();
      });
    this.fetch();
  }

  onSearch(value: string): void {
    this.searchInput$.next(value);
  }

  selectSegment(value: PartyType | null): void {
    if (this.partyType() === value) return;
    this.partyType.set(value);
    this.page.set(1);
    this.fetch();
  }

  prev(): void {
    if (this.page() <= 1) return;
    this.page.update((p) => p - 1);
    this.fetch();
  }

  next(): void {
    if (this.page() >= this.totalPages()) return;
    this.page.update((p) => p + 1);
    this.fetch();
  }

  retry(): void {
    this.fetch();
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  private fetch(): void {
    const id = ++this.reqId;
    this.state.set({ kind: 'loading' });
    this.svc
      .list({
        page: this.page(),
        page_size: this.pageSize,
        q: this.query(),
        party_type: this.partyType(),
      })
      .subscribe({
        next: (data) => {
          if (id === this.reqId) this.state.set({ kind: 'ready', data });
        },
        error: (err) => {
          if (id === this.reqId) this.state.set(fehlerState(err));
        },
      });
  }

  // ---- Anlegen: Person ----------------------------------------------------
  personOeffnen(): void {
    this.personForm.reset({
      salutation: '',
      title: '',
      first_name: '',
      last_name: '',
      birth_date: '',
    });
    this.formularMeldung.set(null);
    this.personOffen.set(true);
  }

  personSchliessen(): void {
    if (this.neuLaedt()) return;
    this.personOffen.set(false);
  }

  personAbsenden(): void {
    if (this.neuLaedt()) return;
    serverFehlerZuruecksetzen(this.personForm);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.personForm);
    if (this.personForm.invalid) return;

    const v = this.personForm.getRawValue();
    const payload: PersonIn = {
      first_name: v.first_name.trim(),
      last_name: v.last_name.trim(),
      salutation: v.salutation.trim() || null,
      title: v.title.trim() || null,
      birth_date: v.birth_date || null,
    };

    this.neuLaedt.set(true);
    this.svc.createPerson(payload).subscribe({
      next: (party) => {
        this.neuLaedt.set(false);
        this.personOffen.set(false);
        this.meldung.set({
          art: 'erfolg',
          text: `Person „${party.display_name}“ wurde angelegt.`,
        });
        this.page.set(1);
        this.fetch();
      },
      error: (err) => {
        this.neuLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.personForm).formular);
      },
    });
  }

  // ---- Anlegen: Organisation ---------------------------------------------
  orgOeffnen(): void {
    this.orgForm.reset({
      legal_name: '',
      organization_type: '',
      display_name: '',
      legal_form: '',
      registration_number: '',
      tax_number: '',
      vat_id: '',
    });
    this.formularMeldung.set(null);
    this.orgOffen.set(true);
  }

  orgSchliessen(): void {
    if (this.neuLaedt()) return;
    this.orgOffen.set(false);
  }

  orgAbsenden(): void {
    if (this.neuLaedt()) return;
    serverFehlerZuruecksetzen(this.orgForm);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.orgForm);
    if (this.orgForm.invalid) return;

    const v = this.orgForm.getRawValue();
    const payload: OrganizationIn = {
      legal_name: v.legal_name.trim(),
      organization_type: v.organization_type as OrganizationTypeCode,
      display_name: v.display_name.trim() || null,
      legal_form: v.legal_form.trim() || null,
      registration_number: v.registration_number.trim() || null,
      tax_number: v.tax_number.trim() || null,
      vat_id: v.vat_id.trim() || null,
    };

    this.neuLaedt.set(true);
    this.svc.createOrganization(payload).subscribe({
      next: (party) => {
        this.neuLaedt.set(false);
        this.orgOffen.set(false);
        this.meldung.set({
          art: 'erfolg',
          text: `Organisation „${party.display_name}“ wurde angelegt.`,
        });
        this.page.set(1);
        this.fetch();
      },
      error: (err) => {
        this.neuLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.orgForm).formular);
      },
    });
  }

  // ---- Darstellungshelfer -------------------------------------------------
  monogram(p: Party): string {
    const parts = p.display_name.trim().split(/\s+/).filter(Boolean);
    if (parts.length === 0) return '–';
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }

  shortId(p: Party): string {
    return p.id.replace(/-/g, '').slice(0, 8).toUpperCase();
  }

  typeLabel(t: PartyType): string {
    return t === 'PERSON' ? 'Person' : 'Organisation';
  }

  statusLabel(s: PartyStatus): string {
    switch (s) {
      case 'ACTIVE':
        return 'Aktiv';
      case 'INACTIVE':
        return 'Inaktiv';
      case 'MERGED':
        return 'Zusammengeführt';
    }
  }

  statusClass(s: PartyStatus): string {
    switch (s) {
      case 'ACTIVE':
        return 'stamp--positive';
      case 'MERGED':
        return 'stamp--warn';
      default:
        return '';
    }
  }
}
