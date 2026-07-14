import {
  Punkt,
  beschreibung,
  flaecheM2,
  istGueltig,
  istSchliessendeKante,
  kanteLaengeSetzen,
  kanten,
  meterAusMm,
  mmAusMeter,
  nordRichtung,
  oeffnungPasst,
  pruefe,
  punktAufKante,
  punktEinfuegen,
  punktLoeschen,
  punktVerschieben,
  sichtEinpassen,
  snapMm,
  snapPunkt,
  streckenSchneiden,
  umfangM,
  umlaufsinn,
  zuSicht,
  zuWelt,
} from './geometrie';

const p = (x: number, y: number): Punkt => ({ x_mm: x, y_mm: y });

/** 5 m × 4 m, gegen den Uhrzeigersinn. 20 m², 18 m Umfang. */
const RECHTECK: Punkt[] = [p(0, 0), p(5000, 0), p(5000, 4000), p(0, 4000)];
/** Dasselbe Rechteck, IM Uhrzeigersinn — dieselbe Fläche, nur andersherum gelaufen. */
const RECHTECK_UZS: Punkt[] = [p(0, 0), p(0, 4000), p(5000, 4000), p(5000, 0)];

/**
 * L-förmiger Raum (6 Punkte). Ein 5×4-Rechteck, aus dem oben rechts ein
 * 2×1,5-Stück fehlt: 20 − 3 = 17 m².
 */
const L_FORM: Punkt[] = [
  p(0, 0),
  p(5000, 0),
  p(5000, 2500),
  p(3000, 2500),
  p(3000, 4000),
  p(0, 4000),
];

describe('Millimeter — ganzzahlig, einmal gerundet', () => {
  it('rechnet Meter in ganze Millimeter (der Laser misst 4,37 m)', () => {
    expect(mmAusMeter(4.37)).toBe(4370);
    expect(mmAusMeter(0.001)).toBe(1);
    // 2,0005 m: kaufmännisch auf 2001 mm — und NICHT auf einen Gleitkommawert.
    expect(mmAusMeter(2.0005)).toBe(2001);
    expect(Number.isInteger(mmAusMeter(1 / 3))).toBe(true);
  });

  it('rechnet zurück in Meter (millimetergenau)', () => {
    expect(meterAusMm(4370)).toBe(4.37);
    expect(meterAusMm(1)).toBe(0.001);
  });

  it('fängt auf das Raster — und immer auf ganze Millimeter', () => {
    expect(snapMm(1234, 100)).toBe(1200);
    expect(snapMm(1260, 100)).toBe(1300);
    expect(snapMm(1234, 250)).toBe(1250);
    expect(snapMm(1234, 0)).toBe(1234); // frei = nur ganze mm
    expect(snapMm(1234.6, 0)).toBe(1235);
    expect(snapPunkt(p(1234, 987), 250)).toEqual(p(1250, 1000));
  });
});

describe("Fläche (Gauß'sche Trapezformel, als BETRAG)", () => {
  it('rechnet das Rechteck', () => {
    expect(flaecheM2(RECHTECK)).toBe(20);
  });

  it('BEIDE Umlaufsinne ergeben dieselbe POSITIVE Fläche', () => {
    expect(flaecheM2(RECHTECK_UZS)).toBe(20);
    expect(flaecheM2(RECHTECK)).toBe(flaecheM2(RECHTECK_UZS));
    // Der Umlaufsinn selbst ist trotzdem unterscheidbar (er trägt die Außennormale).
    expect(umlaufsinn(RECHTECK)).toBe(1);
    expect(umlaufsinn(RECHTECK_UZS)).toBe(-1);
  });

  it('rechnet die L-Form', () => {
    expect(flaecheM2(L_FORM)).toBe(17);
    expect(flaecheM2([...L_FORM].reverse())).toBe(17); // andersherum: gleich groß
  });

  it('ohne 3 Punkte gibt es keine Fläche', () => {
    expect(flaecheM2([])).toBe(0);
    expect(flaecheM2([p(0, 0), p(1000, 0)])).toBe(0);
  });
});

