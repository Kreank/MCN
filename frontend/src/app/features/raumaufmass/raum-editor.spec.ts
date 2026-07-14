import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Bauteil } from '../../core/bauteilkatalog.model';
import { AufbauIn, Room } from '../../core/raum.model';
import { RaumEditor } from './raum-editor';

/**
 * Der Editor darf über den Aufbau NICHT lügen. Die Wandzuordnung einer Öffnung
 * entscheidet darüber, ob das Fenster in die Transmission zählt — steht dort
 * fälschlich „keiner Fläche zugeordnet", „korrigiert" der Bediener eine
 * richtige Zuordnung kaputt.
 */
const raum = (): Room => ({
  id: 'r-1',
  building_id: null,
  unit_id: null,
  storey: 'EG',
  name: 'Wohnzimmer',
  room_type: 'WOHNEN',
  floor_area_m2: '24.000',
  length_m: null,
  width_m: null,
  room_height_m: '2.500',
  perimeter_m: null,
  volume_m3: '60.000',
  indoor_temp_c: '20.0',
  air_change_rate: '0.5',
  heat_load_w_per_m2: null,
  riser_distance_m: null,
  status: 'AKTIV',
  note: null,
  surfaces: [
    {
      id: 's-1',
      surface_type: 'AUSSENWAND',
      adjacent: 'AUSSENLUFT',
      orientation: 'N',
      label: 'Außenwand Nord',
      gross_area_m2: '20.000',
      u_value: '0.240',
      temp_factor: null,
      net_area_m2: '18.000',
      edge_index: null,
      edge_length_m: null,
      area_is_derived: false,
    },
  ],
  openings: [
    {
      id: 'o-1',
      surface_id: 's-1',
      opening_type: 'FENSTER',
      label: 'Fenster Nord',
      quantity: 1,
      width_m: '2.000',
      height_m: '1.000',
      u_value: '1.300',
      area_m2: '2.000',
      position_m: null,
    },
  ],
  vertices: [],
  kennzahlen: {
    geometrie_quelle: 'EINGEGEBEN',
    floor_area_m2: '24.000',
    volume_m3: '60.000',
    perimeter_m: null,
    wall_area_gross_m2: '20.000',
    opening_area_m2: '2.000',
    wall_area_net_m2: '18.000',
    heizlast_kennwert_w: null,
    transmission_w: null,
    lueftung_w: null,
    heizlast_huellflaeche_w: null,
    unbekannt_grund: 'Die Außentemperatur (Auslegungstemperatur) fehlt.',
    hinweise: [],
  },
});

/** Vorlagen des Bauteilkatalogs — eine MIT und eine OHNE U-Wert. */
const WAND: Bauteil = {
  id: 't-wand',
  kind: 'FLAECHE',
  name: 'Außenwand, Ziegel ungedämmt',
  default_surface_type: 'AUSSENWAND',
  default_opening_type: null,
  u_value: '1.400',
  note: null,
  status: 'AKTIV',
  sort_index: 1,
};
/** Auslieferungszustand des Katalogs: Vorlage ohne U-Wert. Kein Fehler. */
const WAND_OHNE_WERT: Bauteil = {
  ...WAND,
  id: 't-wand-2',
  name: 'Außenwand, gedämmt',
  u_value: null,
};
const FENSTER: Bauteil = {
  id: 't-fenster',
  kind: 'OEFFNUNG',
  name: 'Fenster, Doppelkastenfenster',
  default_surface_type: null,
  default_opening_type: 'FENSTER',
  u_value: '2.700',
  note: null,
  status: 'AKTIV',
  sort_index: 1,
};

