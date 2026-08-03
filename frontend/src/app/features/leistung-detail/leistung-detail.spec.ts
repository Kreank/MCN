import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { AuthService } from '../../core/auth.service';
import { AssemblyDetail, AssemblyKalkulation } from '../../core/artikel.model';
import { LeistungDetail } from './leistung-detail';

/**
 * Die Leistungsmappe. Zwei Dinge sind hier heikel:
 *
 * * **Bearbeiten darf nichts verlieren.** Der Dialog schaltet beim Öffnen die
 *   Positionsart um, und der Umschalter leert die Felder der jeweils anderen
 *   Art. Träfe das die geladene Position, verlöre „Bearbeiten" den Artikel —
 *   und das Speichern schriebe eine leere Position zurück.
 * * **Die Kalkulation muss ehrlich sein.** Fehlt ein Preis, ist er unbekannt,
 *   nicht null Euro; und ohne Einkaufspreise wird keine Marge behauptet.
 */
const DETAIL: AssemblyDetail = {
  id: 'l-1',
  assembly_number: 'LEI-00001',
  name: 'Ziegel verlegen',
  unit: 'm²',
  status: 'AKTIV',
  internal_name: null,
  description: null,
  version: 1,
  components: [
    {
      position: 1,
      kind: 'MATERIAL',
      description: 'Dachziegel',
      quantity: '2.000',
      unit: 'Stk',
      minutes: null,
      article_id: 'art-1',
      wage_group_id: null,
      note: 'ohne Bruch',
    },
    {
      position: 2,
      kind: 'LOHN',
      description: 'Monteur',
      quantity: null,
      unit: null,
      minutes: '30.00',
      article_id: null,
      wage_group_id: 'wg-1',
      note: null,
    },
  ],
};

const KALK: AssemblyKalkulation = {
  assembly_id: 'l-1',
  assembly_number: 'LEI-00001',
  name: 'Ziegel verlegen',
  unit: 'm²',
  positionen: [
    {
      position: 1,
      kind: 'MATERIAL',
      description: 'Dachziegel',
      reference: 'ART-00001',
      quantity: '2.000',
      unit: 'Stk',
      minutes: null,
      ek_je_einheit: null,
      vk_je_einheit: '10.00',
      ek_summe: null,
      vk_summe: '20.00',
      hinweis: null,
    },
  ],
  material_ek: '0.00',
  material_vk: '20.00',
  lohn_ek: '15.00',
  lohn_vk: '30.00',
  minuten_gesamt: '30.00',
  ek_gesamt: '15.00',
  vk_gesamt: '50.00',
  lohnanteil_vk: '30.00',
  marge_prozent: null,
  vollstaendig: true,
  kosten_vollstaendig: false,
};

describe('LeistungDetail', () => {
  let fixture: ComponentFixture<LeistungDetail>;
  let http: HttpTestingController;

  const el = () => fixture.nativeElement as HTMLElement;
  const text = () => (el().textContent ?? '').replace(/\s+/g, ' ');
  const knopf = (beschriftung: string) =>
    Array.from(el().querySelectorAll('button')).find((b) =>
      (b.textContent ?? '').includes(beschriftung),
    ) as HTMLButtonElement;

  const laden = (daten: AssemblyDetail = DETAIL) => {
    fixture.detectChanges();
    http.expectOne('/api/pricing/assemblies/l-1').flush(daten);
    fixture.detectChanges();
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [LeistungDetail],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: AuthService, useValue: { darf: () => true, darfAlle: () => true } },
        {
          provide: ActivatedRoute,
          useValue: {
            paramMap: of(convertToParamMap({ id: 'l-1' })),
            snapshot: { paramMap: convertToParamMap({ id: 'l-1' }) },
          },
        },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(LeistungDetail);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('zeigt die Stückliste mit Mengen und Zeiten', () => {
    laden();
    expect(text()).toContain('Dachziegel');
    expect(text()).toContain('2 Stk');
    expect(text()).toContain('30 min');
  });

  it('Bearbeiten füllt die Materialposition vollständig vor', () => {
    laden();
    knopf('Bearbeiten').click();
    fixture.detectChanges();
    // Die Lohngruppen für den Dialog werden nachgeladen.
    http.expectOne('/api/pricing/wage_groups').flush([]);
    fixture.detectChanges();

    const form = (fixture.componentInstance as any).posForm;
    expect(form.controls.kind.value).toBe('MATERIAL');
    // Der entscheidende Punkt: der Artikel darf NICHT vom Umschalter der
    // Positionsart weggeräumt worden sein.
    expect(form.controls.article_id.value).toBe('art-1');
    expect(form.controls.quantity.value).toBe('2.000');
    expect(form.controls.note.value).toBe('ohne Bruch');
  });

  it('Entfernen schickt die Liste OHNE die entfernte Position', () => {
    laden();
    const entfernen = Array.from(el().querySelectorAll('button')).filter((b) =>
      (b.textContent ?? '').includes('Entfernen'),
    ) as HTMLButtonElement[];
    entfernen[0].click();
    fixture.detectChanges();

    const req = http.expectOne('/api/pricing/assemblies/l-1/components');
    expect(req.request.method).toBe('PUT');
    expect(req.request.body.components).toHaveLength(1);
    expect(req.request.body.components[0].wage_group_id).toBe('wg-1');
    req.flush({ ...DETAIL, components: [DETAIL.components[1]] });
  });

  it('Umsortieren tauscht die Reihenfolge, nicht den Inhalt', () => {
    laden();
    const runter = Array.from(el().querySelectorAll('button')).filter(
      (b) => (b.getAttribute('aria-label') ?? '').includes('nach unten'),
    ) as HTMLButtonElement[];
    runter[0].click();
    fixture.detectChanges();

    const req = http.expectOne('/api/pricing/assemblies/l-1/components');
    expect(req.request.body.components[0].wage_group_id).toBe('wg-1');
    expect(req.request.body.components[1].article_id).toBe('art-1');
    req.flush(DETAIL);
  });

  it('ohne Einkaufspreise wird keine Marge behauptet', () => {
    laden();
    knopf('Kalkulation').click();
    fixture.detectChanges();
    http.expectOne('/api/pricing/assemblies/l-1/kalkulation').flush(KALK);
    fixture.detectChanges();

    expect(text()).toContain('50,00');            // der Verkaufspreis steht
    expect(text()).toContain('Kosten unvollständig');

    // Die Marge-Kachel bleibt leer statt zu schön. Gezielt DIESE Kachel prüfen,
    // nicht die ganze Seite — sonst bricht der Test am nächsten Prozentzeichen
    // irgendwo anders und behauptet einen Fehler, den es nicht gibt.
    const margeKachel = Array.from(el().querySelectorAll('.summe')).find((k) =>
      (k.textContent ?? '').includes('Marge'),
    ) as HTMLElement;
    expect(margeKachel.querySelector('.summe__wert')!.textContent).toContain('—');
  });

  it('ein unbekannter Einzelpreis erscheint als „—", nicht als 0,00 €', () => {
    laden();
    knopf('Kalkulation').click();
    fixture.detectChanges();
    http.expectOne('/api/pricing/assemblies/l-1/kalkulation').flush(KALK);
    fixture.detectChanges();

    const zeilen = el().querySelectorAll('.pos__num--leer');
    expect(zeilen.length).toBeGreaterThan(0);
    expect(zeilen[0].textContent).toContain('—');
  });

  it('die leere Stückliste sagt, warum das ein Problem ist', () => {
    laden({ ...DETAIL, components: [] });
    expect(text()).toContain('keinen Preis vorschlagen');
  });
});
