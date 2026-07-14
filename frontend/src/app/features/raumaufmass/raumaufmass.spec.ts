import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { AuthService } from '../../core/auth.service';
import { Aufmass, Room, RoomStatus } from '../../core/raum.model';
import { Raumaufmass } from './raumaufmass';

/**
 * **Gelöscht wird nie.** Ein Raum, der umgebaut wurde oder weggefallen ist (zwei
 * Zimmer zusammengelegt), wird STILLGELEGT: er bleibt als Nachweis über den
 * Bestand lesbar, zählt aber in keiner Summe mehr mit.
 *
 * Zwei Dinge prüft dieser Test scharf:
 *  1. Der Schalter „stillgelegte anzeigen" ist **kein Client-Filter**, sondern
 *     eine andere Serverabfrage (`?mit_inaktiven=true`).
 *  2. Ein stillgelegter Raum wird **nicht mitgezählt**, auch wenn er in der
 *     Liste steht — sonst widerspräche die Liste der Aufmaß-Summe des Servers.
 */
const raum = (id: string, name: string, status: RoomStatus, flaeche: string): Room => ({
  id,
  building_id: null,
  unit_id: null,
  storey: 'EG',
  name,
  room_type: 'WOHNEN',
  floor_area_m2: flaeche,
  length_m: null,
  width_m: null,
  room_height_m: '2.500',
  perimeter_m: null,
  volume_m3: null,
  indoor_temp_c: null,
  air_change_rate: null,
  heat_load_w_per_m2: null,
  riser_distance_m: null,
  status,
  note: null,
  surfaces: [],
  openings: [],
  vertices: [],
  kennzahlen: {
    geometrie_quelle: 'EINGEGEBEN',
    floor_area_m2: flaeche,
    volume_m3: null,
    perimeter_m: null,
    wall_area_gross_m2: null,
    opening_area_m2: null,
    wall_area_net_m2: null,
    heizlast_kennwert_w: null,
    transmission_w: null,
    lueftung_w: null,
    heizlast_huellflaeche_w: null,
    unbekannt_grund: 'Die Außentemperatur fehlt.',
    hinweise: [],
  },
});

const AUFMASS: Aufmass = {
  design_outdoor_temp_c: '-12',
  heat_load_w_per_m2: null,
  raeume_anzahl: 1,
  flaeche_m2: '20.000',
  volumen_m3: null,
  umfang_m: null,
  heizlast_kennwert_w: null,
  heizlast_huellflaeche_w: null,
  unbekannt_raeume: [],
  leitungslaenge_schaetzung_m: null,
  raeume_ohne_steigleitung: 0,
  hinweise: [],
};

describe('Raumaufmass — Raum stilllegen', () => {
  let fixture: ComponentFixture<Raumaufmass>;
  let http: HttpTestingController;

  const el = () => fixture.nativeElement as HTMLElement;
  const text = () => (el().textContent ?? '').replace(/\s+/g, ' ');
  const knopf = (label: string) =>
    Array.from(el().querySelectorAll('button')).find((b) =>
      (b.textContent ?? '').includes(label),
    ) as HTMLButtonElement | undefined;

  /** Räume + Aufmaß beantworten; liefert die Räume-Anfrage zurück (für die Params). */
  const antworten = (raeume: Room[]) => {
    const req = http.expectOne((r) => r.url === '/api/property/properties/p-1/rooms');
    req.flush(raeume);
    http.expectOne('/api/property/properties/p-1/aufmass').flush(AUFMASS);
    fixture.detectChanges();
    return req;
  };

  /** „Stillgelegte Räume anzeigen" umlegen. */
  const umschalten = () => {
    const schalter = el().querySelector<HTMLInputElement>('.ra__schalter-box')!;
    schalter.checked = !schalter.checked;
    schalter.dispatchEvent(new Event('change'));
    fixture.detectChanges();
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Raumaufmass],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: AuthService, useValue: { darf: () => true, darfAlle: () => true } },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(Raumaufmass);
    http = TestBed.inject(HttpTestingController);
    fixture.componentRef.setInput('propertyId', 'p-1');
  });

  afterEach(() => http.verify());

  it('lädt standardmäßig NUR die aktiven Räume (kein mit_inaktiven)', () => {
    fixture.detectChanges();
    const req = antworten([raum('r-1', 'Wohnzimmer', 'AKTIV', '20.000')]);
    expect(req.request.params.has('mit_inaktiven')).toBe(false);
  });

  it('der Schalter fragt den Server neu (mit_inaktiven=true) und zählt sie NICHT mit', () => {
    fixture.detectChanges();
    antworten([raum('r-1', 'Wohnzimmer', 'AKTIV', '20.000')]);

    umschalten();

    const req = antworten([
      raum('r-1', 'Wohnzimmer', 'AKTIV', '20.000'),
      raum('r-2', 'Abstellkammer', 'INAKTIV', '5.000'),
    ]);
    expect(req.request.params.get('mit_inaktiven')).toBe('true');

    // Er steht in der Liste (Nachweis über den Bestand) …
    expect(text()).toContain('Abstellkammer');
    expect(text()).toContain('stillgelegt');
    // … zählt aber in keiner Summe mit.
    const gesamt = (el().querySelector('.ra__gesamt')?.textContent ?? '').replace(/\s+/g, ' ');
    expect(gesamt).toContain('1 Räume'); // nur der aktive
    expect(gesamt).toContain('20,00 m²'); // die 5 m² des stillgelegten sind NICHT drin
    expect(text()).toContain('in keiner Summe enthalten');
  });

  it('legt einen Raum erst nach Bestätigung still (PATCH status=INAKTIV)', () => {
    fixture.detectChanges();
    antworten([raum('r-1', 'Wohnzimmer', 'AKTIV', '20.000')]);

    knopf('Stilllegen')!.click();
    fixture.detectChanges();
    expect(text()).toContain('Raum stilllegen?');
    // Vor der Bestätigung darf nichts geschickt worden sein.
    http.expectNone((r) => r.method === 'PATCH');

    // Der Bestätigen-Knopf des Dialogs trägt dasselbe Wort — der letzte gewinnt.
    const dialogKnopf = Array.from(el().querySelectorAll('button')).filter((b) =>
      (b.textContent ?? '').includes('Stilllegen'),
    );
    dialogKnopf[dialogKnopf.length - 1].click();
    fixture.detectChanges();

    const req = http.expectOne('/api/property/rooms/r-1');
    expect(req.request.method).toBe('PATCH');
    expect(req.request.body).toEqual({ status: 'INAKTIV' });
    req.flush(raum('r-1', 'Wohnzimmer', 'INAKTIV', '20.000'));

    // Danach holt die Liste den Stand frisch vom Server (er entscheidet über die
    // Sichtbarkeit und die Summen, nicht der Client).
    antworten([]);
    expect(text()).toContain('kein Raum erfasst');
  });

  it('reaktiviert einen stillgelegten Raum ohne Rückfrage (PATCH status=AKTIV)', () => {
    fixture.detectChanges();
    antworten([]); // kein aktiver Raum — der Schalter muss trotzdem erreichbar sein
    umschalten();
    antworten([raum('r-2', 'Abstellkammer', 'INAKTIV', '5.000')]);

    knopf('Wieder aktivieren')!.click();
    fixture.detectChanges();

    const req = http.expectOne('/api/property/rooms/r-2');
    expect(req.request.body).toEqual({ status: 'AKTIV' });
    req.flush(raum('r-2', 'Abstellkammer', 'AKTIV', '5.000'));

    antworten([raum('r-2', 'Abstellkammer', 'AKTIV', '5.000')]);
  });
});
