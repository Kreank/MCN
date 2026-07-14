import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SucheErgebnis, SucheKategorie, SucheTreffer } from '../../core/suche.service';
import { Kommandopalette } from './kommandopalette';

/**
 * Die Kernbeschwerde des Nutzers am alten System: „Ich gebe die exakte
 * Angebotsnummer ein und bekomme eine elend lange Liste." Der Direkttreffer ist
 * die Antwort darauf — er steht oben, ist vorausgewaehlt, und Enter springt
 * sofort dorthin. Genau das prueft dieser Test scharf, dazu die Tastaturkette
 * ueber Kategoriegrenzen hinweg und die ehrlichen Leerzustaende.
 */
const treffer = (
  typ: SucheTreffer['typ'],
  id: string,
  titel: string,
  rang = 1,
): SucheTreffer => ({
  typ,
  id,
  titel,
  untertitel: `Kontext zu ${titel}`,
  status: null,
  rang,
  grund: 'Bezeichnung',
  ist_direkttreffer: rang === 0,
});

/** Antwort bauen — `kategorien` spiegelt per Vorgabe die gelieferten Zeilen. */
const antwort = (
  begriff: string,
  liste: SucheTreffer[],
  direkttreffer: SucheTreffer | null = null,
  kategorien?: SucheKategorie[],
): SucheErgebnis => ({
  begriff,
  treffer: liste,
  direkttreffer,
  kategorien:
    kategorien ??
    [...new Set(liste.map((t) => t.typ))].map((typ) => ({
      typ,
      anzahl: liste.filter((t) => t.typ === typ).length,
      mehr_vorhanden: false,
    })),
});

