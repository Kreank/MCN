import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { AuthService } from '../../core/auth.service';
import { EinheitBelegung, Mieter, OccupancyType } from '../../core/belegung.model';
import { Belegung } from './belegung';

/**
 * Der Reiter „Belegung". Vier Dinge werden scharf geprüft — sie sind der Zweck
 * des Slices, nicht Dekor:
 *
 * 1. **Die Telefonnummer steht als Wähl-Link an der Wohnung.** Ohne sie kommt der
 *    Monteur nicht rein.
 * 2. **„Nicht erfasst" ≠ „Leerstand".** Zwei verschiedene Aussagen, und das UI
 *    darf sie nicht einebnen.
 * 3. **Gemeinschaftsfläche/Technikraum trägt keine Belegung** (F-12) — kein Knopf,
 *    statt den Nutzer in einen 422 laufen zu lassen.
 * 4. **Gelöscht wird nie:** „Ausgezogen" schickt ein POST auf `/beenden`, kein
 *    DELETE.
 */
const mieter = (
  id: string,
  name: string,
  telefon: string | null = '030 123456',
  is_current = true,
): Mieter => ({
  id,
  party_id: `party-${id}`,
  display_name: name,
  role: 'CONTRACTUAL_TENANT',
  valid_from: '2024-01-01',
  valid_until: is_current ? null : '2025-01-01',
  is_current,
  telefon,
  email: null,
});

const einheit = (
  unit_id: string,
  unit_number: string,
  opts: {
    unit_type?: string;
    belegbar?: boolean;
    occupancy_type?: OccupancyType;
    mieter?: Mieter[];
    ohneBelegung?: boolean;
  } = {},
): EinheitBelegung => ({
  unit_id,
  unit_number,
  unit_type: opts.unit_type ?? 'APARTMENT',
  belegbar: opts.belegbar ?? true,
  belegung: opts.ohneBelegung
    ? null
    : {
        id: `occ-${unit_id}`,
        unit_id,
        unit_number,
        unit_type: opts.unit_type ?? 'APARTMENT',
        occupancy_type: opts.occupancy_type ?? 'RENTED',
        contract_reference: null,
        valid_from: '2024-01-01',
        valid_until: null,
        is_current: true,
        mieter: opts.mieter ?? [],
      },
});

