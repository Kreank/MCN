import {
  Flaechenwert,
  OeffnungMasse,
  apiZahl,
  bruttoGesamt,
  eingabe,
  flaecheAusMassen,
  ganzzahlAus,
  istFehleingabe,
  mitEinheit,
  nettoFlaeche,
  nettoGesamt,
  oeffnungFlaeche,
  oeffnungenGesamt,
  oeffnungenSumme,
  runde,
  summeApi,
  volumenAus,
  zahlAus,
  zeige,
  zeigeApi,
} from './raum-rechnen';

/** Der m²-Wert — oder `null`, wenn die Fläche unbekannt ist. Nie eine erfundene 0. */
const m2 = (f: Flaechenwert): number | null => (f.art === 'wert' ? f.m2 : null);

/**
 * Der Rechenkern des Raumaufmaßes. Zwei Dinge sind hier nicht verhandelbar:
 *
 *  1. Eine **mehrdeutige** Eingabe („1.500") wird ABGELEHNT, nicht geraten.
 *     Genau hier lag der frühere Datenverlust-Bug (1200 → „1.200" → 1,5).
 *  2. Ein fehlender Wert ist **null**, niemals 0 — weder in der Nettofläche noch
 *     in einer Summe. Eine erfundene 0 wäre eine Behauptung.
 */
describe('Zahleingabe', () => {
  it('liest deutsche Kommazahlen', () => {
    expect(zahlAus('4,20')).toBe(4.2);
    expect(zahlAus('12')).toBe(12);
    expect(zahlAus('2.5')).toBe(2.5); // Punkt-Dezimal, eindeutig
  });

  it('LEHNT mehrdeutige Eingaben AB (der Datenverlust-Bug)', () => {
    expect(eingabe('1.500').art).toBe('fehler');
    expect(zahlAus('1.500')).toBeNull();
    expect(istFehleingabe('1.500')).toBe(true);
    // Eindeutig gemeint: entweder so …
    expect(zahlAus('1500')).toBe(1500);
    // … oder so.
    expect(zahlAus('1,5')).toBe(1.5);
  });

  it('behandelt leer als leer (kein Fehler, keine 0)', () => {
    expect(eingabe('').art).toBe('leer');
    expect(eingabe(null).art).toBe('leer');
    expect(zahlAus('')).toBeNull();
    expect(istFehleingabe('')).toBe(false);
  });

  it('erkennt Buchstabensalat als Fehler', () => {
    expect(istFehleingabe('drei Meter')).toBe(true);
    expect(zahlAus('abc')).toBeNull();
  });

  it('Ganzzahlen: nur positive Stückzahlen', () => {
    expect(ganzzahlAus('2')).toBe(2);
    expect(ganzzahlAus('0')).toBeNull();
    expect(ganzzahlAus('1,5')).toBeNull();
    expect(ganzzahlAus('-3')).toBeNull();
    expect(ganzzahlAus('')).toBeNull();
  });
});

describe('Geometrie (das Einzige, was der Client rechnen darf)', () => {
  it('Fläche = Länge × Breite', () => {
    expect(flaecheAusMassen('4,20', '3,10')).toBe(13.02);
    expect(flaecheAusMassen('5', '4')).toBe(20);
  });

  it('ohne vollständige Maße keine Fläche (null, nicht 0)', () => {
    expect(flaecheAusMassen('4,20', '')).toBeNull();
    expect(flaecheAusMassen('', '3,10')).toBeNull();
    expect(flaecheAusMassen('0', '3,10')).toBeNull();
    expect(flaecheAusMassen('1.500', '3')).toBeNull(); // mehrdeutig → kein Ergebnis
  });

  it('Volumen = Fläche × Höhe', () => {
    expect(volumenAus('13,02', '2,5')).toBe(32.55);
    expect(volumenAus('13,02', '')).toBeNull();
  });

  it('Öffnungsfläche = Anzahl × Breite × Höhe', () => {
    expect(oeffnungFlaeche('2', '1,20', '1,40')).toBe(3.36);
    expect(oeffnungFlaeche('1', '0,9', '2,0')).toBe(1.8);
    expect(oeffnungFlaeche('', '1,20', '1,40')).toBeNull();
    expect(oeffnungFlaeche('2', '1,20', '')).toBeNull();
  });

  it('rundet auf die DB-Skala (3 Nachkommastellen)', () => {
    expect(runde(1.0005)).toBe(1.001);
    expect(apiZahl(13.02)).toBe('13.02');
    expect(apiZahl(20)).toBe('20');
    expect(apiZahl(1 / 3)).toBe('0.333');
  });
});

