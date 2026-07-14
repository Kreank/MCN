import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QuoteMengen } from '../../core/beleg.model';
import { AngebotMengenListe } from './angebot-mengen-liste';

const eintrag = (id: string, nummer: string): QuoteMengen => ({
  id,
  quote_number: nummer,
  title: 'Heizkörper Material und Montage',
  status: 'VERSENDET',
  quote_date: '2026-07-14',
  valid_until_date: null,
  property: { id: 'p-1', property_number: 'OBJ-00001', name: 'Alpha-Hof', city: 'Berlin' },
  work_order_id: null,
  preise_ausgeblendet: true,
});

describe('AngebotMengenListe — „was ist an meinen Objekten beauftragt?"', () => {
  let fixture: ComponentFixture<AngebotMengenListe>;
  let http: HttpTestingController;

  const el = () => fixture.nativeElement as HTMLElement;
  const text = () => (el().textContent ?? '').replace(/\s+/g, ' ');

  const antworten = (items: QuoteMengen[], total = items.length) => {
    const req = http.expectOne((r) => r.url === '/api/invoicing/quotes/mengen');
    req.flush({ items, total, page: 1, page_size: 100 });
    fixture.detectChanges();
    return req;
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AngebotMengenListe],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    }).compileComponents();

    fixture = TestBed.createComponent(AngebotMengenListe);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('zieht aus der preisfreien Liste — nie aus /invoicing/quotes', () => {
    fixture.detectChanges();
    const req = antworten([eintrag('q-1', 'AN-2026-000042')]);
    expect(req.request.url).toBe('/api/invoicing/quotes/mengen');
    expect(text()).toContain('AN-2026-000042');
  });

  it('zeigt keinen Betrag und sagt, dass Preise ausgeblendet sind', () => {
    fixture.detectChanges();
    antworten([eintrag('q-1', 'AN-2026-000042')]);
    expect(text()).toContain('Preise sind für deine Rolle ausgeblendet');
    expect(text()).not.toContain('€');
  });

  it('erklärt die leere Liste, statt sie schweigend zu zeigen', () => {
    fixture.detectChanges();
    antworten([]);
    expect(text()).toContain('Kein Angebot');
    expect(text()).toContain('Entwürfe siehst du nicht');
  });

  it('sagt es, wenn die Liste gekürzt wurde (kein stilles Abschneiden)', () => {
    fixture.detectChanges();
    antworten([eintrag('q-1', 'AN-2026-000042')], 7);
    expect(text()).toContain('Es gibt mehr Angebote, als hier stehen');
  });
});
