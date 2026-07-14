import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { AuthService } from '../../core/auth.service';
import { NachtragVorschau, WorkOrderDetail } from '../../core/auftrag.model';
import { Nachtrag } from './nachtrag';

/**
 * „Nachtrag abrechnen" — der Knopf, der aus einer sichtbaren Abweichung Geld macht.
 *
 * Drei Dinge werden scharf geprüft (alle drei enden sonst in Geld):
 *
 * 1. **Nur die Differenz.** 19 statt 18 → abzurechnen ist **1**, nicht 19. Die
 *    Sollmenge ist mit der Pauschale bezahlt.
 * 2. **Kein toter Knopf.** Gibt es nichts abzurechnen, sagt der Abschnitt WARUM —
 *    keine Abweichung, oder alles schon fakturiert (mit Belegnummer).
 * 3. **Ein unbekannter Preis bleibt unbekannt.** Nie 0,00 €, und die Summe sagt
 *    ausdrücklich, dass sie unvollständig ist.
 */
const auftrag = {
  id: 'wo-1',
  billing_mode: 'PAUSCHAL',
  property: { id: 'p-1' },
} as unknown as WorkOrderDetail;

const vorschau = (v: Partial<NachtragVorschau> = {}): NachtragVorschau => ({
  work_order_id: 'wo-1',
  billing_mode: 'PAUSCHAL',
  abrechenbar: true,
  hinweis: 'Nachtrag: Abgerechnet werden ausschließlich die Abweichungen.',
  positionen: [],
  bereits_abgerechnet: [],
  einheit_konflikte: [],
  summe: '0.00',
  preise_unbekannt: false,
  nicht_unterzeichnete_berichte: [],
  ...v,
});

const mehrverbrauch = (preisBekannt = true) => ({
  schluessel: 'ARTIKEL:a-1:stk',
  art: 'MEHRVERBRAUCH' as const,
  bezeichnung: 'Thermostatventil',
  einheit: 'stk',
  soll: '18.000',
  ist: '19.000',
  menge: '1.000',
  bereits_berechnet: '0.000',
  preis_status: (preisBekannt ? 'BEKANNT' : 'UNBEKANNT') as 'BEKANNT' | 'UNBEKANNT',
  einzelpreis: preisBekannt ? '24.00' : null,
  betrag: preisBekannt ? '24.00' : null,
  grund: preisBekannt ? null : 'EK_FEHLT',
  grund_text: preisBekannt ? null : 'Der Einkaufspreis des Artikels ist unbekannt.',
  vorschlaege: [],
});

