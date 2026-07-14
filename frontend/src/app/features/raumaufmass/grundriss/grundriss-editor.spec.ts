import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { GrundrissIn, Room } from '../../../core/raum.model';
import { Huelle, Oeffnung } from '../aufbau-modell';
import { GrundrissEditor } from './grundriss-editor';

/**
 * Der Zeichner darf über den Umriss NICHT lügen. Zwei Dinge prüft dieser Test
 * scharf:
 *
 *  1. **Die Koordinaten sind ganze Millimeter.** Was hier hinausgeht, ist
 *     `integer` — kein Gleitkomma, das der Server erst runden müsste.
 *  2. **Eine Öffnung ohne Lage wird NICHT bei 0 platziert.** Sie zählt in Fläche
 *     und Heizlast, sie steht in der Liste „ohne Lage in der Wand" — aber sie
 *     wird nicht gezeichnet, und es wird keine Lage erfunden.
 */
const raum = (vertices: { idx: number; x_mm: number; y_mm: number }[] = []): Room => ({
  id: 'r-1',
  building_id: null,
  unit_id: null,
  storey: 'EG',
  name: 'Wohnzimmer',
  room_type: 'WOHNEN',
  floor_area_m2: '20.000',
  length_m: null,
  width_m: null,
  room_height_m: '2.500',
  perimeter_m: null,
  volume_m3: null,
  indoor_temp_c: null,
  air_change_rate: null,
  heat_load_w_per_m2: null,
  riser_distance_m: null,
  status: 'AKTIV',
  note: null,
  surfaces: [],
  openings: [],
  vertices,
  kennzahlen: {
    geometrie_quelle: vertices.length ? 'GEZEICHNET' : 'EINGEGEBEN',
    floor_area_m2: '20.000',
    volume_m3: null,
    perimeter_m: null,
    wall_area_gross_m2: null,
    opening_area_m2: null,
    wall_area_net_m2: null,
    heizlast_kennwert_w: null,
    transmission_w: null,
    lueftung_w: null,
    heizlast_huellflaeche_w: null,
    unbekannt_grund: null,
    hinweise: [],
  },
});

/** 5 m × 4 m — als gespeicherter Umriss. */
const RECHTECK = [
  { idx: 0, x_mm: 0, y_mm: 0 },
  { idx: 1, x_mm: 5000, y_mm: 0 },
  { idx: 2, x_mm: 5000, y_mm: 4000 },
  { idx: 3, x_mm: 0, y_mm: 4000 },
];

/** Kantenwand — `brutto` LEER heißt: der Server rechnet sie (Migration 0093). */
const wand = (uid: string, edge_index: number | null, brutto = ''): Huelle => ({
  uid,
  surface_type: 'AUSSENWAND',
  adjacent: 'AUSSENLUFT',
  orientation: 'S',
  label: 'Außenwand Süd',
  brutto,
  u_value: '',
  temp_factor: '',
  template_id: null,
  edge_index,
});

const fenster = (uid: string, surfaceRef: string, position: string): Oeffnung => ({
  uid,
  surfaceRef,
  opening_type: 'FENSTER',
  label: 'Fenster Süd',
  anzahl: '1',
  breite: '1,2',
  hoehe: '1,4',
  u_value: '',
  template_id: null,
  position,
});