describe('Belegung — Mieter, Leerstand, Erreichbarkeit', () => {
  let fixture: ComponentFixture<Belegung>;
  let http: HttpTestingController;

  const el = () => fixture.nativeElement as HTMLElement;
  const kartenText = () =>
    Array.from(el().querySelectorAll('.karte'))
      .map((k) => k.textContent ?? '')
      .join(' ')
      .replace(/\s+/g, ' ');
  /**
   * Knöpfe der SEITE. Die Dialoge (Erfassen, Bestätigen) liegen **immer** im DOM;
   * ein Helfer, der sie mitzählt, findet Knöpfe, die gar nicht sichtbar sind —
   * und ein „darf nicht ändern"-Test würde falsch rot oder falsch grün.
   * (Dieselbe Falle steht im Kopf von `anlagen.spec.ts`.)
   */
  const knopf = (label: string) =>
    Array.from(el().querySelectorAll('button'))
      .filter((b) => !b.closest('dialog'))
      .find((b) => (b.textContent ?? '').includes(label)) as HTMLButtonElement | undefined;

  const dialogKnopf = (label: string) =>
    Array.from(el().querySelectorAll('dialog button')).find(
      (b) => (b.textContent ?? '').trim() === label,
    ) as HTMLButtonElement | undefined;

  const antworten = (liste: EinheitBelegung[]) => {
    const req = http.expectOne((r) => r.url === '/api/tenure/properties/p-1/belegung');
    req.flush(liste);
    fixture.detectChanges();
    return req;
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Belegung],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: AuthService, useValue: { darf: () => true, darfAlle: () => true } },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(Belegung);
    http = TestBed.inject(HttpTestingController);
    fixture.componentRef.setInput('propertyId', 'p-1');
  });

  afterEach(() => http.verify());

  it('lädt standardmäßig OHNE Historie', () => {
    fixture.detectChanges();
    const req = antworten([]);
    expect(req.request.params.has('historie')).toBe(false);
  });

  it('zeigt den Mieter mit Namen — er ist ein verlinkter Kontakt', () => {
    fixture.detectChanges();
    antworten([einheit('u-1', 'EG rechts', { mieter: [mieter('m-1', 'Robco')] })]);
    expect(kartenText()).toContain('Robco');
    const link = el().querySelector('.mieter__name') as HTMLAnchorElement;
    expect(link.getAttribute('href')).toContain('/kontakte/party-m-1');
  });

  it('DER KERN: die Telefonnummer steht als Wähl-Link an der Wohnung', () => {
    fixture.detectChanges();
    antworten([
      einheit('u-1', 'EG rechts', { mieter: [mieter('m-1', 'Robco', '0176 62147248')] }),
    ]);
    const tel = el().querySelector('.mieter__tel') as HTMLAnchorElement;
    expect(tel).toBeTruthy();
    expect(tel.textContent).toContain('0176 62147248');
    // Der Link muss wählbar sein — Leerzeichen stören manche Geräte.
    expect(tel.getAttribute('href')).toBe('tel:017662147248');
  });

  it('fehlende Telefonnummer wird AUSGESPROCHEN, nicht als leere Zelle versteckt', () => {
    fixture.detectChanges();
    antworten([einheit('u-1', 'EG rechts', { mieter: [mieter('m-1', 'Robco', null)] })]);
    expect(kartenText()).toContain('Keine Telefonnummer hinterlegt');
    expect(el().querySelector('.mieter__tel')).toBeNull();
  });

  it('„nicht erfasst" ist NICHT „leerstehend" — die Aussagen bleiben getrennt', () => {
    fixture.detectChanges();
    antworten([
      einheit('u-1', 'EG links', { ohneBelegung: true }),
      einheit('u-2', 'EG rechts', { occupancy_type: 'VACANT', mieter: [] }),
    ]);
    const text = kartenText();
    expect(text).toContain('Nicht erfasst');
    expect(text).toContain('Leerstand');
    // Und der Hinweis oben nennt die Zahl der unerfassten Einheiten beim Namen.
    expect((el().textContent ?? '').replace(/\s+/g, ' ')).toContain(
      'keine erfasste Belegung',
    );
  });

  it('Leerstand sagt, dass es eine Aussage ist — keine Lücke', () => {
    fixture.detectChanges();
    antworten([einheit('u-1', 'EG rechts', { occupancy_type: 'VACANT', mieter: [] })]);
    expect(kartenText()).toContain('Leerstand');
    expect(kartenText()).not.toContain('Der Monteur hat niemanden');
  });

  it('belegte Einheit ohne Bewohner: der Befund wird benannt', () => {
    fixture.detectChanges();
    antworten([einheit('u-1', 'EG rechts', { occupancy_type: 'RENTED', mieter: [] })]);
    expect(kartenText()).toContain('Der Monteur hat niemanden, den er anrufen kann');
  });

  it('F-12: Gemeinschaftsfläche trägt keine Belegung — und bietet keinen Knopf an', () => {
    fixture.detectChanges();
    antworten([
      einheit('u-1', 'Treppenhaus', {
        unit_type: 'COMMON_AREA',
        belegbar: false,
        ohneBelegung: true,
      }),
    ]);
    expect(kartenText()).toContain('Trägt keine Belegung');
    expect(knopf('Belegung erfassen')).toBeUndefined();
  });

  it('mehrere Bewohner sind der Normalfall (Ehepaar)', () => {
    fixture.detectChanges();
    antworten([
      einheit('u-1', '2. OG rechts', {
        mieter: [mieter('m-1', 'Kutzi'), mieter('m-2', 'Kutzi (Ehefrau)')],
      }),
    ]);
    expect(el().querySelectorAll('.mieter__zeile').length).toBe(2);
  });

  it('GELÖSCHT WIRD NIE: „Ausgezogen" schickt POST auf /beenden, kein DELETE', () => {
    fixture.detectChanges();
    antworten([einheit('u-1', 'EG rechts', { mieter: [mieter('m-1', 'Robco')] })]);

    knopf('Ausgezogen')!.click();
    fixture.detectChanges();
    // Vor der unumkehrbaren Aktion wird gefragt.
    const bestaetigen = dialogKnopf('Auszug eintragen');
    expect(bestaetigen).toBeTruthy();
    bestaetigen!.click();
    fixture.detectChanges();

    const req = http.expectOne('/api/tenure/mieter/m-1/beenden');
    expect(req.request.method).toBe('POST');
    expect(req.request.body.valid_until).toBeTruthy();
    req.flush({});
    fixture.detectChanges();
    antworten([]);
  });

  it('ohne AENDERN-Recht gibt es keinen Auszugs-Knopf (der Server tort ohnehin)', async () => {
    TestBed.resetTestingModule();
    await TestBed.configureTestingModule({
      imports: [Belegung],
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
    fixture = TestBed.createComponent(Belegung);
    http = TestBed.inject(HttpTestingController);
    fixture.componentRef.setInput('propertyId', 'p-1');
    fixture.detectChanges();
    antworten([einheit('u-1', 'EG rechts', { mieter: [mieter('m-1', 'Robco')] })]);

    // Lesen darf er (der Monteur!) — inklusive Nummer.
    expect(kartenText()).toContain('Robco');
    expect(el().querySelector('.mieter__tel')).toBeTruthy();
    // Ändern nicht.
    expect(knopf('Ausgezogen')).toBeUndefined();
    expect(knopf('Belegung ändern')).toBeUndefined();
    expect(knopf('Belegung erfassen')).toBeUndefined();
  });

  it('es gibt keinen Löschen-Knopf', () => {
    fixture.detectChanges();
    antworten([einheit('u-1', 'EG rechts', { mieter: [mieter('m-1', 'Robco')] })]);
    const alle = Array.from(el().querySelectorAll('button'))
      .map((b) => (b.textContent ?? '').toLowerCase())
      .join(' ');
    expect(alle).not.toContain('löschen');
  });
});
