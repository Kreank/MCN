import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { AuthService } from '../../core/auth.service';
import { Anlage, AnlageStatus, SupplyType } from '../../core/anlage.model';
import { Anlagen } from './anlagen';

/**
 * Der Reiter „Anlagen" einer Liegenschaft. Drei Dinge werden scharf geprüft:
 *
 * 1. **Zentral oder dezentral steht als TEXT da**, nicht nur als Farbe (WCAG
 *    1.4.1) — es ist die Angabe, die einen Einsatz verändert.
 * 2. **Gelöscht wird nie:** Es gibt keinen Löschen-Knopf; „Stilllegen" schickt ein
 *    PATCH mit `status='INAKTIV'`, kein DELETE.
 * 3. **Fehlende Leistung ist „unbekannt", nie 0 kW** (Projektinvariante).
 */
const anlage = (
  id: string,
  name: string,
  supply_type: SupplyType,
  status: AnlageStatus = 'AKTIV',
  leistung: string | null = null,
): Anlage => ({
  id,
  property_id: 'p-1',
  name,
  asset_type: 'HEIZUNG',
  status,
  supply_type,
  building_id: null,
  unit_id: null,
  building_label: null,
  unit_label: null,
  manufacturer: null,
  model: null,
  year_built: null,
  serial_number: null,
  location_note: null,
  energy_source: null,
  power_kw: leistung,
  note: null,
});

describe('Anlagen — Liste, Versorgung, Stilllegen', () => {
  let fixture: ComponentFixture<Anlagen>;
  let http: HttpTestingController;

  const el = () => fixture.nativeElement as HTMLElement;
  const text = () => (el().textContent ?? '').replace(/\s+/g, ' ');
  /**
   * Text NUR der Anlagenkarten. Der Erfassungsdialog liegt immer im DOM (mit
   * seinem `<select>` samt aller Codelisten-Beschriftungen) — eine Prüfung gegen
   * den Gesamttext würde also „Dezentral" auch dann finden, wenn keine Anlage es
   * ist. Genau so ein Test wäre wertlos.
   */
  const kartenText = () =>
    Array.from(el().querySelectorAll('.karte'))
      .map((k) => k.textContent ?? '')
      .join(' ')
      .replace(/\s+/g, ' ');
  const knopf = (label: string) =>
    Array.from(el().querySelectorAll('button')).find((b) =>
      (b.textContent ?? '').includes(label),
    ) as HTMLButtonElement | undefined;

  const antworten = (liste: Anlage[]) => {
    const req = http.expectOne((r) => r.url === '/api/property/properties/p-1/assets');
    req.flush(liste);
    fixture.detectChanges();
    return req;
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Anlagen],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: AuthService, useValue: { darf: () => true, darfAlle: () => true } },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(Anlagen);
    http = TestBed.inject(HttpTestingController);
    fixture.componentRef.setInput('propertyId', 'p-1');
    fixture.componentRef.setInput('gebaeude', []);
  });

  afterEach(() => http.verify());

  it('lädt standardmäßig NUR die aktiven Anlagen (kein mit_inaktiven)', () => {
    fixture.detectChanges();
    const req = antworten([anlage('a-1', 'Heizzentrale', 'ZENTRAL')]);
    expect(req.request.params.has('mit_inaktiven')).toBe(false);
  });

  it('nennt die zentrale Anlage beim Namen — als Text, nicht nur als Farbe', () => {
    fixture.detectChanges();
    antworten([anlage('a-1', 'Heizzentrale', 'ZENTRAL')]);
    expect(kartenText()).toContain('Zentrale Anlage');
  });

  it('unbekannte Versorgung wird ausgesprochen, nicht als „dezentral" geraten', () => {
    fixture.detectChanges();
    antworten([anlage('a-1', 'Kessel', 'UNBEKANNT')]);
    expect(kartenText()).toContain('Versorgung unbekannt');
    expect(kartenText()).not.toContain('Dezentral');
  });

  it('fehlende Leistung ist „unbekannt" — NIE 0 kW', () => {
    fixture.detectChanges();
    antworten([anlage('a-1', 'Therme', 'DEZENTRAL', 'AKTIV', null)]);
    expect(kartenText()).toContain('unbekannt');
    expect(kartenText()).not.toContain('0 kW');
  });

  it('bekannte Leistung wird mit Einheit gezeigt', () => {
    fixture.detectChanges();
    antworten([anlage('a-1', 'Therme', 'DEZENTRAL', 'AKTIV', '24.50')]);
    expect(kartenText()).toContain('24,5 kW');
  });

  it('bietet KEIN Löschen an — nur Stilllegen', () => {
    fixture.detectChanges();
    antworten([anlage('a-1', 'Heizzentrale', 'ZENTRAL')]);
    expect(knopf('Löschen')).toBeUndefined();
    expect(knopf('Stilllegen')).toBeDefined();
  });

  it('Stilllegen fragt nach und schickt dann PATCH status=INAKTIV (kein DELETE)', () => {
    fixture.detectChanges();
    antworten([anlage('a-1', 'Heizzentrale', 'ZENTRAL')]);

    knopf('Stilllegen')!.click();
    fixture.detectChanges();
    // Der Bestätigungsdialog sagt ausdrücklich, dass NICHT gelöscht wird.
    expect(text()).toContain('NICHT gelöscht');

    // Im Dialog bestätigen (der zweite „Stilllegen"-Knopf ist der des Dialogs).
    const knoepfe = Array.from(el().querySelectorAll('button')).filter((b) =>
      (b.textContent ?? '').includes('Stilllegen'),
    ) as HTMLButtonElement[];
    knoepfe[knoepfe.length - 1].click();
    fixture.detectChanges();

    const req = http.expectOne('/api/property/assets/a-1');
    expect(req.request.method).toBe('PATCH');
    expect(req.request.body).toEqual({ status: 'INAKTIV' });
    req.flush(anlage('a-1', 'Heizzentrale', 'ZENTRAL', 'INAKTIV'));
    fixture.detectChanges();

    // Danach wird frisch geladen — was sichtbar ist, entscheidet der Server.
    antworten([]);
  });

  it('„Stillgelegte anzeigen" ist kein Client-Filter, sondern eine neue Anfrage', () => {
    fixture.detectChanges();
    antworten([anlage('a-1', 'Alt', 'ZENTRAL', 'INAKTIV')]);

    knopf('Stillgelegte anzeigen')!.click();
    fixture.detectChanges();

    const req = http.expectOne(
      (r) => r.url === '/api/property/properties/p-1/assets' && r.params.get('mit_inaktiven') === 'true',
    );
    req.flush([anlage('a-1', 'Alt', 'ZENTRAL', 'INAKTIV')]);
    fixture.detectChanges();
    expect(text()).toContain('Stillgelegt');
  });

  it('ohne Anlagen: fordert zur Erfassung auf, statt leer zu bleiben', () => {
    fixture.detectChanges();
    antworten([]);
    expect(text()).toContain('Noch keine Anlage erfasst');
  });
});