describe('Nettofläche', () => {
  const oeffnungen: OeffnungMasse[] = [
    { surfaceRef: 'w1', anzahl: '2', breite: '1,20', hoehe: '1,40' }, // 3,36
    { surfaceRef: 'w1', anzahl: '1', breite: '0,90', hoehe: '2,00' }, // 1,80
    { surfaceRef: 'w2', anzahl: '1', breite: '1,00', hoehe: '1,00' }, // 1,00
    { surfaceRef: null, anzahl: '1', breite: '1,00', hoehe: '1,00' }, // keiner Wand
  ];

  it('summiert nur die Öffnungen DIESER Wand', () => {
    expect(m2(oeffnungenSumme('w1', oeffnungen))).toBe(5.16);
    expect(m2(oeffnungenSumme('w2', oeffnungen))).toBe(1);
    expect(m2(oeffnungenSumme('w3', oeffnungen))).toBe(0); // keine Öffnung = echte 0
  });

  it('netto = brutto − Öffnungen', () => {
    expect(m2(nettoFlaeche('20', 'w1', oeffnungen))).toBe(14.84);
    expect(m2(nettoFlaeche('20', 'w3', oeffnungen))).toBe(20);
  });

  it('ohne Bruttofläche gibt es keine Nettofläche (unbekannt, nicht 0)', () => {
    const leer = nettoFlaeche('', 'w1', oeffnungen);
    expect(leer.art).toBe('unbekannt');
    expect(m2(leer)).toBeNull();
    const mehrdeutig = nettoFlaeche('1.500', 'w1', oeffnungen);
    expect(mehrdeutig.art).toBe('unbekannt');
  });

  it('zeigt eine Überdeckung als NEGATIVE Fläche, statt sie auf 0 zu klemmen', () => {
    // Fenster größer als die Wand — ein Erfassungsfehler. Wir verstecken ihn nicht.
    // (Die DB lehnt ihn beim Speichern ab; hier soll er sichtbar sein.)
    expect(m2(nettoFlaeche('4', 'w1', oeffnungen))).toBe(-1.16);
  });

  // ---- Der Befund: eine HALB getippte Öffnung darf nicht als 0 m² zählen ----
  it('eine halb getippte Öffnung macht die Nettofläche UNBEKANNT (nicht zu groß)', () => {
    const halb: OeffnungMasse[] = [
      { surfaceRef: 'w1', anzahl: '1', breite: '1,20', hoehe: '1,40' }, // 1,68
      { surfaceRef: 'w1', anzahl: '1', breite: '0,90', hoehe: '' }, // Höhe fehlt noch
    ];
    // Früher: 20 − (1,68 + 0) = 18,32 m² — die Wand wäre zu groß gezeigt worden.
    const summe = oeffnungenSumme('w1', halb);
    expect(summe.art).toBe('unbekannt');
    expect(m2(summe)).toBeNull();

    const netto = nettoFlaeche('20', 'w1', halb);
    expect(netto.art).toBe('unbekannt');
    expect(m2(netto)).toBeNull();
    expect(netto.art === 'unbekannt' && netto.grund).toContain('unvollständig');
  });

  it('eine unvollständige Öffnung an einer ANDEREN Wand stört diese Wand nicht', () => {
    const gemischt: OeffnungMasse[] = [
      { surfaceRef: 'w1', anzahl: '1', breite: '1,00', hoehe: '1,00' },
      { surfaceRef: 'w2', anzahl: '1', breite: '0,90', hoehe: '' }, // unvollständig
    ];
    expect(m2(nettoFlaeche('20', 'w1', gemischt))).toBe(19);
    expect(nettoFlaeche('20', 'w2', gemischt).art).toBe('unbekannt');
  });
});

