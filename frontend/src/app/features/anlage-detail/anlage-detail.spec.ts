import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { convertToParamMap } from '@angular/router';
import { AuthService } from '../../core/auth.service';
import { AnlageDetail as AnlageDetailModel } from '../../core/anlage.model';
import { AnlageDetail } from './anlage-detail';

/**
 * Die Anlagenmappe. Geprüft wird vor allem die **Ehrlichkeit** der Anzeige:
 *
 * * Der Wartungsvertrag hängt im Schema an der **Liegenschaft**, nicht an der
 *   Anlage. Das UI muss das aussprechen, statt Anlagenbezug vorzutäuschen.
 * * Eine fehlende Leistung ist „unbekannt", nie 0 kW.
 * * 403 → „Kein Zugriff"; ein fremdes Objekt liefert 404 → Fehlerzustand (keine
 *   Existenzaussage).
 */
const DETAIL: AnlageDetailModel = {
  id: 'a-1',
  property_id: 'p-1',
  name: 'Heizzentrale',
  asset_type: 'KESSEL_HEIZUNG',
  status: 'AKTIV',
  supply_type: 'ZENTRAL',
  building_id: null,
  unit_id: null,
  building_label: null,
  unit_label: null,
  unit_storey: null,
  nutzer: [],
  belegung_sichtbar: true,
  manufacturer: 'Viessmann',
  model: null,
  year_built: 1998,
  serial_number: null,
  location_note: 'Keller',
  energy_source: 'GAS',
  power_kw: null,
  note: null,
  wartungsvertraege: [
    {
      id: 'w-1',
      contract_number: 'W-00001',
      name: 'Heizungswartung',
      status: 'AKTIV',
      next_due_date: '2026-09-01',
      bezug: 'LIEGENSCHAFT',
    },
  ],
  pruefungen: [],
  auftraege: [],
  faelligkeiten: [],
  maintenance_sichtbar: true,
  workflow_sichtbar: true,
};

describe('AnlageDetail', () => {
  let fixture: ComponentFixture<AnlageDetail>;
  let http: HttpTestingController;

  const el = () => fixture.nativeElement as HTMLElement;
  const text = () => (el().textContent ?? '').replace(/\s+/g, ' ');

  const laden = (daten: AnlageDetailModel = DETAIL) => {
    fixture.detectChanges();
    http.expectOne('/api/property/assets/a-1').flush(daten);
    fixture.detectChanges();
    // Zweite Anfrage: die Gebäude für den Bearbeiten-Dialog.
    http.expectOne('/api/property/properties/p-1').flush({ buildings: [] });
    fixture.detectChanges();
  };

  const tabOeffnen = (label: string) => {
    const tab = Array.from(el().querySelectorAll('button')).find((b) =>
      (b.textContent ?? '').includes(label),
    ) as HTMLButtonElement;
    tab.click();
    fixture.detectChanges();
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AnlageDetail],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: AuthService, useValue: { darf: () => true, darfAlle: () => true } },
        {
          provide: ActivatedRoute,
          useValue: {
            paramMap: of(convertToParamMap({ id: 'a-1' })),
            snapshot: { paramMap: convertToParamMap({ id: 'a-1' }) },
          },
        },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(AnlageDetail);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('zeigt die Versorgung als Text im Kopf', () => {
    laden();
    expect(text()).toContain('Zentrale Anlage');
  });

  it('fehlende Leistung bleibt „unbekannt" — NIE 0 kW', () => {
    laden();
    expect(text()).toContain('unbekannt');
    expect(text()).not.toContain('0 kW');
  });

  it('nicht erfasste Felder zeigen „—", keinen erfundenen Wert', () => {
    laden();
    expect(text()).toContain('—');
  });

  it('spricht aus, dass der Wartungsvertrag am OBJEKT hängt, nicht an der Anlage', () => {
    laden();
    tabOeffnen('Wartung & Prüfungen');
    expect(text()).toContain('Liegenschaft');
    expect(text()).toContain('W-00001');
  });

  it('ohne Auftrag: erklärt, wie einer an die Anlage kommt', () => {
    laden();
    tabOeffnen('Aufträge');
    expect(text()).toContain('Noch kein Auftrag zu dieser Anlage');
  });

  it('ohne maintenance-Recht: sagt „nicht sichtbar" — behauptet NICHT „gibt es nicht"', () => {
    laden({
      ...DETAIL,
      wartungsvertraege: [],
      maintenance_sichtbar: false,
    });
    tabOeffnen('Wartung & Prüfungen');
    expect(text()).toContain('nicht sichtbar');
    expect(text()).toContain('maintenance/LESEN');
    // Die Lüge, die es NICHT sagen darf:
    expect(text()).not.toContain('Kein Wartungsvertrag an dieser Liegenschaft');
  });

  it('ohne workflow-Recht: dasselbe für die Aufträge', () => {
    laden({ ...DETAIL, auftraege: [], workflow_sichtbar: false });
    tabOeffnen('Aufträge');
    expect(text()).toContain('nicht sichtbar');
    expect(text()).toContain('workflow/LESEN');
    expect(text()).not.toContain('Noch kein Auftrag zu dieser Anlage');
  });

  it('403 zeigt den Sperrhinweis, nicht den Fehlerzustand (kein „Erneut versuchen")', () => {
    fixture.detectChanges();
    http
      .expectOne('/api/property/assets/a-1')
      .flush({ detail: 'Keine Berechtigung' }, { status: 403, statusText: 'Forbidden' });
    fixture.detectChanges();
    expect(text()).toContain('Keine Berechtigung');
    expect(text()).not.toContain('Erneut versuchen');
  });

  it('404 (fremdes oder unbekanntes Objekt) endet im Fehlerzustand, ohne Existenzaussage', () => {
    fixture.detectChanges();
    http
      .expectOne('/api/property/assets/a-1')
      .flush({ detail: 'Anlage nicht gefunden.' }, { status: 404, statusText: 'Not Found' });
    fixture.detectChanges();
    expect(text()).toContain('Anlage konnte nicht geladen werden');
  });
});
