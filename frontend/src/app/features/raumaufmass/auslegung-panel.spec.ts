import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { AuslegungPanel, auslegungPruefen } from './auslegung-panel';

/**
 * Die Auslegungsdaten sind der Grund, warum die raumweise Heizlast überhaupt
 * rechenbar ist. Zwei Dinge sind hier nicht verhandelbar:
 *
 *  1. **Leer heißt leer.** Kein Normwert wird vorbelegt und keiner geraten —
 *     ohne Außentemperatur bleibt die Heizlast unbekannt, und das Panel sagt es.
 *  2. Ein **mehrdeutiger** Wert („1.500") wird abgelehnt, nicht gesendet.
 */
describe('auslegungPruefen', () => {
  it('macht aus deutschen Eingaben einen API-Body', () => {
    const p = auslegungPruefen('-12,5', '80');
    expect(p.ok).toBe(true);
    expect(p.ok && p.payload).toEqual({
      design_outdoor_temp_c: '-12.5',
      heat_load_w_per_m2: '80',
    });
  });

  it('leer = null (zurücksetzen), nicht 0 und nicht „unverändert"', () => {
    const p = auslegungPruefen('', '');
    expect(p.ok).toBe(true);
    expect(p.ok && p.payload).toEqual({
      design_outdoor_temp_c: null,
      heat_load_w_per_m2: null,
    });
  });

  it('nimmt negative Außentemperaturen (der Normalfall)', () => {
    const p = auslegungPruefen('-16', '');
    expect(p.ok && p.payload.design_outdoor_temp_c).toBe('-16');
  });

  it('LEHNT eine mehrdeutige Eingabe AB, statt sie zu raten', () => {
    const p = auslegungPruefen('', '1.500');
    expect(p.ok).toBe(false);
    expect(!p.ok && p.fehler).toContain('nicht eindeutig');
  });

  it('lehnt einen Kennwert ≤ 0 ab — er würde 0 W Heizlast ergeben', () => {
    expect(auslegungPruefen('', '0').ok).toBe(false);
    expect(auslegungPruefen('', '-5').ok).toBe(false);
  });
});

