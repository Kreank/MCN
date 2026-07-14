import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { AuthService } from '../../core/auth.service';
import { Mandat } from '../../core/verwaltung.model';
import { Verwaltung } from './verwaltung';

/**
 * Der Reiter „Verwaltung". Geprüft wird das, woran die Vorführung sonst scheitert:
 *
 * 1. **Verwaltung ≠ Auftraggeber ≠ Standardkontakt** — alle drei stehen
 *    nebeneinander, jede mit ihrer Funktion **als Text**. Verwechselt man sie,
 *    geht die Rechnung an den Falschen.
 * 2. **Die Telefonnummer** steht als Wähl-Link da (der Monteur ruft an, wenn
 *    niemand aufmacht).
 * 3. **Gelöscht wird nie:** „Mandat beenden" schickt POST auf `/beenden`.
 * 4. **Der Umfang ist unveränderlich** — das UI sagt es, statt es zu verschweigen.
 */
const mandat = (over: Partial<Mandat> = {}): Mandat => ({
  id: 'm-1',
  property_id: 'p-1',
  mandate_type: 'WEG_MANAGEMENT',
  scope_type: 'ENTIRE_PROPERTY',
  status: 'ACTIVE',
  valid_from: '2019-11-01',
  valid_until: null,
  is_current: true,
  contract_reference: null,
  verwaltung: {
    party_id: 'stegos',
    display_name: 'Stegos Immobilien GmbH',
    telefon: '030 79085327',
    email: 'info@stegos.net',
  },
  auftraggeber: {
    party_id: 'weg',
    display_name: 'WEG Badensche Straße 53',
    telefon: null,
    email: null,
  },
  standardkontakt: {
    party_id: 'karin',
    display_name: 'Karin Stegemann',
    telefon: '0170 1234567',
    email: null,
  },
  einheiten: [],
  zustaendigkeiten: [],
  ...over,
});