describe('Raumweite Vorschau (Wandflächen)', () => {
  const huellen = [{ brutto: '20' }, { brutto: '15,5' }];
  const oeffnungen: OeffnungMasse[] = [
    { surfaceRef: 'w1', anzahl: '1', breite: '1,00', hoehe: '1,00' },
    { surfaceRef: null, anzahl: '1', breite: '2,00', hoehe: '1,00' }, // ohne Wandzuordnung
  ];

  it('summiert brutto, Öffnungen (auch die ohne Wand) und netto', () => {
    expect(m2(bruttoGesamt(huellen))).toBe(35.5);
    expect(m2(oeffnungenGesamt(oeffnungen))).toBe(3);
    expect(m2(nettoGesamt(huellen, oeffnungen))).toBe(32.5);
  });

  it('OHNE Hüllfläche ist die Wandfläche unbekannt — nicht 0', () => {
    const leer = bruttoGesamt([]);
    expect(leer.art).toBe('unbekannt');
    expect(leer.art === 'unbekannt' && leer.grund).toContain('keine Hüllfläche');
    expect(nettoGesamt([], oeffnungen).art).toBe('unbekannt');
  });

  it('eine Hüllfläche ohne lesbare Bruttofläche macht die Summe unbekannt', () => {
    expect(bruttoGesamt([{ brutto: '20' }, { brutto: '' }]).art).toBe('unbekannt');
    expect(bruttoGesamt([{ brutto: '1.500' }]).art).toBe('unbekannt'); // mehrdeutig
  });

  it('eine unvollständige Öffnung macht die Netto-Vorschau unbekannt', () => {
    const halb: OeffnungMasse[] = [{ surfaceRef: null, anzahl: '1', breite: '2,00', hoehe: '' }];
    expect(oeffnungenGesamt(halb).art).toBe('unbekannt');
    expect(m2(nettoGesamt(huellen, halb))).toBeNull();
  });
});

describe('Anzeige', () => {
  it('formatiert deutsch', () => {
    expect(zeige(13.02)).toBe('13,02');
    expect(zeige(1234.5, 1)).toBe('1.234,5');
  });

  it('macht aus einem unbekannten Wert NIE eine 0', () => {
    expect(zeigeApi(null)).toBe('unbekannt');
    expect(zeigeApi('')).toBe('unbekannt');
    expect(zeigeApi(null, 0, '—')).toBe('—');
    expect(zeigeApi('0')).toBe('0,00'); // eine echte 0 bleibt eine 0
    expect(zeigeApi('13.020')).toBe('13,02');
    expect(zeigeApi(13.02)).toBe('13,02'); // auch ein float vom Server
  });

  it('hängt an einen unbekannten Wert KEINE Einheit', () => {
    expect(mitEinheit('13.02', 'm²')).toBe('13,02 m²');
    expect(mitEinheit(null, 'm²')).toBe('unbekannt');
    expect(mitEinheit(null, 'm²', 2, '—')).toBe('—');
    expect(mitEinheit('0', 'm²')).toBe('0,00 m²');
  });

  it('summiert nur Bekanntes; ohne jeden Wert ist die Summe unbekannt', () => {
    expect(summeApi(['13.02', '20', null])).toBe(33.02);
    expect(summeApi([null, undefined, ''])).toBeNull();
    expect(summeApi([])).toBeNull();
  });
});