describe('GrundrissEditor', () => {
  let fixture: ComponentFixture<GrundrissEditor>;
  let http: HttpTestingController;

  const bauen = (r: Room, huellen: Huelle[] = [], oeffnungen: Oeffnung[] = []) => {
    fixture = TestBed.createComponent(GrundrissEditor);
    fixture.componentRef.setInput('raum', r);
    fixture.componentRef.setInput('huellen', huellen);
    fixture.componentRef.setInput('oeffnungen', oeffnungen);
    fixture.componentRef.setInput('raumHoehe', '2,50');
    fixture.componentRef.setInput('darfAendern', true);
    fixture.detectChanges();
  };

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  /**
   * **Der Test, der das tote Werkzeug gefunden hätte.**
   *
   * Die Einzelteile waren alle grün — `zuWelt`, `snapPunkt`, `sichtEinpassen`,
   * `flaecheKlick`. Kaputt war die **Kette**: Nach dem ersten Klick passte der
   * Editor die Ansicht auf einen einzelnen Punkt ein, die Bounding-Box war 0 × 0,
   * die Skala entglitt auf 560 Einheiten je Millimeter — und jeder weitere Klick
   * snappte zurück auf Punkt 1: „Dort liegt bereits ein Punkt.", überall, für immer.
   *
   * Ein grüner Unit-Test, der an einem toten Werkzeug vorbeiläuft, ist schlimmer
   * als kein Test. Deshalb wird hier die echte Folge gefahren: klicken, klicken,
   * und die Weltkoordinaten nachrechnen.
   */
  describe('Zeichnen mit der Maus (die KETTE, nicht die Einzelteile)', () => {
    /** Die Zeichenfläche, wie der Browser sie ausmisst — jsdom tut das nicht. */
    const blatt = (): Element =>
      ({
        getBoundingClientRect: () => ({ left: 0, top: 0, width: 1000, height: 700 }),
      }) as unknown as Element;

    /** Ein Klick auf die Fläche, in Pixeln des gerenderten SVG. */
    const klick = (px: number, py: number) => {
      const c = fixture.componentInstance as unknown as {
        flaecheKlick: (ev: PointerEvent, svg: Element) => void;
      };
      c.flaecheKlick({ clientX: px, clientY: py } as PointerEvent, blatt());
      fixture.detectChanges();
    };

    const zustand = () =>
      fixture.componentInstance as unknown as {
        punkte: () => { x_mm: number; y_mm: number }[];
        sicht: () => { skala: number };
        fehler: () => string | null;
        zeichnet: () => boolean;
        einpassen: () => void;
      };

    it('zwei Klicks an ENTFERNTE Stellen ergeben ZWEI Punkte (nicht „liegt schon da")', () => {
      bauen(raum([])); // leerer Raum → Zeichnen ist an
      const c = zustand();
      expect(c.zeichnet()).toBe(true);

      // Standardfeld: 12 m breit, Skala 0,0666… Einheiten/mm, Mitte (0,0).
      klick(500, 350); // genau in die Mitte → Welt (0, 0)
      expect(c.punkte()).toEqual([{ x_mm: 0, y_mm: 0 }]);

      // GENAU HIER starb es vorher: die Ansicht wurde auf einen 0 × 0 großen
      // Kasten eingepasst, und der nächste Klick landete wieder auf Punkt 1.
      klick(900, 100); // deutlich entfernt → Welt (6000, 3750)
      expect(c.fehler()).toBeNull();
      expect(c.punkte()).toEqual([
        { x_mm: 0, y_mm: 0 },
        { x_mm: 6000, y_mm: 3750 },
      ]);
    });

    it('die Skala bleibt nach dem ersten Punkt brauchbar (das Raster bleibt sichtbar)', () => {
      bauen(raum([]));
      const c = zustand();
      const vorher = c.sicht().skala;
      klick(500, 350);
      // Vorher sprang sie hier von 0,0667 auf 560 — Faktor 8400.
      expect(c.sicht().skala).toBe(vorher);
      // Ein 25-cm-Raster muss auf dem Blatt noch eine sichtbare Weite haben.
      expect(250 * c.sicht().skala).toBeGreaterThan(4);
    });

    it('kollineare Zwischenstufe: der dritte Klick landet richtig', () => {
      bauen(raum([]));
      const c = zustand();

      // Zwei Punkte auf der x-Achse — die Bounding-Box ist in y GENAU 0 hoch.
      klick(500, 350); // (0, 0)
      klick(800, 350); // (4500, 0)
      expect(c.punkte()).toEqual([
        { x_mm: 0, y_mm: 0 },
        { x_mm: 4500, y_mm: 0 },
      ]);

      // „Ansicht einpassen" auf eine LINIE — hier entgleiste die y-Achse.
      c.einpassen();
      fixture.detectChanges();
      expect(Number.isFinite(c.sicht().skala)).toBe(true);
      expect(250 * c.sicht().skala).toBeGreaterThan(4);

      // Der dritte Klick muss neben der Linie landen — und zwar dort, wo geklickt wurde.
      const s = c.sicht().skala;
      const px = 500 + 1000 * s; // 1000 mm rechts der Mitte (2250, 0)
      const py = 350 - 2000 * s; // 2000 mm darüber
      klick(px, py);
      expect(c.fehler()).toBeNull();
      expect(c.punkte()[2]).toEqual({ x_mm: 3250, y_mm: 2000 });
      expect(c.punkte()).toHaveLength(3);
    });

    it('ein Klick GENAU auf einen bestehenden Punkt wird weiterhin abgelehnt', () => {
      bauen(raum([]));
      const c = zustand();
      klick(500, 350);
      klick(500, 350); // derselbe Ort — das wäre eine Kante ohne Länge
      expect(c.punkte()).toHaveLength(1);
      expect(c.fehler()).toContain('liegt bereits ein Punkt');
    });
  });

  it('übernimmt den gespeicherten Umriss (nach idx sortiert)', () => {
    bauen(raum([RECHTECK[2], RECHTECK[0], RECHTECK[3], RECHTECK[1]])); // absichtlich verdreht
    const c = fixture.componentInstance as unknown as {
      punkte: () => { x_mm: number; y_mm: number }[];
      flaeche: () => number;
      umfang: () => number;
    };
    expect(c.punkte()).toEqual([
      { x_mm: 0, y_mm: 0 },
      { x_mm: 5000, y_mm: 0 },
      { x_mm: 5000, y_mm: 4000 },
      { x_mm: 0, y_mm: 4000 },
    ]);
    expect(c.flaeche()).toBe(20);
    expect(c.umfang()).toBe(18);
  });

  it('schickt GANZE MILLIMETER an PUT /grundriss — kein Gleitkomma', () => {
    bauen(raum(RECHTECK));
    const c = fixture.componentInstance as unknown as {
      kanteTippen: (i: number, t: string) => void;
      kanteUebernehmen: (i: number) => void;
      speichern: () => void;
    };

    // Der Laser sagt 4,37 m — der Handwerker tippt es, statt zu ziehen.
    c.kanteTippen(0, '4,37');
    c.kanteUebernehmen(0);
    fixture.detectChanges();

    c.speichern();
    const req = http.expectOne('/api/property/rooms/r-1/grundriss');
    expect(req.request.method).toBe('PUT');
    const body = req.request.body as GrundrissIn;
    expect(body.vertices[1]).toEqual({ x_mm: 4370, y_mm: 0 });
    for (const v of body.vertices) {
      expect(Number.isInteger(v.x_mm)).toBe(true);
      expect(Number.isInteger(v.y_mm)).toBe(true);
    }
    req.flush(raum(RECHTECK));
  });

  it('lehnt eine MEHRDEUTIGE Kantenlänge ab, statt sie zu raten', () => {
    bauen(raum(RECHTECK));
    const c = fixture.componentInstance as unknown as {
      kanteTippen: (i: number, t: string) => void;
      kanteUebernehmen: (i: number) => void;
      punkte: () => unknown[];
      fehler: () => string | null;
    };
    const vorher = JSON.stringify(c.punkte());
    c.kanteTippen(0, '1.500'); // 1500 mm oder 1,5 m? Wir raten nicht.
    c.kanteUebernehmen(0);
    expect(c.fehler()).toContain('nicht eindeutig');
    expect(JSON.stringify(c.punkte())).toBe(vorher); // nichts verschoben
  });

  it('die SCHLIESSENDE Kante lässt sich nicht eintippen (sie ist abgeleitet)', () => {
    bauen(raum(RECHTECK));
    const c = fixture.componentInstance as unknown as {
      kanteTippen: (i: number, t: string) => void;
      kanteUebernehmen: (i: number) => void;
      fehler: () => string | null;
    };
    c.kanteTippen(3, '9');
    c.kanteUebernehmen(3);
    expect(c.fehler()).toContain('schließt den Umriss');
  });

  it('SPERRT das Speichern eines überschlagenen Umrisses (der Server würde 422 sagen)', () => {
    // Sanduhr: Punkt 2 und 3 vertauscht.
    bauen(
      raum([
        { idx: 0, x_mm: 0, y_mm: 0 },
        { idx: 1, x_mm: 5000, y_mm: 0 },
        { idx: 2, x_mm: 0, y_mm: 4000 },
        { idx: 3, x_mm: 5000, y_mm: 4000 },
      ]),
    );
    const c = fixture.componentInstance as unknown as {
      befunde: () => { art: string; text: string }[];
      speicherSperre: () => string | null;
      speichern: () => void;
    };
    expect(c.befunde().some((b) => b.art === 'SELBSTSCHNITT')).toBe(true);
    expect(c.speicherSperre()).toContain('Fehler');
    c.speichern();
    http.expectNone('/api/property/rooms/r-1/grundriss'); // gar nicht erst abgeschickt
  });

  it('zeichnet eine Öffnung OHNE Lage NICHT — und setzt sie NICHT auf 0', () => {
    const w = wand('w1', 0);
    const f = fenster('o1', 'w1', ''); // Lage nicht ausgemessen
    bauen(raum(RECHTECK), [w], [f]);
    const c = fixture.componentInstance as unknown as {
      oeffnungenSicht: () => unknown[];
      ohneLage: () => { uid: string }[];
    };
    expect(c.oeffnungenSicht()).toHaveLength(0); // nicht gezeichnet
    expect(c.ohneLage().map((o) => o.uid)).toEqual(['o1']); // aber sichtbar geführt
  });

  it('zeichnet eine Öffnung MIT Lage maßstäblich in ihre Kante', () => {
    bauen(raum(RECHTECK), [wand('w1', 0)], [fenster('o1', 'w1', '1,2')]);
    const c = fixture.componentInstance as unknown as {
      oeffnungenSicht: () => { uid: string }[];
      ohneLage: () => unknown[];
    };
    expect(c.oeffnungenSicht().map((o) => o.uid)).toEqual(['o1']);
    expect(c.ohneLage()).toHaveLength(0);
  });

  it('meldet eine Öffnung, die nicht in ihre Kante passt (statt sie stumm zu kappen)', () => {
    // Kante 1 ist 4 m lang; das Fenster säße bei 3,5 m und wäre 1,2 m breit.
    bauen(raum(RECHTECK), [wand('w1', 1)], [fenster('o1', 'w1', '3,5')]);
    const c = fixture.componentInstance as unknown as {
      zeilen: () => { oeffnungen: { passung: { art: string } }[] }[];
      oeffnungenSicht: () => unknown[];
    };
    expect(c.zeilen()[1].oeffnungen[0].passung.art).toBe('passt_nicht');
    expect(c.oeffnungenSicht()).toHaveLength(0); // nichts Falsches zeichnen
  });

  it('leitet die Wandfläche aus Kantenlänge × Raumhöhe ab (wie der Server)', () => {
    bauen(raum(RECHTECK), [], []);
    const c = fixture.componentInstance as unknown as {
      zeilen: () => { abgeleitetM2: number | null }[];
    };
    expect(c.zeilen()[0].abgeleitetM2).toBe(12.5); // 5,00 m × 2,50 m
    expect(c.zeilen()[1].abgeleitetM2).toBe(10); // 4,00 m × 2,50 m
  });

  it('eine Kantenwand OHNE eingetragene Fläche gilt als BERECHNET (der Server rechnet sie)', () => {
    bauen(raum(RECHTECK), [wand('w1', 0)], []);
    const c = fixture.componentInstance as unknown as {
      zeilen: () => { ueberschrieben: boolean; bruttoM2: number | null }[];
    };
    expect(c.zeilen()[0].ueberschrieben).toBe(false);
    // Angezeigt wird trotzdem der gerechnete Wert — anzeigen ist nicht behaupten.
    expect(c.zeilen()[0].bruttoM2).toBe(12.5);
  });

  it('erkennt eine abweichend eingetragene Wandfläche (Giebel/Erker) als solche', () => {
    bauen(raum(RECHTECK), [wand('w1', 0, '15')], []); // berechnet wären 12,5
    const c = fixture.componentInstance as unknown as {
      zeilen: () => { ueberschrieben: boolean; bruttoM2: number | null }[];
    };
    expect(c.zeilen()[0].ueberschrieben).toBe(true);
    expect(c.zeilen()[0].bruttoM2).toBe(15);
  });

  it('„zurück auf berechnet" LEERT das Feld (damit das Speichern nichts mehr schickt)', () => {
    bauen(raum(RECHTECK), [wand('w1', 0, '15')], []);
    const c = fixture.componentInstance as unknown as {
      wandAbleiten: (i: number) => void;
      huelleSetzen: { subscribe: (f: (e: unknown) => void) => void };
    };
    let patch: unknown = null;
    c.huelleSetzen.subscribe((e) => (patch = e));
    c.wandAbleiten(0);
    expect(patch).toEqual({ uid: 'w1', patch: { brutto: '' } });
  });

  it('„Wand zuordnen" legt sie BERECHNET an — ohne vorbelegte Fläche', () => {
    bauen(raum(RECHTECK), [], []);
    const c = fixture.componentInstance as unknown as {
      wandAnlegen: (i: number) => void;
      huelleAnKante: { subscribe: (f: (e: unknown) => void) => void };
    };
    let e: unknown = null;
    c.huelleAnKante.subscribe((x) => (e = x));
    c.wandAnlegen(0);
    // Kein `brutto` im Ereignis: eine vorbelegte Fläche machte die Wand zur
    // Handeingabe und die Raumhöhen-Korrektur ginge still an ihr vorbei.
    expect(e).toEqual({ edge_index: 0 });
  });

  it('sperrt die Kantenzuordnung, solange der Umriss nicht gespeichert ist', () => {
    bauen(raum(RECHTECK));
    const c = fixture.componentInstance as unknown as {
      kanteBelegbar: () => boolean;
      punktAnhaengen: () => void;
    };
    expect(c.kanteBelegbar()).toBe(true);
    c.punktAnhaengen(); // Arbeitsstand: der Server kennt die neue Kante noch nicht
    fixture.detectChanges();
    expect(c.kanteBelegbar()).toBe(false);
  });
});