describe('Verwaltung — Mandat, Rollen, Beenden', () => {
  let fixture: ComponentFixture<Verwaltung>;
  let http: HttpTestingController;

  const el = () => fixture.nativeElement as HTMLElement;
  const text = () => (el().textContent ?? '').replace(/\s+/g, ' ');

  /**
   * Knöpfe der SEITE — die Bestätigungsdialoge liegen immer im DOM, und ihr
   * Bestätigen-Knopf heißt ebenfalls „Mandat beenden". Ein Helfer, der sie
   * mitzählt, würde „der Monteur hat keinen Beenden-Knopf" fälschlich für
   * erfüllt halten — oder, schlimmer, für verletzt. (Dieselbe Falle steht im
   * Kopf von `anlagen.spec.ts`.)
   */
  const knopf = (label: string) =>
    Array.from(el().querySelectorAll('button'))
      .filter((b) => !b.closest('dialog'))
      .find((b) => (b.textContent ?? '').includes(label)) as HTMLButtonElement | undefined;

  /** Der Bestätigen-Knopf IM Dialog (der unumkehrbare Schritt). */
  const dialogKnopf = (label: string) =>
    Array.from(el().querySelectorAll('dialog button')).find(
      (b) => (b.textContent ?? '').trim() === label,
    ) as HTMLButtonElement | undefined;

  const antworten = (liste: Mandat[]) => {
    const req = http.expectOne((r) => r.url === '/api/management/properties/p-1/mandate');
    req.flush(liste);
    fixture.detectChanges();
    return req;
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Verwaltung],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: AuthService, useValue: { darf: () => true, darfAlle: () => true } },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(Verwaltung);
    http = TestBed.inject(HttpTestingController);
    fixture.componentRef.setInput('propertyId', 'p-1');
    fixture.componentRef.setInput('gebaeude', []);
  });

  afterEach(() => http.verify());

  it('lädt standardmäßig nur die geltenden Mandate (keine Historie)', () => {
    fixture.detectChanges();
    const req = antworten([]);
    expect(req.request.params.has('historie')).toBe(false);
  });

  it('DER KERN: Verwaltung, Auftraggeber und Standardkontakt stehen getrennt da', () => {
    fixture.detectChanges();
    antworten([mandat()]);

    const rollen = Array.from(el().querySelectorAll('.rolle')).map((r) =>
      (r.textContent ?? '').replace(/\s+/g, ' '),
    );
    expect(rollen.length).toBe(3);

    const verwaltung = rollen.find((r) => r.includes('Stegos'))!;
    const auftraggeber = rollen.find((r) => r.includes('WEG Badensche'))!;
    const kontakt = rollen.find((r) => r.includes('Karin'))!;

    // Jede Rolle trägt ihre FUNKTION als Text — nicht nur eine Überschrift.
    expect(verwaltung).toContain('führt aus');
    expect(auftraggeber).toContain('beauftragt und zahlt');
    expect(auftraggeber).toContain('hierhin geht die Rechnung');
    expect(kontakt).toContain('nimmt ab');
    // Und sie sind nicht dieselbe Partei.
    expect(verwaltung).not.toContain('WEG Badensche');
  });

  it('die Telefonnummer der Verwaltung ist ein Wähl-Link', () => {
    fixture.detectChanges();
    antworten([mandat()]);
    const tel = el().querySelector('.rolle__tel') as HTMLAnchorElement;
    expect(tel.getAttribute('href')).toBe('tel:03079085327');
  });

  it('fehlende Nummer des Standardkontakts wird ausgesprochen', () => {
    fixture.detectChanges();
    antworten([
      mandat({
        standardkontakt: {
          party_id: 'karin',
          display_name: 'Karin Stegemann',
          telefon: null,
          email: null,
        },
      }),
    ]);
    expect(text()).toContain('Keine Telefonnummer hinterlegt');
  });

  it('Teilmandat: der Umfang wird gezeigt UND als unveränderlich benannt', () => {
    fixture.detectChanges();
    antworten([
      mandat({
        scope_type: 'SELECTED_UNITS',
        einheiten: [{ unit_id: 'u-1', unit_number: 'W1' }],
      }),
    ]);
    expect(text()).toContain('W1');
    expect(text()).toContain('Der Umfang ist nicht änderbar');
  });

  it('GELÖSCHT WIRD NIE: „Mandat beenden" schickt POST auf /beenden', () => {
    fixture.detectChanges();
    antworten([mandat()]);

    knopf('Mandat beenden')!.click();
    fixture.detectChanges();
    // Vor der unumkehrbaren Aktion wird gefragt.
    const bestaetigen = dialogKnopf('Mandat beenden');
    expect(bestaetigen).toBeTruthy();
    bestaetigen!.click();
    fixture.detectChanges();

    const req = http.expectOne('/api/management/mandate/m-1/beenden');
    expect(req.request.method).toBe('POST');
    expect(req.request.body.valid_until).toBeTruthy();
    req.flush(mandat({ status: 'ENDED', is_current: false }));
    fixture.detectChanges();
    antworten([]);
  });

  it('ein beendetes Mandat wird als beendet ausgewiesen und bietet keine Aktionen', () => {
    fixture.detectChanges();
    antworten([mandat({ status: 'ENDED', is_current: false, valid_until: '2025-01-01' })]);
    expect(text()).toContain('Beendet');
    expect(knopf('Mandat beenden')).toBeUndefined();
    expect(knopf('Korrigieren')).toBeUndefined();
  });

  it('MONTEUR (nur LESEN) sieht Verwaltung samt Nummer, kann aber nichts ändern', async () => {
    TestBed.resetTestingModule();
    await TestBed.configureTestingModule({
      imports: [Verwaltung],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        {
          provide: AuthService,
          useValue: { darf: (m: string, a: string) => a === 'LESEN', darfAlle: () => true },
        },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(Verwaltung);
    http = TestBed.inject(HttpTestingController);
    fixture.componentRef.setInput('propertyId', 'p-1');
    fixture.componentRef.setInput('gebaeude', []);
    fixture.detectChanges();
    antworten([mandat()]);

    expect(text()).toContain('Stegos Immobilien GmbH');
    expect((el().querySelector('.rolle__tel') as HTMLAnchorElement).textContent).toContain(
      '030 79085327',
    );
    expect(knopf('Mandat beenden')).toBeUndefined();
    expect(knopf('Korrigieren')).toBeUndefined();
    expect(knopf('＋ Mandat')).toBeUndefined();
  });

  it('es gibt keinen Löschen-Knopf', () => {
    fixture.detectChanges();
    antworten([mandat()]);
    const alle = Array.from(el().querySelectorAll('button'))
      .map((b) => (b.textContent ?? '').toLowerCase())
      .join(' ');
    expect(alle).not.toContain('löschen');
  });
});
