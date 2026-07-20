import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Subject, debounceTime, distinctUntilChanged, map, startWith } from 'rxjs';
import { PropertyService } from '../../core/property.service';
import { AuthService } from '../../core/auth.service';
import {
  AdressDublettenQuery,
  AdressTreffer,
  Property,
  PropertyIn,
  PropertyPage,
  PropertyStatus,
  PropertyType,
} from '../../core/property.model';
import {
  AdressDublettenHinweis,
  adressDublettenStrom,
} from '../../shared/adress-dubletten/adress-dubletten';
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
  | { kind: 'ready'; data: PropertyPage }
  | VerbotenState
  | { kind: 'error' };

type Segment = { value: PropertyType | null; label: string };
type Meldung = { art: 'erfolg' | 'fehler'; text: string };

@Component({
  selector: 'app-liegenschaften',
  imports: [
    RouterLink,
    ReactiveFormsModule,
    KeinZugriff,
    Dialog,
    Feld,
    AdressDublettenHinweis,
  ],
  templateUrl: './liegenschaften.html',
  styleUrl: './liegenschaften.scss',
})
export class Liegenschaften {
  private readonly svc = inject(PropertyService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  protected readonly pageSize = 20;
  protected readonly segments: Segment[] = [
    { value: null, label: 'Alle' },
    { value: 'EINFAMILIENHAUS', label: 'Einfamilienhaus' },
    { value: 'WEG', label: 'WEG' },
    { value: 'RENTAL_PROPERTY', label: 'Mietobjekt' },
    { value: 'COMMERCIAL', label: 'Gewerbe' },
    { value: 'MIXED', label: 'Gemischt' },
    { value: 'OTHER', label: 'Sonstige' },
  ];

  protected readonly typOptionen: FeldOption[] = [
    { wert: 'EINFAMILIENHAUS', label: 'Einfamilienhaus' },
    { wert: 'WEG', label: 'WEG' },
    { wert: 'RENTAL_PROPERTY', label: 'Mietobjekt' },
    { wert: 'COMMERCIAL', label: 'Gewerbe' },
    { wert: 'MIXED', label: 'Gemischt' },
    { wert: 'OTHER', label: 'Sonstige' },
  ];

  protected readonly query = signal('');
  protected readonly propertyType = signal<PropertyType | null>(null);
  protected readonly page = signal(1);
  protected readonly state = signal<ViewState>({ kind: 'loading' });

  // Fuer das Laden-Skelett.
  protected readonly skeletons = Array.from({ length: 6 });

  // --- Rechte (nur UI-Sichtbarkeit; der Server setzt sie durch) -----------
  /**
   * `darfAlle`, nicht `darf`: `POST /api/property/properties` ist fail-closed
   * (`permissions.require`) — eine neue Liegenschaft ist per Definition keine
   * „eigene" Zeile, ein Konto mit row_scope EIGENE bekommt dort 403. Der MONTEUR
   * trägt `property/ANLEGEN` mit Scope EIGENE (er darf Gebäude, Einheiten und
   * Räume an SEINEM Objekt anlegen — die Endpunkte dort sind `require_scoped`).
   * Mit `darf` stünde ihm hier ein Knopf, der nur 403 kann.
   */
  protected readonly darfAnlegen = computed(() => this.auth.darfAlle('property', 'ANLEGEN'));

  // --- Meldung + Anlage-Dialog --------------------------------------------
  protected readonly meldung = signal<Meldung | null>(null);
  protected readonly neuOffen = signal(false);
  protected readonly neuLaedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);
  protected readonly neuForm = this.fb.group({
    name: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(300)],
    }),
    property_type: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    street: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    house_number: this.fb.control('', { nonNullable: true }),
    address_addition: this.fb.control('', { nonNullable: true }),
    postal_code: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    city: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    country_code: this.fb.control('DE', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(2)],
    }),
  });

  /** Treffer der Live-Dublettenprüfung im Anlagedialog. */
  protected readonly dubletten = signal<AdressTreffer[]>([]);

  private readonly searchInput$ = new Subject<string>();
  private reqId = 0;

  protected readonly totalPages = computed(() => {
    const s = this.state();
    if (s.kind !== 'ready') return 1;
    return Math.max(1, Math.ceil(s.data.total / s.data.page_size));
  });

  protected readonly resultSummary = computed(() => {
    const s = this.state();
    if (s.kind === 'loading') return 'Liegenschaften werden geladen.';
    if (s.kind === 'forbidden') return 'Keine Berechtigung für die Liegenschaften.';
    if (s.kind === 'error') return 'Liegenschaften konnten nicht geladen werden.';
    const t = s.data.total;
    if (t === 0) return 'Keine Liegenschaften gefunden.';
    return `${t} ${t === 1 ? 'Liegenschaft' : 'Liegenschaften'} gefunden, Seite ${s.data.page} von ${this.totalPages()}.`;
  });

  constructor() {
    this.searchInput$
      .pipe(debounceTime(300), distinctUntilChanged(), takeUntilDestroyed())
      .subscribe((v) => {
        this.query.set(v);
        this.page.set(1);
        this.fetch();
      });

    // Dublettenwarnung im Anlagedialog. Anders als in der Schnellerfassung gibt
    // es hier kein Zielfeld — der Treffer führt per Link zur bestehenden
    // Liegenschaft. Anlegen bleibt erlaubt; der Hinweis blockiert nichts.
    adressDublettenStrom(
      this.svc,
      this.neuForm.valueChanges.pipe(
        startWith(null),
        map(() => this.dublettenAbfrage()),
      ),
    )
      .pipe(takeUntilDestroyed())
      .subscribe((t) => this.dubletten.set(t));

    this.fetch();
  }

  /** Abfrageparameter der Dublettenprüfung — `null`, wenn nicht geprüft wird. */
  private dublettenAbfrage(): AdressDublettenQuery | null {
    if (!this.neuOffen()) return null;
    const v = this.neuForm.getRawValue();
    const street = v.street.trim();
    const postal_code = v.postal_code.trim();
    const city = v.city.trim();
    // Straße allein träfe quer durch die Republik — PLZ oder Ort muss dazu.
    if (!street || (!postal_code && !city)) return null;
    return {
      street,
      house_number: v.house_number.trim() || null,
      postal_code: postal_code || null,
      city: city || null,
    };
  }

  onSearch(value: string): void {
    this.searchInput$.next(value);
  }

  selectSegment(value: PropertyType | null): void {
    if (this.propertyType() === value) return;
    this.propertyType.set(value);
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

  // ---- Anlegen ------------------------------------------------------------
  neuOeffnen(): void {
    this.neuForm.reset({
      name: '',
      property_type: '',
      street: '',
      house_number: '',
      address_addition: '',
      postal_code: '',
      city: '',
      country_code: 'DE',
    });
    this.formularMeldung.set(null);
    this.dubletten.set([]);
    this.neuOffen.set(true);
  }

  neuSchliessen(): void {
    if (this.neuLaedt()) return;
    this.neuOffen.set(false);
    this.dubletten.set([]);
  }

  /** „Öffnen" an einem Treffer: Dialog zu, danach navigiert der RouterLink. */
  protected dubletteGeoeffnet(): void {
    this.neuOffen.set(false);
    this.dubletten.set([]);
  }

  neuAbsenden(): void {
    if (this.neuLaedt()) return;
    serverFehlerZuruecksetzen(this.neuForm);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.neuForm);
    if (this.neuForm.invalid) return;

    const v = this.neuForm.getRawValue();
    const payload: PropertyIn = {
      name: v.name.trim(),
      property_type: v.property_type as PropertyType,
      street: v.street.trim(),
      postal_code: v.postal_code.trim(),
      city: v.city.trim(),
      house_number: v.house_number.trim() || null,
      address_addition: v.address_addition.trim() || null,
      country_code: v.country_code.trim().toUpperCase() || 'DE',
    };

    this.neuLaedt.set(true);
    this.svc.create(payload).subscribe({
      next: (prop) => {
        this.neuLaedt.set(false);
        this.neuOffen.set(false);
        this.dubletten.set([]);
        this.meldung.set({
          art: 'erfolg',
          text: `Liegenschaft „${prop.name}“ (Nr. ${prop.property_number}) wurde angelegt.`,
        });
        this.page.set(1);
        this.fetch();
      },
      error: (err) => {
        this.neuLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.neuForm).formular);
      },
    });
  }

  private fetch(): void {
    const id = ++this.reqId;
    this.state.set({ kind: 'loading' });
    this.svc
      .list({
        page: this.page(),
        page_size: this.pageSize,
        q: this.query(),
        property_type: this.propertyType(),
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

  // ---- Darstellungshelfer -------------------------------------------------
  monogram(p: Property): string {
    const parts = p.name.trim().split(/\s+/).filter(Boolean);
    if (parts.length === 0) return '–';
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }

  typeLabel(t: PropertyType): string {
    switch (t) {
      case 'EINFAMILIENHAUS':
        return 'Einfamilienhaus';
      case 'WEG':
        return 'WEG';
      case 'RENTAL_PROPERTY':
        return 'Mietobjekt';
      case 'COMMERCIAL':
        return 'Gewerbe';
      case 'MIXED':
        return 'Gemischt';
      case 'OTHER':
        return 'Sonstige';
    }
  }

  statusLabel(s: PropertyStatus): string {
    return s === 'ACTIVE' ? 'Aktiv' : 'Inaktiv';
  }

  statusClass(s: PropertyStatus): string {
    // Aktiv = gruener Stempel; Inaktiv nutzt den neutralen Basis-Stempel.
    return s === 'ACTIVE' ? 'stamp--positive' : '';
  }
}