describe('Umfang', () => {
  it('summiert die Kantenlängen des GESCHLOSSENEN Umrisses', () => {
    expect(umfangM(RECHTECK)).toBe(18); // 5 + 4 + 5 + 4
    expect(umfangM(L_FORM)).toBe(18); // 5 + 2,5 + 2 + 1,5 + 3 + 4
  });

  it('zählt die schließende Kante mit', () => {
    // Dreieck 3-4-5.
    expect(umfangM([p(0, 0), p(3000, 0), p(3000, 4000)])).toBe(12);
  });

  /**
   * **Die ausgewiesene Summe muss die Summe der ausgewiesenen Teile sein.**
   *
   * Der Server rundet jede Kante einzeln auf Millimeter und summiert die
   * gerundeten. Summierte der Client stattdessen die rohen Gleitkommalängen und
   * rundete erst am Schluss, käme eine „genauere" Zahl heraus, die weder zu den
   * Kantenlängen daneben in der Liste passt noch zu der, die gespeichert wird —
   * die Anzeige spränge beim Speichern. Der Umfang ist die Mengengrundlage für
   * Sockelleisten und geht so in ein Angebot.
   */
  it('rundet JEDE Kante einzeln — wie der Server (Raute, nicht achsparallel)', () => {
    const raute = [p(0, 0), p(1000, 1000), p(0, 2000), p(-1000, 1000)];
    // Jede Kante: √2 m = 1414,2135… mm → 1414 mm = 1,414 m. Vier davon: 5,656 m.
    const kantenGerundet = kanten(raute).map((k) => meterAusMm(k.laenge_mm));
    expect(kantenGerundet).toEqual([1.414, 1.414, 1.414, 1.414]);

    expect(umfangM(raute)).toBe(5.656); // die Summe der ausgewiesenen Teile
    expect(umfangM(raute)).toBe(kantenGerundet.reduce((s, m) => s + m, 0));

    // Die frühere Rechnung (roh summieren, einmal runden) ergab 5,657 m — die Zahl
    // sprang beim Speichern um einen Millimeter.
    expect(umfangM(raute)).not.toBe(5.657);
  });

  it('bleibt bei achsparallelen Kanten exakt (dort gab es nie eine Drift)', () => {
    expect(umfangM(RECHTECK)).toBe(18);
  });
});

describe('Kanten', () => {
  it('Kante i geht von Punkt i nach i+1, zyklisch', () => {
    const ks = kanten(RECHTECK);
    expect(ks).toHaveLength(4);
    expect(ks[0].von).toEqual(p(0, 0));
    expect(ks[0].bis).toEqual(p(5000, 0));
    expect(ks[0].laenge_mm).toBe(5000);
    // Die letzte schließt zurück auf Punkt 0.
    expect(ks[3].von).toEqual(p(0, 4000));
    expect(ks[3].bis).toEqual(p(0, 0));
    expect(ks[3].laenge_mm).toBe(4000);
  });

  it('die schließende Kante ist die letzte', () => {
    expect(istSchliessendeKante(3, 4)).toBe(true);
    expect(istSchliessendeKante(0, 4)).toBe(false);
  });
});