describe('Kommandopalette', () => {
  let fixture: ComponentFixture<Kommandopalette>;
  let http: HttpTestingController;

  beforeEach(async () => {
    vi.useFakeTimers();
    await TestBed.configureTestingModule({
      imports: [Kommandopalette],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();
    fixture = TestBed.createComponent(Kommandopalette);
    http = TestBed.inject(HttpTestingController);
    fixture.detectChanges();
  });

  const el = (): HTMLElement => fixture.nativeElement as HTMLElement;

  const oeffnen = (): void => {
    el().querySelector<HTMLButtonElement>('.kp-trigger')!.click();
    fixture.detectChanges();
  };

  /** Tippen + Debounce abwarten + Serverantwort einspielen. */
  const tippen = (q: string, antwort: SucheErgebnis | null): void => {
    const feld = el().querySelector<HTMLInputElement>('.kp__feld')!;
    feld.value = q;
    feld.dispatchEvent(new Event('input'));
    vi.advanceTimersByTime(250);
    if (antwort !== null) {
      const req = http.expectOne((r) => r.url === '/api/suche' && r.params.get('q') === q);
      req.flush(antwort);
    }
    vi.runOnlyPendingTimers();
    fixture.detectChanges();
  };

  const taste = (key: string): void => {
    el()
      .querySelector<HTMLInputElement>('.kp__feld')!
      .dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
    fixture.detectChanges();
  };

  it('zeigt vor der Eingabe, wonach man suchen kann — statt einer leeren Liste', () => {
    oeffnen();
    const text = el().querySelector('.kp__leer')!.textContent!;
    expect(text).toContain('Adresse');
    expect(text).toContain('Belegnummer');
    expect(text).toContain('Artikelnummer');
    http.expectNone('/api/suche'); // Leerer Begriff fragt den Server gar nicht erst.
  });

  it('stellt den Direkttreffer voran, waehlt ihn vor und springt mit Enter dorthin', () => {
    const direkt = treffer('ANGEBOT', 'a-1', 'AN-2026-0042', 0);
    oeffnen();
    // Der Server liefert den Direkttreffer laut Vertrag AUCH in `treffer` (Rang 0).
    tippen(
      'AN-2026-0042',
      antwort('AN-2026-0042', [direkt, treffer('KONTAKT', 'k-9', 'Meier GmbH')], direkt),
    );

    const opts = el().querySelectorAll('.kp__opt');
    expect(opts.length).toBe(2); // Kein Doppel: der Direkttreffer erscheint genau einmal.
    expect(opts[0].classList).toContain('kp__opt--direkt');
    expect(opts[0].getAttribute('aria-selected')).toBe('true');
    expect(opts[0].textContent).toContain('Direkttreffer'); // Marke als TEXT, nicht nur Farbe.
    expect(el().querySelector('.kp__feld')!.getAttribute('aria-activedescendant')).toBe('kp-opt-0');

    const nav = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    taste('Enter');
    expect(nav).toHaveBeenCalledWith(['/dokumente', 'a-1']);
  });

  it('fuehrt die Pfeiltasten ueber Kategoriegrenzen hinweg; End springt ans Ende', () => {
    oeffnen();
    tippen(
      'mei',
      antwort('mei', [
        treffer('KONTAKT', 'k-1', 'Meier'),
        treffer('LIEGENSCHAFT', 'l-1', 'Meierweg 3'),
        treffer('ARTIKEL', 'ar-1', 'Meissel'),
      ]),
    );

    expect(el().querySelectorAll('.kp__gruppe').length).toBe(3);
    taste('ArrowDown'); // 0 -> 1: von der Kontakt- in die Liegenschaftsgruppe.
    expect(el().querySelectorAll('.kp__opt')[1].getAttribute('aria-selected')).toBe('true');
    taste('End');
    expect(el().querySelectorAll('.kp__opt')[2].getAttribute('aria-selected')).toBe('true');
    taste('ArrowDown'); // Umlauf zurueck an den Anfang.
    expect(el().querySelectorAll('.kp__opt')[0].getAttribute('aria-selected')).toBe('true');
  });

  it('ordnet die Gruppen nach dem RANG des Servers, nicht nach der Navigationsfolge', () => {
    oeffnen();
    // KONTAKT steht in der Navigationsordnung ganz vorn, trifft hier aber nur
    // ueber eine Beziehung (Rang 3). ARTIKEL steht hinten, trifft aber am
    // Wortanfang (Rang 1). Der Artikel MUSS oben stehen — in der Liste UND unter
    // dem Pfeil. Genau hier ging dem Nutzer bisher der Treffer verloren.
    tippen(
      'rohr',
      antwort('rohr', [
        treffer('ARTIKEL', 'ar-1', 'Rohr 15mm', 1),
        treffer('KONTAKT', 'k-1', 'Rohrbach GmbH', 3),
      ]),
    );

    const gruppen = el().querySelectorAll('.kp__gruppe');
    expect(gruppen[0].getAttribute('aria-label')).toContain('Artikel');
    expect(gruppen[1].getAttribute('aria-label')).toContain('Kontakte');

    // Die Tastaturreihenfolge folgt der sichtbaren Reihenfolge exakt.
    const opts = el().querySelectorAll('.kp__opt');
    expect(opts[0].textContent).toContain('Rohr 15mm');
    expect(opts[0].id).toBe('kp-opt-0');
    expect(opts[0].getAttribute('aria-selected')).toBe('true');

    const nav = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    taste('Enter');
    expect(nav).toHaveBeenCalledWith(['/artikel', 'ar-1']); // nicht der Kontakt.
  });

  it('markiert bei ZWEI exakten Kennungen beide Zeilen (direkttreffer bleibt null)', () => {
    oeffnen();
    // Mehrdeutig: Artikelnummer des einen == GTIN des anderen. Der Server setzt
    // `direkttreffer` auf null, markiert aber beide Zeilen mit rang 0.
    tippen(
      '4012345678901',
      antwort('4012345678901', [
        treffer('ARTIKEL', 'ar-1', 'Rohr 15mm', 0),
        treffer('LEISTUNG', 'le-1', 'Rohrmontage', 0),
      ]),
    );

    expect(el().querySelector('.kp__opt--direkt')).toBeNull(); // keine Kopfzeile …
    const marken = el().querySelectorAll('.kp__marke');
    expect(marken.length).toBe(2); // … dafuer beide Zeilen markiert.
    expect(el().querySelectorAll('.kp__opt')[0].textContent).toContain('Direkttreffer');
    expect(el().querySelectorAll('.kp__opt')[1].textContent).toContain('Direkttreffer');
  });

  it('meldet „keine Treffer" klar — und blendet keine leeren Kategorien ein', () => {
    oeffnen();
    tippen('xyzq', antwort('xyzq', []));
    expect(el().querySelector('.kp__meldung')!.textContent).toContain('Keine Treffer');
    expect(el().querySelectorAll('.kp__gruppe').length).toBe(0);
    expect(el().querySelector('.kp__zahl')!.textContent).toContain('Keine Treffer');
  });

  it('sagt in der Gruppenueberschrift, wenn der Server gekuerzt hat („von mehr")', () => {
    oeffnen();
    tippen(
      'roh',
      antwort('roh', [treffer('ARTIKEL', 'ar-1', 'Rohr 15mm')], null, [
        { typ: 'ARTIKEL', anzahl: 1, mehr_vorhanden: true },
      ]),
    );

    const kat = el().querySelector('.kp__kat-mehr')!;
    expect(kat.textContent).toContain('1 von mehr'); // Kuerzung im Bild …
    const gruppe = el().querySelector('.kp__gruppe')!;
    expect(gruppe.getAttribute('aria-label')).toContain('von mehr'); // … und im Namen.
    expect(el().querySelector('.kp__zahl')!.textContent).toContain('gekürzt');
  });

  it('benennt Kategorien, aus denen keine Zeile mehr in die Liste passte', () => {
    oeffnen();
    // Die Gesamtgrenze hat LEISTUNG ganz verdraengt: anzahl=0, mehr_vorhanden=true.
    tippen(
      'mont',
      antwort('mont', [treffer('KONTAKT', 'k-1', 'Montag GmbH')], null, [
        { typ: 'KONTAKT', anzahl: 1, mehr_vorhanden: false },
        { typ: 'LEISTUNG', anzahl: 0, mehr_vorhanden: true },
      ]),
    );

    // Keine leere Kategorie in der Liste — aber ein ehrlicher Hinweis darunter.
    expect(el().querySelectorAll('.kp__gruppe').length).toBe(1);
    expect(el().querySelector('.kp__gekuerzt')!.textContent).toContain('Leistungen');
  });

  it('sagt bei zwei Zeichen, dass der Artikelstamm aussen vor bleibt („zr")', () => {
    oeffnen();
    // Der Server laesst ARTIKEL bei < 3 Zeichen aus (Trigramm-Index) — die
    // anderen Kategorien liefern weiter. Ohne Hinweis haelt der Nutzer die Suche
    // fuer kaputt („ZR" ist ein echtes Artikelnummern-Praefix).
    tippen('zr', antwort('zr', [treffer('KONTAKT', 'k-1', 'Zrenner GmbH')]));

    const hinweis = el().querySelector('.kp__grenze')!;
    expect(hinweis.textContent).toContain('Artikelstamm');
    expect(hinweis.getAttribute('role')).toBe('status'); // kommt am Screenreader an
    expect(el().querySelectorAll('.kp__opt').length).toBe(1); // Liste bleibt daneben.
  });

  it('zeigt den Hinweis NICHT, sobald ein Token drei Zeichen traegt („zr-6" → „zr6")', () => {
    oeffnen();
    // Dieselbe Normalisierung wie der Server: Bindestrich faellt weg, „zr6" ist
    // trigrammfaehig — der Artikelstamm WIRD durchsucht, also kein Hinweis.
    tippen('zr-6', antwort('zr-6', [treffer('ARTIKEL', 'ar-1', 'ZR-6 Rohr')]));
    expect(el().querySelector('.kp__grenze')).toBeNull();
  });

  it('luegt bei einem einzelnen Zeichen nicht „keine Treffer" — der Server sucht gar nicht', () => {
    oeffnen();
    tippen('z', antwort('z', []));
    const meldung = el().querySelector('.kp__meldung')!.textContent!;
    expect(meldung).toContain('mindestens 2 Zeichen');
    expect(meldung).not.toContain('Keine Treffer');
  });

  it('sucht denselben Begriff nach dem Leeren erneut (kein haengendes distinctUntilChanged)', () => {
    oeffnen();
    tippen('meier', antwort('meier', [treffer('KONTAKT', 'k-1', 'Meier')]));
    expect(el().querySelectorAll('.kp__opt').length).toBe(1);

    tippen('', null); // Leeren: keine Anfrage, zurueck zum Hinweis.
    expect(el().querySelector('.kp__leer')).not.toBeNull();

    // Derselbe Begriff noch einmal — er MUSS wieder eine Anfrage ausloesen.
    tippen('meier', antwort('meier', [treffer('KONTAKT', 'k-1', 'Meier')]));
    expect(el().querySelectorAll('.kp__opt').length).toBe(1);
  });

  it('oeffnet und schliesst per Strg+K und faengt die Browser-Belegung ab', () => {
    const ev = new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, cancelable: true });
    document.dispatchEvent(ev);
    fixture.detectChanges();
    expect(ev.defaultPrevented).toBe(true);
    expect(el().querySelector('.kp')!.hasAttribute('open')).toBe(true);

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true }));
    fixture.detectChanges();
    expect(el().querySelector('.kp')!.hasAttribute('open')).toBe(false);
  });
});