describe('RaumEditor', () => {
  let fixture: ComponentFixture<RaumEditor>;
  let http: HttpTestingController;

  const el = () => fixture.nativeElement as HTMLElement;
  const select = (praefix: string): HTMLSelectElement =>
    el().querySelector<HTMLSelectElement>(`select[id^="${praefix}"]`)!;
  const eingabeZu = (labelText: string): HTMLInputElement => {
    const label = Array.from(el().querySelectorAll('label')).find((l) =>
      (l.textContent ?? '').includes(labelText),
    )!;
    const fuer = label.getAttribute('for');
    return fuer
      ? el().querySelector<HTMLInputElement>(`#${fuer}`)!
      : label.querySelector<HTMLInputElement>('input')!;
  };
  const tippen = (input: HTMLInputElement, wert: string) => {
    input.value = wert;
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
  };
  const waehlen = (select: HTMLSelectElement, wert: string) => {
    select.value = wert;
    select.dispatchEvent(new Event('change'));
    fixture.detectChanges();
  };
  const text = (sel: string) => (el().querySelector(sel)?.textContent ?? '').replace(/\s+/g, ' ');

  /** Der Editor holt beim Aufbau den Bauteilkatalog (je Gattung eine Anfrage). */
  const katalogFlush = (flaechen: Bauteil[] = [WAND, WAND_OHNE_WERT], oeffnungen = [FENSTER]) => {
    const req = (kind: string) =>
      http.expectOne(
        (r) => r.url.endsWith('/api/property/component-templates') && r.params.get('kind') === kind,
      );
    req('FLAECHE').flush(flaechen);
    req('OEFFNUNG').flush(oeffnungen);
    fixture.detectChanges();
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [RaumEditor],
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    }).compileComponents();

    fixture = TestBed.createComponent(RaumEditor);
    http = TestBed.inject(HttpTestingController);
    fixture.componentRef.setInput('propertyId', 'p-1');
    fixture.componentRef.setInput('darfAendern', true);
    // Der Katalog wird beim Anlegen der Komponente geholt — sonst bliebe die
    // Anfrage offen und `http.verify()` schlüge in jedem Test an.
    katalogFlush();
  });

  afterEach(() => http.verify());

  it('zeigt die gespeicherte Wandzuordnung einer Öffnung — nicht „keiner Fläche"', () => {
    fixture.componentRef.setInput('raum', raum());
    fixture.detectChanges();

    const wand = select('of-wand-');
    const gewaehlt = wand.selectedOptions[0];
    expect(wand.value).not.toBe(''); // der alte Bug: stiller Rückfall auf ""
    expect(gewaehlt.textContent).toContain('Außenwand Nord');
    // Die Option zeigt auf genau die Hüllfläche, die im Editor steht.
    expect(wand.value).toBe(select('hf-art-').id.replace('hf-art-', ''));
  });

  it('zeigt auch die übrigen Auswahlen des Aufbaus richtig vor', () => {
    fixture.componentRef.setInput('raum', raum());
    fixture.detectChanges();

    expect(select('hf-art-').value).toBe('AUSSENWAND');
    expect(select('hf-nachbar-').value).toBe('AUSSENLUFT');
    expect(select('hf-richtung-').value).toBe('N');
    expect(select('of-art-').value).toBe('FENSTER');
  });

  it('zeigt beim neuen Raum Fläche UND Volumen live — nicht „—" neben einer Zahl', () => {
    fixture.componentRef.setInput('raum', null);
    fixture.detectChanges();

    tippen(eingabeZu('Fläche (m²)'), '20');
    tippen(eingabeZu('Höhe (m)'), '2,5');

    const werte = Array.from(el().querySelectorAll('.kz__wert')).map((w) =>
      (w.textContent ?? '').replace(/\s+/g, ' ').trim(),
    );
    expect(werte[0]).toContain('20,00 m²'); // Fläche
    expect(werte[1]).toContain('50,00 m³'); // Volumen
  });

  it('ohne Maße bleiben Fläche und Volumen beide leer („—")', () => {
    fixture.componentRef.setInput('raum', null);
    fixture.detectChanges();

    const werte = Array.from(el().querySelectorAll('.kz__wert')).map((w) =>
      (w.textContent ?? '').replace(/\s+/g, ' ').trim(),
    );
    expect(werte[0]).toContain('—');
    expect(werte[1]).toContain('—');
  });

  it('jedes Eingabefeld des Aufbaus trägt eine id (Formularfeld-Semantik)', () => {
    fixture.componentRef.setInput('raum', raum());
    fixture.detectChanges();

    const felder = Array.from(
      el().querySelectorAll<HTMLElement>('.hf input, .hf select, .of input, .of select'),
    );
    expect(felder.length).toBeGreaterThan(0);
    expect(felder.every((f) => f.id !== '')).toBe(true);
  });

  // ----------------------------------------------------- Bauteilkatalog ---
  // Die Vorlage belegt den U-Wert VOR und der Wert ist eine KOPIE: er bleibt
  // überschreibbar (gemessen schlägt Vorlage), und der Bediener muss sehen,
  // woher er kommt.
  describe('Bauteil aus dem Katalog', () => {
    const uFeld = (praefix: string) =>
      el().querySelector<HTMLInputElement>(`input[id^="${praefix}"]`)!;

    it('belegt den U-Wert der Hüllfläche aus der Vorlage vor — und die Bauteilart', () => {
      fixture.componentRef.setInput('raum', raum());
      fixture.detectChanges();

      // Die geladene Wand hat U 0,24 und keine Vorlage.
      expect(uFeld('hf-u-').value).toBe('0,24');
      expect(select('hf-vorlage-').value).toBe('');

      waehlen(select('hf-vorlage-'), WAND.id);

      expect(uFeld('hf-u-').value).toBe('1,4'); // aus dem Katalog kopiert
      expect(select('hf-art-').value).toBe('AUSSENWAND'); // Vorbelegung der Art
      expect(text('.hf')).toContain('aus Vorlage');
    });

    it('erkennt einen abweichend eingegebenen U-Wert (gemessen schlägt Vorlage)', () => {
      fixture.componentRef.setInput('raum', raum());
      fixture.detectChanges();

      waehlen(select('hf-vorlage-'), WAND.id);
      expect(text('.hf')).toContain('aus Vorlage');

      tippen(uFeld('hf-u-'), '0,9');

      const hf = text('.hf');
      expect(hf).toContain('abweichend');
      expect(hf).not.toContain('aus Vorlage');
      expect(hf).toContain('1,4'); // der Katalogwert steht als Vergleich daneben
    });

    it('sagt bei einer Vorlage OHNE U-Wert, dass die Heizlast unbekannt bleibt', () => {
      fixture.componentRef.setInput('raum', raum());
      fixture.detectChanges();

      // Feld leeren, dann die Vorlage ohne Katalogwert wählen.
      tippen(uFeld('hf-u-'), '');
      waehlen(select('hf-vorlage-'), WAND_OHNE_WERT.id);

      const hf = text('.hf');
      expect(hf).toContain('U-Wert im Katalog nicht hinterlegt');
      expect(hf).toContain('Heizlast bleibt unbekannt');
      expect(uFeld('hf-u-').value).toBe(''); // kein geratener Wert, keine 0
    });

    it('wirft einen getippten U-Wert nicht weg, wenn die Vorlage keinen hat', () => {
      fixture.componentRef.setInput('raum', raum());
      fixture.detectChanges();

      waehlen(select('hf-vorlage-'), WAND_OHNE_WERT.id);

      expect(uFeld('hf-u-').value).toBe('0,24'); // der erfasste Wert bleibt stehen
      expect(text('.hf')).toContain('eigener Wert');
    });

    it('belegt auch die Öffnung aus dem Katalog vor', () => {
      fixture.componentRef.setInput('raum', raum());
      fixture.detectChanges();

      waehlen(select('of-vorlage-'), FENSTER.id);

      expect(uFeld('of-u-').value).toBe('2,7');
      expect(select('of-art-').value).toBe('FENSTER');
    });

    it('speichert die Herkunft (template_id) mit dem Aufbau', () => {
      fixture.componentRef.setInput('raum', raum());
      fixture.detectChanges();

      waehlen(select('hf-vorlage-'), WAND.id);
      waehlen(select('of-vorlage-'), FENSTER.id);

      const speichern = Array.from(el().querySelectorAll('button')).find((b) =>
        (b.textContent ?? '').includes('Aufbau speichern'),
      )!;
      speichern.click();

      const req = http.expectOne('/api/property/rooms/r-1/aufbau');
      const body = req.request.body as AufbauIn;
      expect(body.surfaces[0].template_id).toBe(WAND.id);
      expect(body.surfaces[0].u_value).toBe('1.4'); // die KOPIE, nicht ein Verweis
      expect(body.openings[0].template_id).toBe(FENSTER.id);
      req.flush(raum());
    });
  });

  /**
   * **Die Wandfläche einer Kantenwand gehört dem SERVER** (Migration 0093).
   *
   * Schickt der Client `gross_area_m2` mit, ist die Wand ab sofort Handeingabe
   * (`area_is_derived = false`) und wird **nie wieder** nachgerechnet. Korrigiert
   * dann jemand die Raumhöhe von 2,50 auf 2,80 m, bleibt die Wandfläche auf 2,50
   * stehen — und die Heizlast ist still falsch. Genau dagegen ist 0093 gebaut.
   *
   * Deshalb gilt: **abgeleitet = weglassen.** Anzeigen darf der Client sie, senden
   * nicht.
   */
  describe('Wandfläche: berechnet vs. abweichend (0093)', () => {
    /** Raum mit Umriss (5 m × 4 m) und einer Wand auf Kante 0, vom Server berechnet. */
    const gezeichnet = (): Room => ({
      ...raum(),
      room_height_m: '2.500',
      vertices: [
        { idx: 0, x_mm: 0, y_mm: 0 },
        { idx: 1, x_mm: 5000, y_mm: 0 },
        { idx: 2, x_mm: 5000, y_mm: 4000 },
        { idx: 3, x_mm: 0, y_mm: 4000 },
      ],
      surfaces: [
        {
          id: 's-1',
          surface_type: 'AUSSENWAND',
          adjacent: 'AUSSENLUFT',
          orientation: 'S',
          label: 'Außenwand Süd',
          gross_area_m2: '12.500', // 5,00 m × 2,50 m — der Server hat sie gerechnet
          u_value: '0.240',
          temp_factor: null,
          net_area_m2: '12.500',
          edge_index: 0,
          edge_length_m: '5.000',
          area_is_derived: true,
        },
      ],
      openings: [],
      kennzahlen: { ...raum().kennzahlen, geometrie_quelle: 'GEZEICHNET' },
    });

    const aufbauSpeichern = () => {
      Array.from(el().querySelectorAll('button'))
        .find((b) => (b.textContent ?? '').includes('Aufbau speichern'))!
        .click();
      return http.expectOne('/api/property/rooms/r-1/aufbau');
    };

    // (a)
    it('schickt für eine BERECHNETE Kantenwand KEIN gross_area_m2', () => {
      fixture.componentRef.setInput('raum', gezeichnet());
      fixture.detectChanges();

      const req = aufbauSpeichern();
      const body = req.request.body as AufbauIn;
      expect(body.surfaces[0].edge_index).toBe(0);
      // Der springende Punkt: das Feld ist gar nicht erst im Payload.
      expect('gross_area_m2' in body.surfaces[0]).toBe(false);
      req.flush(gezeichnet());
    });

    it('zeigt die berechnete Fläche trotzdem an (anzeigen ist nicht behaupten)', () => {
      fixture.componentRef.setInput('raum', gezeichnet());
      fixture.detectChanges();
      expect(text('.hf')).toContain('berechnet');
      expect(text('.hf')).toContain('12,50 m²'); // 5,00 m × 2,50 m
    });

    // (b)
    it('schickt eine ABWEICHEND eingetragene Fläche mit (Giebel, Erker)', () => {
      fixture.componentRef.setInput('raum', gezeichnet());
      fixture.detectChanges();

      // „Fläche abweichend eintragen" macht aus dem Ergebnis ein Feld …
      Array.from(el().querySelectorAll('button'))
        .find((b) => (b.textContent ?? '').includes('Fläche abweichend eintragen'))!
        .click();
      fixture.detectChanges();
      tippen(eingabeZu('Fläche brutto'), '15,25'); // … und der Nutzer trägt seinen Giebel ein

      const body = aufbauSpeichern().request.body as AufbauIn;
      expect(body.surfaces[0].gross_area_m2).toBe('15.25');
      expect(body.surfaces[0].edge_index).toBe(0);
    });

    // (c)
    it('„zurück auf berechnet" lässt sie wieder weg', () => {
      fixture.componentRef.setInput('raum', gezeichnet());
      fixture.detectChanges();

      Array.from(el().querySelectorAll('button'))
        .find((b) => (b.textContent ?? '').includes('Fläche abweichend eintragen'))!
        .click();
      fixture.detectChanges();
      tippen(eingabeZu('Fläche brutto'), '15,25');

      Array.from(el().querySelectorAll('button'))
        .find((b) => (b.textContent ?? '').includes('zurück auf berechnet'))!
        .click();
      fixture.detectChanges();

      const body = aufbauSpeichern().request.body as AufbauIn;
      expect('gross_area_m2' in body.surfaces[0]).toBe(false);
    });

    it('OHNE Kante bleibt die Fläche Pflicht — da gibt es nichts abzuleiten', () => {
      const decke = (): Room => ({
        ...gezeichnet(),
        surfaces: [
          {
            ...gezeichnet().surfaces[0],
            surface_type: 'DECKE',
            edge_index: null,
            edge_length_m: null,
            area_is_derived: false,
            gross_area_m2: '',
          },
        ],
      });
      fixture.componentRef.setInput('raum', decke());
      fixture.detectChanges();

      Array.from(el().querySelectorAll('button'))
        .find((b) => (b.textContent ?? '').includes('Aufbau speichern'))!
        .click();
      fixture.detectChanges();

      http.expectNone('/api/property/rooms/r-1/aufbau'); // gar nicht erst abgeschickt
      expect(text('.melde--fehler')).toContain('Bruttofläche ist erforderlich');
    });
  });
});