describe('Kantenlänge eintippen — der Kern der Bedienung', () => {
  it('verschiebt den Endpunkt ENTLANG der Kante; die Folgepunkte wandern mit', () => {
    // Der Handwerker misst mit dem Laser 4,37 m statt der gezeichneten 5,00 m.
    const neu = kanteLaengeSetzen(RECHTECK, 0, mmAusMeter(4.37))!;

    // Punkt 0 ist der Anker, Punkt 1 wandert entlang der Kantenrichtung — und die
    // FOLGEPUNKTE wandern um denselben Vektor mit. Die Kanten 1 und 2 behalten
    // dadurch Länge UND Richtung; was die Änderung aufnimmt, ist die SCHLIESSENDE
    // Kante. Das ist kein Fehler, sondern der einzige Freiheitsgrad, der übrig ist:
    // ein geschlossener Umriss kann nicht alle Kanten gleichzeitig festhalten.
    expect(neu).toEqual([p(0, 0), p(4370, 0), p(4370, 4000), p(-630, 4000)]);
    expect(kanten(neu)[0].laenge_mm).toBe(4370); // die getippte Länge steht
    expect(kanten(neu)[1].laenge_mm).toBe(4000); // Folgekante: unverändert
    expect(kanten(neu)[2].laenge_mm).toBe(5000); // Folgekante: unverändert
  });

  it('… und die Gegenkante nachzutippen macht daraus wieder ein Rechteck', () => {
    // Genau so misst man auf der Baustelle: eine Kante nach der anderen.
    const eins = kanteLaengeSetzen(RECHTECK, 0, mmAusMeter(4.37))!;
    const zwei = kanteLaengeSetzen(eins, 2, mmAusMeter(4.37))!;
    expect(zwei).toEqual([p(0, 0), p(4370, 0), p(4370, 4000), p(0, 4000)]);
    expect(flaecheM2(zwei)).toBe(17.48); // 4,37 × 4,00
    expect(umfangM(zwei)).toBe(16.74);
  });

  it('verlängert auch nach unten/oben korrekt (Richtung, nicht Achse)', () => {
    const neu = kanteLaengeSetzen(RECHTECK, 1, mmAusMeter(3))!;
    expect(neu[2]).toEqual(p(5000, 3000));
    expect(neu[3]).toEqual(p(0, 3000)); // der Folgepunkt wandert um denselben Vektor
    expect(neu[0]).toEqual(p(0, 0)); // Punkt 0 ist der Anker
    expect(flaecheM2(neu)).toBe(15);
  });

  it('rundet auf ganze Millimeter — auch auf einer schrägen Kante', () => {
    const schraeg: Punkt[] = [p(0, 0), p(3000, 4000), p(0, 4000)];
    const neu = kanteLaengeSetzen(schraeg, 0, 1000)!;
    expect(Number.isInteger(neu[1].x_mm)).toBe(true);
    expect(Number.isInteger(neu[1].y_mm)).toBe(true);
    expect(neu[1]).toEqual(p(600, 800)); // 3-4-5-Dreieck: 1000 mm → (600, 800)
  });

  it('die SCHLIESSENDE Kante lässt sich nicht setzen — sie ist abgeleitet', () => {
    expect(kanteLaengeSetzen(RECHTECK, 3, 9000)).toBeNull();
  });

  it('lehnt sinnlose Längen ab, statt zu raten', () => {
    expect(kanteLaengeSetzen(RECHTECK, 0, 0)).toBeNull();
    expect(kanteLaengeSetzen(RECHTECK, 0, -100)).toBeNull();
  });
});

describe('Punkte bearbeiten', () => {
  it('verschiebt einen Punkt', () => {
    const neu = punktVerschieben(RECHTECK, 1, 100, -50);
    expect(neu[1]).toEqual(p(5100, -50));
    expect(neu[0]).toEqual(p(0, 0));
  });

  it('fügt einen Punkt in der Mitte einer Kante ein', () => {
    const neu = punktEinfuegen(RECHTECK, 0);
    expect(neu).toHaveLength(5);
    expect(neu[1]).toEqual(p(2500, 0));
    expect(flaecheM2(neu)).toBe(20); // ein Punkt auf der Geraden ändert nichts
  });

  it('fügt auch auf der schließenden Kante ein (zyklisch)', () => {
    const neu = punktEinfuegen(RECHTECK, 3);
    expect(neu).toHaveLength(5);
    expect(neu[4]).toEqual(p(0, 2000));
  });

  it('löscht einen Punkt', () => {
    const neu = punktLoeschen(RECHTECK, 2);
    expect(neu).toEqual([p(0, 0), p(5000, 0), p(0, 4000)]);
    expect(flaecheM2(neu)).toBe(10);
  });
});

