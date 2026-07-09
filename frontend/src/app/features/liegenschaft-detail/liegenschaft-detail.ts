import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { PropertyService } from '../../core/property.service';
import { PartyService } from '../../core/party.service';
import { AuthService } from '../../core/auth.service';
import {
  BuildingIn,
  PartyRoleIn,
  PropertyDetail,
  PropertyRoleCode,
  PropertyStatus,
  PropertyType,
  UnitIn,
  UnitTypeCode,
} from '../../core/property.model';
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
  | { kind: 'ready'; data: PropertyDetail }
  | VerbotenState
  | { kind: 'error' };

type Meldung = { art: 'erfolg' | 'fehler'; text: string };

@Component({
  selector: 'app-liegenschaft-detail',
  imports: [Mappe, RouterLink, ReactiveFormsModule, KeinZugriff, Dialog, Feld],
  templateUrl: './liegenschaft-detail.html',
  styleUrl: './liegenschaft-detail.scss',
})
export class LiegenschaftDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(PropertyService);
  private readonly parties = inject(PartyService);
  private readonly fb = inject(FormBuilder);
  private readonly auth = inject(AuthService);

  protected readonly tab = signal('uebersicht');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  protected readonly tabs: MappeTab[] = [
    { id: 'uebersicht', label: 'Übersicht' },
    { id: 'struktur', label: 'Struktur' },
    { id: 'beteiligte', label: 'Beteiligte' },
    { id: 'eigentum', label: 'Eigentum' },
    { id: 'belegung', label: 'Belegung' },
    { id: 'dokumente', label: 'Dokumente' },
  ];

  protected readonly unitTypOptionen: FeldOption[] = [
    { wert: 'APARTMENT', label: 'Wohnung' },
    { wert: 'COMMERCIAL', label: 'Gewerbe' },
    { wert: 'GARAGE', label: 'Garage' },
    { wert: 'PARKING', label: 'Stellplatz' },
    { wert: 'STORAGE', label: 'Lager' },
    { wert: 'COMMON_AREA', label: 'Gemeinschaft' },
    { wert: 'TECHNICAL_ROOM', label: 'Technikraum' },
    { wert: 'OTHER', label: 'Sonstige' },
  ];

  protected readonly rolleOptionen: FeldOption[] = [
    { wert: 'COMMUNITY_OF_OWNERS', label: 'Eigentümergemeinschaft' },
    { wert: 'PROPERTY_OWNER', label: 'Eigentümer' },
    { wert: 'OPERATOR', label: 'Betreiber' },
    { wert: 'CARETAKER', label: 'Hausmeisterei' },
  ];

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  protected readonly einheitenGesamt = computed(() => {
    const d = this.daten();
    if (!d) return 0;
    return d.buildings.reduce((n, b) => n + b.units.length, 0);
  });

  // --- Rechte (nur UI-Sichtbarkeit; der Server setzt sie durch) -----------
  protected readonly darfAnlegen = computed(() => this.auth.darf('property', 'ANLEGEN'));
  protected readonly darfAendern = computed(() => this.auth.darf('property', 'AENDERN'));

  // --- Meldung + Dialoge ---------------------------------------------------
  protected readonly meldung = signal<Meldung | null>(null);
  protected readonly dialogLaedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);

  // Gebäude
  protected readonly gebaeudeOffen = signal(false);
  protected readonly gebaeudeForm = this.fb.group({
    building_number: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    name: this.fb.control('', { nonNullable: true }),
  });

  // Einheit (an ein Gebäude gebunden)
  protected readonly einheitOffen = signal(false);
  protected readonly einheitGebaeude = signal<{ id: string; label: string } | null>(null);
  protected readonly einheitForm = this.fb.group({
    unit_type: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    unit_number: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
  });

  // Beteiligte(r)
  protected readonly beteiligtOffen = signal(false);
  protected readonly parteiOptionen = signal<FeldOption[]>([]);
  protected readonly parteienLaedt = signal(false);
  protected readonly beteiligtForm = this.fb.group({
    party_id: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    role: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    valid_from: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    valid_until: this.fb.control('', { nonNullable: true }),
  });

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.tab.set('uebersicht');
      this.meldung.set(null);
      if (!id) {
        this.state.set({ kind: 'error' });
        return;
      }
      this.load(id);
    });
  }

  retry(): void {
    const id = this.route.snapshot.paramMap.get('id');
    if (id) this.load(id);
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  private load(id: string): void {
    const rid = ++this.reqId;
    this.state.set({ kind: 'loading' });
    this.svc.get(id).subscribe({
      next: (data) => {
        if (rid === this.reqId) this.state.set({ kind: 'ready', data });
      },
      error: (err) => {
        if (rid === this.reqId) this.state.set(fehlerState(err));
      },
    });
  }

  /** Detail neu laden, aktiven Tab beibehalten (nach Schreibaktion). */
  private reload(): void {
    const d = this.daten();
    if (d) this.load(d.id);
  }

  // ---- Gebäude anlegen ----------------------------------------------------
  gebaeudeOeffnen(): void {
    this.gebaeudeForm.reset({ building_number: '', name: '' });
    this.formularMeldung.set(null);
    this.gebaeudeOffen.set(true);
  }

  gebaeudeSchliessen(): void {
    if (this.dialogLaedt()) return;
    this.gebaeudeOffen.set(false);
  }

  gebaeudeAbsenden(): void {
    const d = this.daten();
    if (this.dialogLaedt() || !d) return;
    serverFehlerZuruecksetzen(this.gebaeudeForm);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.gebaeudeForm);
    if (this.gebaeudeForm.invalid) return;

    const v = this.gebaeudeForm.getRawValue();
    const payload: BuildingIn = {
      building_number: v.building_number.trim(),
      name: v.name.trim() || null,
    };
    this.dialogLaedt.set(true);
    this.svc.addBuilding(d.id, payload).subscribe({
      next: (b) => {
        this.dialogLaedt.set(false);
        this.gebaeudeOffen.set(false);
        this.meldung.set({ art: 'erfolg', text: `Gebäude ${b.building_number} wurde angelegt.` });
        this.reload();
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.gebaeudeForm).formular);
      },
    });
  }

  // ---- Einheit anlegen ----------------------------------------------------
  einheitOeffnen(buildingId: string, buildingLabel: string): void {
    this.einheitGebaeude.set({ id: buildingId, label: buildingLabel });
    this.einheitForm.reset({ unit_type: '', unit_number: '' });
    this.formularMeldung.set(null);
    this.einheitOffen.set(true);
  }

  einheitSchliessen(): void {
    if (this.dialogLaedt()) return;
    this.einheitOffen.set(false);
  }

  einheitAbsenden(): void {
    const geb = this.einheitGebaeude();
    if (this.dialogLaedt() || !geb) return;
    serverFehlerZuruecksetzen(this.einheitForm);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.einheitForm);
    if (this.einheitForm.invalid) return;

    const v = this.einheitForm.getRawValue();
    const payload: UnitIn = {
      unit_type: v.unit_type as UnitTypeCode,
      unit_number: v.unit_number.trim(),
    };
    this.dialogLaedt.set(true);
    this.svc.addUnit(geb.id, payload).subscribe({
      next: (u) => {
        this.dialogLaedt.set(false);
        this.einheitOffen.set(false);
        this.meldung.set({ art: 'erfolg', text: `Einheit ${u.unit_number} wurde angelegt.` });
        this.reload();
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.einheitForm).formular);
      },
    });
  }

  // ---- Beteiligte(r) zuordnen --------------------------------------------
  beteiligtOeffnen(): void {
    this.beteiligtForm.reset({ party_id: '', role: '', valid_from: '', valid_until: '' });
    this.formularMeldung.set(null);
    this.beteiligtOffen.set(true);
    this.parteienLaden();
  }

  beteiligtSchliessen(): void {
    if (this.dialogLaedt()) return;
    this.beteiligtOffen.set(false);
  }

  private parteienLaden(): void {
    // Kontakte fuer die Auswahl laden (Personen und Organisationen). Ein
    // dedizierter Autocomplete-Baustein existiert noch nicht — deshalb eine
    // Select-Auswahl ueber die ersten 100 Kontakte (alphabetisch).
    this.parteienLaedt.set(true);
    this.parties.list({ page: 1, page_size: 100 }).subscribe({
      next: (page) => {
        this.parteienLaedt.set(false);
        this.parteiOptionen.set(
          page.items.map((p) => ({
            wert: p.id,
            label: `${p.display_name} (${p.party_type === 'PERSON' ? 'Person' : 'Organisation'})`,
          })),
        );
      },
      error: () => {
        this.parteienLaedt.set(false);
        this.parteiOptionen.set([]);
      },
    });
  }

  beteiligtAbsenden(): void {
    const d = this.daten();
    if (this.dialogLaedt() || !d) return;
    serverFehlerZuruecksetzen(this.beteiligtForm);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.beteiligtForm);
    if (this.beteiligtForm.invalid) return;

    const v = this.beteiligtForm.getRawValue();
    const payload: PartyRoleIn = {
      party_id: v.party_id,
      role: v.role as PropertyRoleCode,
      valid_from: v.valid_from,
      valid_until: v.valid_until || null,
    };
    this.dialogLaedt.set(true);
    this.svc.addPartyRole(d.id, payload).subscribe({
      next: (r) => {
        this.dialogLaedt.set(false);
        this.beteiligtOffen.set(false);
        this.meldung.set({
          art: 'erfolg',
          text: `${this.roleLabel(r.role)}: ${r.party_display_name} wurde zugeordnet.`,
        });
        this.reload();
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.beteiligtForm).formular);
      },
    });
  }

  // ---- Darstellungshelfer -------------------------------------------------
  typeLabel(t: PropertyType): string {
    switch (t) {
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
    return s === 'ACTIVE' ? 'stamp--positive' : '';
  }

  unitTypeLabel(t: string): string {
    const map: Record<string, string> = {
      APARTMENT: 'Wohnung',
      COMMERCIAL: 'Gewerbe',
      GARAGE: 'Garage',
      PARKING: 'Stellplatz',
      STORAGE: 'Lager',
      COMMON_AREA: 'Gemeinschaft',
      TECHNICAL_ROOM: 'Technikraum',
      OTHER: 'Sonstige',
    };
    return map[t] ?? t;
  }

  roleLabel(r: PropertyRoleCode): string {
    const map: Record<PropertyRoleCode, string> = {
      COMMUNITY_OF_OWNERS: 'Eigentümergemeinschaft',
      PROPERTY_OWNER: 'Eigentümer',
      OPERATOR: 'Betreiber',
      CARETAKER: 'Hausmeisterei',
    };
    return map[r] ?? r;
  }
}