describe('AuslegungPanel', () => {
  let fixture: ComponentFixture<AuslegungPanel>;
  let http: HttpTestingController;

  const felder = (): HTMLInputElement[] =>
    Array.from(
      (fixture.nativeElement as HTMLElement).querySelectorAll<HTMLInputElement>(
        'input[inputmode="decimal"]',
      ),
    );
  const tippen = (el: HTMLInputElement, wert: string) => {
    el.value = wert;
    el.dispatchEvent(new Event('input'));
    fixture.detectChanges();
  };
  const speichern = () => {
    (fixture.nativeElement as HTMLElement)
      .querySelector<HTMLButtonElement>('button[type="submit"]')!
      .click();
    fixture.detectChanges();
  };
  const text = () =>
    ((fixture.nativeElement as HTMLElement).textContent ?? '').replace(/\s+/g, ' ');

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AuslegungPanel],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();

    fixture = TestBed.createComponent(AuslegungPanel);
    http = TestBed.inject(HttpTestingController);
    fixture.componentRef.setInput('propertyId', 'p-1');
    fixture.componentRef.setInput('darfAendern', true);
  });

  afterEach(() => http.verify());

  it('zeigt den gespeicherten Stand — ohne Tausenderpunkt im Eingabefeld', () => {
    fixture.componentRef.setInput('auslegung', {
      design_outdoor_temp_c: '-12.000',
      heat_load_w_per_m2: '1500.000',
    });
    fixture.detectChanges();
    expect(felder().map((e) => e.value)).toEqual(['-12', '1500']);
  });

  it('sagt AUFFÄLLIG, wenn die Außentemperatur fehlt — statt still „unbekannt"', () => {
    fixture.componentRef.setInput('auslegung', {
      design_outdoor_temp_c: null,
      heat_load_w_per_m2: '80',
    });
    fixture.detectChanges();
    const luecke = (fixture.nativeElement as HTMLElement).querySelector('.au__luecke');
    expect(luecke).not.toBeNull();
    expect(luecke!.textContent).toContain('Auslegungs-Außentemperatur fehlt');
    // Kein geratener Normwert im Feld.
    expect(felder()[0].value).toBe('');
  });

  it('belegt nichts vor und zeigt keine Lücke, wenn beides gepflegt ist', () => {
    fixture.componentRef.setInput('auslegung', {
      design_outdoor_temp_c: '-12',
      heat_load_w_per_m2: '80',
    });
    fixture.detectChanges();
    expect((fixture.nativeElement as HTMLElement).querySelector('.au__luecke')).toBeNull();
  });

  it('speichert per PATCH und meldet den neuen Stand nach oben', () => {
    fixture.componentRef.setInput('auslegung', {
      design_outdoor_temp_c: null,
      heat_load_w_per_m2: null,
    });
    fixture.detectChanges();

    let gemeldet: unknown = null;
    fixture.componentInstance.gespeichert.subscribe((a) => (gemeldet = a));

    tippen(felder()[0], '-12');
    speichern();

    const req = http.expectOne('/api/property/properties/p-1/auslegung');
    expect(req.request.method).toBe('PATCH');
    // Der Kennwert ist leer → explizites null (zurücksetzen), NICHT 0.
    expect(req.request.body).toEqual({
      design_outdoor_temp_c: '-12',
      heat_load_w_per_m2: null,
    });

    req.flush({ design_outdoor_temp_c: '-12.000', heat_load_w_per_m2: null });
    fixture.detectChanges();
    expect(gemeldet).toEqual({ design_outdoor_temp_c: '-12.000', heat_load_w_per_m2: null });
    expect(text()).toContain('Auslegungsdaten gespeichert');
  });

  it('sendet eine mehrdeutige Eingabe NICHT — sie wird gemeldet', () => {
    fixture.componentRef.setInput('auslegung', {
      design_outdoor_temp_c: null,
      heat_load_w_per_m2: null,
    });
    fixture.detectChanges();

    tippen(felder()[1], '1.500');
    speichern();

    http.expectNone('/api/property/properties/p-1/auslegung');
    expect(
      (fixture.nativeElement as HTMLElement).querySelector('[role="alert"]')?.textContent,
    ).toContain('nicht eindeutig');
  });

  // --- B3: Das Nachladen des Elternteils darf nichts wegwerfen ---------------
  it('ein inhaltsgleiches Nachladen überschreibt die laufende Eingabe NICHT', () => {
    fixture.componentRef.setInput('auslegung', {
      design_outdoor_temp_c: '-12.000',
      heat_load_w_per_m2: null,
    });
    fixture.detectChanges();

    tippen(felder()[1], '95'); // der Anwender tippt gerade den Kennwert

    // Das Eltern-`computed` baut bei jedem Nachladen (auch nach jedem
    // Raum-Speichern) ein NEUES Objekt mit demselben Inhalt.
    fixture.componentRef.setInput('auslegung', {
      design_outdoor_temp_c: '-12.000',
      heat_load_w_per_m2: null,
    });
    fixture.detectChanges();

    expect(felder()[1].value).toBe('95');
    expect(felder()[0].value).toBe('-12');
  });

  it('nach dem Speichern bleibt die Erfolgsmeldung stehen, wenn das Elternteil nachlädt', () => {
    fixture.componentRef.setInput('auslegung', {
      design_outdoor_temp_c: null,
      heat_load_w_per_m2: null,
    });
    fixture.detectChanges();

    tippen(felder()[0], '-12');
    speichern();
    http
      .expectOne('/api/property/properties/p-1/auslegung')
      .flush({ design_outdoor_temp_c: '-12.000', heat_load_w_per_m2: null });
    fixture.detectChanges();
    expect(text()).toContain('Auslegungsdaten gespeichert');

    // Das Elternteil lädt das Aufmaß neu und reicht den gespeicherten Stand
    // herein — inhaltsgleich (nur anders formatiert), also darf nichts passieren.
    fixture.componentRef.setInput('auslegung', {
      design_outdoor_temp_c: '-12.0',
      heat_load_w_per_m2: null,
    });
    fixture.detectChanges();

    expect(text()).toContain('Auslegungsdaten gespeichert');
    expect(felder()[0].value).toBe('-12');
  });

  it('eine ECHTE Änderung von außen übernimmt das Formular weiterhin', () => {
    fixture.componentRef.setInput('auslegung', {
      design_outdoor_temp_c: '-12',
      heat_load_w_per_m2: null,
    });
    fixture.detectChanges();
    fixture.componentRef.setInput('auslegung', {
      design_outdoor_temp_c: '-16',
      heat_load_w_per_m2: '80',
    });
    fixture.detectChanges();

    expect(felder().map((e) => e.value)).toEqual(['-16', '80']);
  });

  // --- B4: Die Anleitung darf keine Form vorgeben, die der Parser ablehnt ----
  it('nennt das Beispiel mit ASCII-Minus (kein U+2212, das „nicht lesbar" ergäbe)', () => {
    fixture.componentRef.setInput('auslegung', {
      design_outdoor_temp_c: null,
      heat_load_w_per_m2: null,
    });
    fixture.detectChanges();
    const luecke = (fixture.nativeElement as HTMLElement).querySelector(
      '.au__luecke',
    )!.textContent!;
    expect(luecke).toContain('-12');
    expect(luecke).not.toContain('−');
    // Und die genannte Form ist auch wirklich lesbar:
    expect(auslegungPruefen('-12', '').ok).toBe(true);
  });

  it('ohne Änderungsrecht ist Speichern gesperrt', () => {
    fixture.componentRef.setInput('darfAendern', false);
    fixture.componentRef.setInput('auslegung', {
      design_outdoor_temp_c: null,
      heat_load_w_per_m2: null,
    });
    fixture.detectChanges();
    const btn = (fixture.nativeElement as HTMLElement).querySelector<HTMLButtonElement>(
      'button[type="submit"]',
    )!;
    expect(btn.disabled).toBe(true);
    expect(text()).toContain('fehlt das Recht');
  });
});