describe('Selbstschnitt — was der Server mit 422 ablehnt', () => {
  it('erkennt zwei sich kreuzende Strecken', () => {
    expect(streckenSchneiden(p(0, 0), p(10, 10), p(0, 10), p(10, 0))).toBe(true);
  });

  it('erkennt parallele Strecken NICHT als Schnitt', () => {
    expect(streckenSchneiden(p(0, 0), p(10, 0), p(0, 5), p(10, 5))).toBe(false);
  });

  it('erkennt die Berührung in einem Punkt', () => {
    expect(streckenSchneiden(p(0, 0), p(10, 0), p(5, 0), p(5, 9))).toBe(true);
  });

  // ---- POSITIV: das überschlagene Polygon (die „Schleife") ----
  it('MELDET das überschlagene Polygon (Sanduhr)', () => {
    const sanduhr: Punkt[] = [p(0, 0), p(5000, 0), p(0, 4000), p(5000, 4000)];
    const b = pruefe(sanduhr);
    expect(b.some((x) => x.art === 'SELBSTSCHNITT')).toBe(true);
    const s = b.find((x) => x.art === 'SELBSTSCHNITT')!;
    // Es kreuzen sich Kante 1 (0→1 ist nicht dabei) — die Diagonalen 2 und 4.
    expect(s.kanten.length).toBeGreaterThanOrEqual(2);
    expect(s.text).toContain('überschlägt sich');
    expect(istGueltig(sanduhr)).toBe(false);
  });

  it('MELDET den Stachel (die Kante klappt auf sich selbst zurück)', () => {
    const stachel: Punkt[] = [p(0, 0), p(5000, 0), p(2000, 0), p(2000, 4000)];
    expect(pruefe(stachel).some((x) => x.art === 'SELBSTSCHNITT')).toBe(true);
  });

  // ---- NEGATIV: saubere Polygone dürfen NICHT gemeldet werden ----
  it('meldet das Rechteck NICHT (beide Umlaufsinne)', () => {
    expect(pruefe(RECHTECK)).toEqual([]);
    expect(pruefe(RECHTECK_UZS)).toEqual([]);
    expect(istGueltig(RECHTECK)).toBe(true);
  });

  it('meldet die L-Form NICHT — auch nicht ihre einspringende Ecke', () => {
    expect(pruefe(L_FORM)).toEqual([]);
    expect(istGueltig(L_FORM)).toBe(true);
  });

  it('meldet ein Polygon mit einem Punkt AUF einer Geraden NICHT', () => {
    const mitZwischenpunkt = punktEinfuegen(RECHTECK, 0);
    expect(pruefe(mitZwischenpunkt)).toEqual([]);
  });
});

describe('Weitere Befunde', () => {
  it('weniger als 3 Punkte', () => {
    expect(pruefe([]).map((b) => b.art)).toEqual(['ZU_WENIG']);
    expect(pruefe([p(0, 0), p(1000, 0)]).map((b) => b.art)).toEqual(['ZU_WENIG']);
  });

  it('doppelter Punkt (eine Kante ohne Länge)', () => {
    const doppelt: Punkt[] = [p(0, 0), p(5000, 0), p(5000, 0), p(0, 4000)];
    const b = pruefe(doppelt);
    expect(b.some((x) => x.art === 'DOPPELT')).toBe(true);
    expect(b.find((x) => x.art === 'DOPPELT')!.punkte).toEqual([1, 2]);
  });

  it('entartetes Polygon: alle Punkte auf einer Linie → Fläche 0', () => {
    const linie: Punkt[] = [p(0, 0), p(2000, 0), p(5000, 0)];
    const b = pruefe(linie);
    expect(b.some((x) => x.art === 'ENTARTET')).toBe(true);
    expect(flaecheM2(linie)).toBe(0);
  });
});

