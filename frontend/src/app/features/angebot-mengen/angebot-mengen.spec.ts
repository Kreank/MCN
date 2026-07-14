import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { convertToParamMap } from '@angular/router';
import { QuoteMengenDetail } from '../../core/beleg.model';
import { AngebotMengen } from './angebot-mengen';

/**
 * Die Angebotsansicht des Monteurs. Drei Dinge werden scharf geprüft:
 *
 * 1. **Sie ruft den preisfreien Endpunkt auf** (`/mengen`) — nicht das
 *    Angebotsdetail mit einer versteckten Spalte. Der Preis darf gar nicht erst im
 *    Browser ankommen.
 * 2. **Sie zeigt Menge und Einheit** — „12 m Kupferrohr DN20" ist der Zweck.
 * 3. **Sie sagt, dass Preise fehlen.** Spalten wortlos wegzulassen wäre eine Lüge
 *    durch Auslassung (dieselbe Ehrlichkeitsregel wie bei der gekürzten
 *    Trefferliste der Suche).
 */
const angebot = (preise_ausgeblendet = true): QuoteMengenDetail => ({
  id: 'q-1',
  quote_number: 'AN-2026-000042',
  title: 'Heizkörper Material und Montage',
  status: 'VERSENDET',
  quote_date: '2026-07-14',
  valid_until_date: null,
  property: { id: 'p-1', property_number: 'OBJ-00001', name: 'Alpha-Hof', city: 'Berlin' },
  work_order_id: 'wo-1',
  preise_ausgeblendet,
  project: null,
  work_order: { id: 'wo-1', order_number: 'AU-2026-000012', title: 'Heizkörper tauschen' },
  sent_at: null,
  rubriken: [],
  lines: [
    {
      position_number: 1,
      line_type: 'MATERIAL',
      line_kind: 'NORMAL',
      rubrik: null,
      description: 'Kupferrohr DN20',
      quantity: '12.000',
      unit: 'm',
      source_article_id: null,
      source_assembly_id: null,
    },
    {
      position_number: 2,
      line_type: 'MATERIAL',
      line_kind: 'ALTERNATIV',
      rubrik: null,
      description: 'Kupferrohr DN25 (Alternative)',
      quantity: '12.000',
      unit: 'm',
      source_article_id: null,
      source_assembly_id: null,
    },
  ],
});

describe('AngebotMengen — Mengen ja, Geld nie', () => {
  let fixture: ComponentFixture<AngebotMengen>;
  let http: HttpTestingController;

  const el = () => fixture.nativeElement as HTMLElement;
  const text = () => (el().textContent ?? '').replace(/\s+/g, ' ');

  const antworten = (daten: QuoteMengenDetail) => {
    const req = http.expectOne('/api/invoicing/quotes/q-1/mengen');
    req.flush(daten);
    fixture.detectChanges();
    return req;
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AngebotMengen],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        {
          provide: ActivatedRoute,
          useValue: {
            paramMap: of(convertToParamMap({ id: 'q-1' })),
            snapshot: { paramMap: convertToParamMap({ id: 'q-1' }) },
          },
        },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(AngebotMengen);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('holt die Mengensicht — nie das preisführende Angebotsdetail', () => {
    fixture.detectChanges();
    const req = antworten(angebot());
    expect(req.request.method).toBe('GET');
    // Kein Aufruf auf /api/invoicing/quotes/q-1 (ohne /mengen): http.verify() im
    // afterEach schlägt sonst fehl.
    expect(req.request.url.endsWith('/mengen')).toBe(true);
  });

  it('zeigt Menge und Einheit — „12 m Kupferrohr DN20"', () => {
    fixture.detectChanges();
    antworten(angebot());
    expect(text()).toContain('Kupferrohr DN20');
    expect(text()).toContain('12 m');
  });

  it('sagt EHRLICH, dass Preise ausgeblendet sind (statt Spalten wegzulassen)', () => {
    fixture.detectChanges();
    antworten(angebot());
    expect(text()).toContain('Preise sind für deine Rolle ausgeblendet');
  });

  it('hat KEINE Preisspalte, KEINE Summenzeile und keinen Betrag in der Tabelle', () => {
    fixture.detectChanges();
    antworten(angebot());
    const kopfzeilen = Array.from(el().querySelectorAll('thead th')).map((t) => t.textContent);
    expect(kopfzeilen).toEqual(['Pos.', 'Bezeichnung', 'Menge']);
    expect(el().querySelector('tfoot')).toBeNull();

    // Der Betragsscan gilt der TABELLE — dort stünde ein Preis. Das €-Zeichen im
    // Hinweis ist die durchgestrichene Marke (aria-hidden) und sagt genau das
    // Gegenteil aus; es hier mitzuzählen, machte den Test wertlos.
    const tabelle = (el().querySelector('.pos')?.textContent ?? '').replace(/\s+/g, ' ');
    expect(tabelle).toContain('12 m');
    expect(tabelle).not.toContain('€');
    // Kein deutscher Geldbetrag (1.234,56) — die Menge „12 m" hat kein Komma.
    expect(tabelle).not.toMatch(/\d,\d{2}\b/);
  });

  it('nennt die ALTERNATIV-Position als nicht beauftragt (sonst baut er sie ein)', () => {
    fixture.detectChanges();
    antworten(angebot());
    expect(text()).toContain('Alternative — nicht beauftragt');
  });

  it('behauptet für row_scope ALLE keinen Beschnitt (kein Hinweis)', () => {
    fixture.detectChanges();
    antworten(angebot(false));
    expect(text()).not.toContain('ausgeblendet');
  });
});