describe('Nachtrag — die Rechnung aus den Abweichungen', () => {
  let fixture: ComponentFixture<Nachtrag>;
  let http: HttpTestingController;

  const el = () => fixture.nativeElement as HTMLElement;
  const text = () => (el().textContent ?? '').replace(/\s+/g, ' ');
  const tabelle = () => (el().querySelector('.nt__tab')?.textContent ?? '').replace(/\s+/g, ' ');

  const antworten = (v: NachtragVorschau) => {
    const req = http.expectOne('/api/workflow/work_orders/wo-1/nachtrag');
    req.flush(v);
    fixture.detectChanges();
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Nachtrag],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        // Der Erfolgsfall springt in den erzeugten Rechnungsentwurf — die Route
        // muss es geben, sonst scheitert die Navigation still im Hintergrund.
        provideRouter([{ path: '**', children: [] }]),
        { provide: AuthService, useValue: { darf: () => true, darfAlle: () => true } },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(Nachtrag);
    http = TestBed.inject(HttpTestingController);
    fixture.componentRef.setInput('auftrag', auftrag);
  });

  afterEach(() => http.verify());

  it('rechnet nur die Differenz ab — 19 statt 18 heißt 1 Stück, nicht 19', () => {
    fixture.detectChanges();
    antworten(vorschau({ positionen: [mehrverbrauch()], summe: '24.00' }));

    const t = tabelle();
    expect(t).toContain('18');           // Soll
    expect(t).toContain('19');           // Ist
    expect(t).toContain('1 stk');        // abzurechnen: die DIFFERENZ, nicht 19
    expect(t).not.toContain('19 stk');   // die Ist-Menge wird NICHT fakturiert
    expect(t).toContain('24,00 €');
  });

  it('nennt die Art im Klartext (nie nur über die Farbe)', () => {
    fixture.detectChanges();
    antworten(vorschau({ positionen: [mehrverbrauch()], summe: '24.00' }));
    expect(tabelle()).toContain('Mehrverbrauch');
  });

  it('ohne Abweichung: kein toter Knopf, sondern die Begründung', () => {
    fixture.detectChanges();
    antworten(vorschau());

    expect(text()).toContain('nichts nachzutragen');
    // Und der Grund, warum Minderverbrauch hier nicht auftaucht.
    expect(text()).toContain('mindern die Pauschale');
    expect(el().querySelector('.nt__lauf')).toBeNull();
  });

  it('alles schon fakturiert: sagt es — mit Belegnummer', () => {
    fixture.detectChanges();
    antworten(
      vorschau({
        bereits_abgerechnet: [
          {
            schluessel: 'ARTIKEL:a-1:stk',
            art: 'MEHRVERBRAUCH',
            bezeichnung: 'Thermostatventil',
            einheit: 'stk',
            menge: '1.000',
            rechnungen: ['RE-00007'],
          },
        ],
      }),
    );

    expect(text()).toContain('bereits abgerechnet');
    expect(text()).toContain('RE-00007');
    expect(el().querySelector('.nt__lauf')).toBeNull();
  });

  it('unbekannter Preis bleibt unbekannt — nie 0,00 €', () => {
    fixture.detectChanges();
    antworten(vorschau({ positionen: [mehrverbrauch(false)], summe: '0.00' , preise_unbekannt: true }));

    expect(tabelle()).toContain('Preis unbekannt');
    expect(tabelle()).not.toContain('0,00 € 0,00 €');
    // Die Summe sagt selbst, dass sie unvollständig ist.
    expect(text()).toContain('der bepreisbaren Positionen');
    expect(text()).toContain('nicht mit 0,00 € abgerechnet');
  });

  it('divergente Einheit: fail-closed Banner statt Beleg, kein Abrechnen-Knopf', () => {
    fixture.detectChanges();
    antworten(
      vorschau({
        einheit_konflikte: [
          { schluessel: 'ARTIKEL:a-1:stk', bezeichnung: 'Thermostatventil', einheiten: ['stk', 'stück'] },
        ],
      }),
    );

    expect(text()).toContain('Einheiten uneindeutig');
    expect(text()).toContain('stk · stück');
    // Kein Abrechnen-Formular, wenn nur ein Konflikt ansteht (nichts Billbares).
    expect(el().querySelector('.nt__lauf')).toBeNull();
  });

  it('REGIE: kein Nachtrag — und die Erklärung, warum nicht', () => {
    fixture.detectChanges();
    antworten(
      vorschau({
        billing_mode: 'REGIE',
        abrechenbar: false,
        hinweis: 'Regieabrechnung: Das gesamte Ist wird fakturiert.',
      }),
    );

    expect(text()).toContain('Regie');
    expect(el().querySelector('.nt__lauf')).toBeNull();
  });

  it('schickt den Steuersatz mit — er wird nie geraten', () => {
    fixture.detectChanges();
    antworten(vorschau({ positionen: [mehrverbrauch()], summe: '24.00' }));

    const komponente = fixture.componentInstance as unknown as {
      form: { controls: { tax_code: { setValue: (v: string) => void } } };
      abrechnen: () => void;
    };
    komponente.form.controls.tax_code.setValue('DE_19');
    komponente.abrechnen();

    const req = http.expectOne('/api/invoicing/invoices/aus-nachtrag');
    expect(req.request.body).toEqual({
      work_order_id: 'wo-1',
      tax_code: 'DE_19',
      preise: {},
    });
    req.flush({ id: 're-1' });
  });
});