describe('Öffnung in ihrer Kante', () => {
  it('passt: Lage + Breite ≤ Kantenlänge', () => {
    expect(oeffnungPasst(1.2, 1.5, 5).art).toBe('passt');
    expect(oeffnungPasst(3.5, 1.5, 5).art).toBe('passt'); // exakt bündig
    expect(oeffnungPasst(0, 5, 5).art).toBe('passt');
  });

  it('passt NICHT: sie ragt über die Kante hinaus', () => {
    const r = oeffnungPasst(4, 1.5, 5);
    expect(r.art).toBe('passt_nicht');
    expect(r.art === 'passt_nicht' && r.grund).toContain('über die Kante hinaus');
  });

  it('eine negative Lage wird abgelehnt', () => {
    expect(oeffnungPasst(-0.5, 1, 5).art).toBe('passt_nicht');
  });

  // ---- Die Hausregel dieses Slices: unvollständig = unbekannt, nie 0 ----
  it('OHNE Lage ist sie UNBEKANNT — nicht „bei 0 m"', () => {
    const r = oeffnungPasst(null, 1.5, 5);
    expect(r.art).toBe('unbekannt');
    // Ausdrücklich NICHT dasselbe wie eine Lage von 0.
    expect(oeffnungPasst(0, 1.5, 5).art).toBe('passt');
  });

  it('OHNE Breite ist sie UNBEKANNT — nicht „passt" (die Breite ist nicht 0)', () => {
    // Lage getippt, Breite noch nicht: Die Frage ist nicht beantwortbar.
    // Eine Breite von 0 anzunehmen hieße „passt immer" zu melden.
    const r = oeffnungPasst(1.2, null, 5);
    expect(r.art).toBe('unbekannt');
    expect(r.art === 'unbekannt' && r.grund).toContain('Ohne Breite');
    // Eine echte 0 bleibt eine 0 — sie passt (und ist ein anderes Problem).
    expect(oeffnungPasst(1.2, 0, 5).art).toBe('passt');
  });

  it('setzt die Öffnung maßstäblich in ihre Kante', () => {
    const k = kanten(RECHTECK)[0]; // (0,0) → (5000,0)
    expect(punktAufKante(k, 1.2)).toEqual(p(1200, 0));
    const schraeg = kanten([p(0, 0), p(3000, 4000), p(0, 4000)])[0]; // 5 m lang
    expect(punktAufKante(schraeg, 2.5)).toEqual(p(1500, 2000));
  });
});

describe('Ansicht: Welt (mm) ⇄ SVG', () => {
  it('bildet hin und zurück ab (y wird gespiegelt: Welt zeigt nach oben)', () => {
    const s = sichtEinpassen(RECHTECK);
    const oben = zuSicht(p(0, 4000), s);
    const unten = zuSicht(p(0, 0), s);
    expect(oben.y).toBeLessThan(unten.y); // oben in der Welt = oben im Bild
    // Rückabbildung trifft den Punkt wieder (auf ganze mm gerundet).
    const zurueck = zuWelt(oben.x, oben.y, s);
    expect(Math.abs(zurueck.x_mm - 0)).toBeLessThanOrEqual(1);
    expect(Math.abs(zurueck.y_mm - 4000)).toBeLessThanOrEqual(1);
  });

  it('passt auch einen leeren Umriss ein (sonst hätte das Blatt keinen Maßstab)', () => {
    const s = sichtEinpassen([]);
    expect(s.skala).toBeGreaterThan(0);
  });

  /**
   * Die entarteten Fälle. Hier lief die Skala früher auf 560 Einheiten je
   * Millimeter — das Blatt zeigte 2 mm Welt, das Raster verschwand, und jeder
   * Klick snappte auf denselben Punkt zurück. Das Zeichnen war tot.
   */
  describe('entartete Bounding-Box (der Zoom darf nicht entgleisen)', () => {
    const brauchbar = (s: { skala: number }) => {
      expect(Number.isFinite(s.skala)).toBe(true);
      expect(s.skala).toBeGreaterThan(0);
      // Ein 25-cm-Raster muss auf dem Blatt sichtbar bleiben …
      expect(250 * s.skala).toBeGreaterThan(4);
      // … und 2 m Welt müssen aufs Blatt passen (Skala nach oben begrenzt).
      expect(2000 * s.skala).toBeLessThanOrEqual(1000);
    };

    it('EIN Punkt: es gibt nichts einzupassen — Standardfeld, um ihn zentriert', () => {
      const s = sichtEinpassen([p(1234, -5678)]);
      brauchbar(s);
      expect(s.skala).toBe(sichtEinpassen([]).skala); // dieselbe Zoomstufe wie leer
      expect(s.mitte_x).toBe(1234);
      expect(s.mitte_y).toBe(-5678);
    });

    it('zwei Punkte auf EINER ACHSE: die andere Ausdehnung ist 0 — trotzdem brauchbar', () => {
      brauchbar(sichtEinpassen([p(0, 0), p(5000, 0)])); // Höhe 0
      brauchbar(sichtEinpassen([p(0, 0), p(0, 5000)])); // Breite 0
    });

    it('drei kollineare Punkte (Fläche 0): ebenfalls brauchbar', () => {
      brauchbar(sichtEinpassen([p(0, 0), p(2000, 0), p(5000, 0)]));
    });

    it('zwei Punkte SEHR nah beieinander laufen nicht in eine sinnlose Zoomstufe', () => {
      brauchbar(sichtEinpassen([p(0, 0), p(1, 1)])); // 1 mm auseinander
    });

    it('ein echter Umriss wird weiterhin normal eingepasst', () => {
      const s = sichtEinpassen(RECHTECK);
      brauchbar(s);
      expect(s.mitte_x).toBe(2500);
      expect(s.mitte_y).toBe(2000);
      // Der Umriss füllt das Blatt bis auf den Rand. Begrenzend ist beim 5 × 4-Raum
      // die HÖHE (560/4000 < 860/5000) — sie stößt exakt an den Rand, die Breite
      // bleibt darunter.
      expect(4000 * s.skala).toBeCloseTo(560, 0);
      expect(5000 * s.skala).toBeLessThanOrEqual(860);
    });
  });
});

describe('Nordpfeil — abgeleitet, nicht erfunden', () => {
  it('leitet Norden aus den Ausrichtungen der Wände ab', () => {
    // Rechteck gegen den UZS: Kante 0 (unten) zeigt nach außen = Süd,
    // Kante 2 (oben) = Nord. Sind sie so beschriftet, zeigt Norden nach oben.
    const n = nordRichtung(RECHTECK, ['S', 'O', 'N', 'W']);
    expect(n.art).toBe('richtung');
    if (n.art === 'richtung') {
      expect(n.x).toBeCloseTo(0, 5);
      expect(n.y).toBeCloseTo(1, 5); // Welt-y nach oben
    }
  });

  it('funktioniert auch bei umgekehrtem Umlaufsinn', () => {
    const n = nordRichtung(RECHTECK_UZS, ['W', 'N', 'O', 'S']);
    expect(n.art).toBe('richtung');
    if (n.art === 'richtung') expect(n.y).toBeCloseTo(1, 5);
  });

  it('gedrehter Raum: Norden dreht mit', () => {
    // Rechteck 90° gedreht — Kante 0 verläuft jetzt nach oben, außen = Ost.
    const gedreht: Punkt[] = [p(0, 0), p(0, 5000), p(-4000, 5000), p(-4000, 0)];
    const n = nordRichtung(gedreht, ['O', 'N', 'W', 'S']);
    expect(n.art).toBe('richtung');
    if (n.art === 'richtung') {
      expect(n.x).toBeCloseTo(0, 5);
      expect(n.y).toBeCloseTo(1, 5);
    }
  });

  it('ohne Ausrichtung: unbekannt — es wird KEIN Norden erfunden', () => {
    expect(nordRichtung(RECHTECK, [null, null, null, null]).art).toBe('unbekannt');
  });

  it('widersprüchliche Angaben: KEIN Pfeil', () => {
    // Zwei gegenüberliegende Wände, beide als „Nord" bezeichnet — das kann nicht sein.
    expect(nordRichtung(RECHTECK, ['N', null, 'N', null]).art).toBe('widerspruch');
  });
});

describe('Textbeschreibung (das barrierefreie Äquivalent zur Zeichnung)', () => {
  it('fasst den Umriss in einem Satz zusammen', () => {
    const t = beschreibung(RECHTECK, { waende: 2, oeffnungen: 2, ohneLage: 1 });
    expect(t).toContain('4 Kanten');
    expect(t).toContain('5,00 m × 4,00 m');
    expect(t).toContain('20,00 m² Fläche');
    expect(t).toContain('18,00 m Umfang');
    expect(t).toContain('2 von 4 Kanten');
    expect(t).toContain('1 Öffnungen ohne Lage');
  });

  it('sagt ehrlich, wenn noch nichts da ist', () => {
    expect(beschreibung([])).toContain('kein Umriss');
    expect(beschreibung([p(0, 0), p(1, 0)])).toContain('mindestens 3');
  });
});
