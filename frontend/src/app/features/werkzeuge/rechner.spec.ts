import {
  DELTA_NORM,
  FBH_TYPEN,
  GEBAEUDE_TYPEN,
  KATEGORIEN,
  MAG_NENNGROESSEN,
  MAG_TEMPERATUREN,
  MESS_ARTEN,
  MessArt,
  ROHR_TYPEN,
  TeilmassEingabe,
  aufmass,
  ausdehnungsgefaess,
  deltaLogarithmisch,
  gebindeAus,
  heizkoerper,
  heizlast,
  mengeApi,
  umrechnen,
  volumenstrom,
  wasserinhalt,
  zahlEine,
  zahlGanz,
  zahlKurz,
  zahlLiter,
  zahlMenge,
  zahlZwei,
} from './rechner';

/**
 * Gegenrechnung zur Windows-App (`NotizApp_Win`, C#/WPF). Die erwarteten Werte
 * sind aus dem C#-Code von Hand nachvollzogen — sie sichern ab, dass der Port
 * dieselben Zahlen liefert wie das Werkzeug, das der Anwender bisher benutzt.
 */
describe('Portierung aus NotizApp_Win', () => {
  describe('Heizlast — flaeche * kennwert (HeizlastRechner.Berechne)', () => {
    it('Beispiel 1: 120 m², Altbau nicht gedämmt (120 W/m²) → 14.400 W / 14,4 kW', () => {
      const e = heizlast(120, 120)!;
      expect(e.watt).toBe(14400);
      expect(zahlGanz(e.watt)).toBe('14.400'); // C# "#,##0"
      expect(zahlEine(e.kw)).toBe('14,4'); // C# "0.0"
    });

    it('Beispiel 2: 85,5 m², Neubau (80 W/m²) → 6.840 W / 6,8 kW', () => {
      const e = heizlast(85.5, 80)!;
      expect(e.watt).toBeCloseTo(6840, 6);
      expect(zahlGanz(e.watt)).toBe('6.840');
      expect(zahlEine(e.kw)).toBe('6,8');
    });

    it('Beispiel 3: 24 m² Raum, überschriebener Kennwert 150 W/m² → 3.600 W / 3,6 kW', () => {
      const e = heizlast(24, 150)!;
      expect(e.watt).toBe(3600);
      expect(zahlGanz(e.watt)).toBe('3.600');
      expect(zahlEine(e.kw)).toBe('3,6');
    });

    it('lehnt Fläche oder Kennwert <= 0 ab (NotizApp zeigt dann „—")', () => {
      expect(heizlast(0, 120)).toBeNull();
      expect(heizlast(120, 0)).toBeNull();
      expect(heizlast(-10, 120)).toBeNull();
    });

    it('trägt exakt die vier Kennwerte der NotizApp-ComboBox', () => {
      expect(GEBAEUDE_TYPEN.map((t) => t.kennwert)).toEqual(['80', '100', '120', '150']);
    });
  });

  describe('Volumenstrom — kW*1000 / (1,163 * ΔT) (VolumenstromRechner.Berechne)', () => {
    it('Beispiel 1: 12 kW bei ΔT 20 K → 515,9 l/h → „516 l/h" / „0,52 m³/h"', () => {
      const e = volumenstrom(12, 20)!;
      expect(e.lh).toBeCloseTo(515.9071, 3); // 12000 / (1,163 * 20)
      expect(zahlGanz(e.lh)).toBe('516');
      expect(zahlZwei(e.m3h)).toBe('0,52');
    });

    it('Beispiel 2: 8 kW bei ΔT 7 K (FBH) → 982,7 l/h → „983 l/h" / „0,98 m³/h"', () => {
      const e = volumenstrom(8, 7)!;
      expect(e.lh).toBeCloseTo(982.6803, 3); // 8000 / (1,163 * 7) = 8000 / 8,141
      expect(zahlGanz(e.lh)).toBe('983');
      expect(zahlZwei(e.m3h)).toBe('0,98');
    });

    it('Beispiel 3: 24,5 kW bei ΔT 10 K → 2.106,6 l/h → „2.107 l/h" / „2,11 m³/h"', () => {
      const e = volumenstrom(24.5, 10)!;
      expect(e.lh).toBeCloseTo(2106.6208, 3); // 24500 / (1,163 * 10)
      expect(zahlGanz(e.lh)).toBe('2.107');
      expect(zahlZwei(e.m3h)).toBe('2,11');
    });

    it('lehnt Leistung oder Spreizung <= 0 ab', () => {
      expect(volumenstrom(0, 20)).toBeNull();
      expect(volumenstrom(12, 0)).toBeNull();
    });
  });

  describe('Einheiten-Umrechner (EinheitenUmrechner.Umrechnen)', () => {
    const kat = (name: string) => KATEGORIEN.find((k) => k.name === name)!;

    it('Leistung: 12 kW → 12.000 W, 10.318,1427 kcal/h, 0,012 MW', () => {
      const z = umrechnen(kat('Leistung'), 0, 12); // von kW
      expect(z.map((x) => x.wert)).toEqual(['12', '12.000', '10.318,1427', '0,012']);
      expect(z[0].istQuelle).toBe(true);
    });

    it('Druck: 2,5 bar → 25,4929 mWS', () => {
      const z = umrechnen(kat('Druck'), 0, 2.5); // von bar
      const mws = z.find((x) => x.name === 'mWS')!;
      expect(mws.wert).toBe('25,4929'); // 250000 / 9806,65
    });

    it('Temperatur: 60 °C → 333,15 K / 140 °F (Offset-Weg, C#-Muster "0.##")', () => {
      const z = umrechnen(kat('Temperatur'), 0, 60);
      expect(z.map((x) => x.wert)).toEqual(['60', '333,15', '140']);
    });

    it('Volumenstrom: 15 l/min → 0,9 m³/h / 900 l/h', () => {
      const z = umrechnen(kat('Volumenstrom'), 2, 15); // von l/min
      expect(z.find((x) => x.name === 'l/h')!.wert).toBe('900');
      expect(z.find((x) => x.name === 'm³/h')!.wert).toBe('0,9');
    });

    it('Wasserhärte (neu): 14 °dH → 2,4966 mmol/l und 249,8 ppm CaCO₃', () => {
      const z = umrechnen(kat('Wasserhärte'), 0, 14); // von °dH
      expect(Number(z.find((x) => x.name === 'mmol/l')!.wert.replace(',', '.'))).toBeCloseTo(
        2.4966,
        3,
      );
      // 1 °dH ≈ 17,848 ppm CaCO₃ → 14 °dH ≈ 249,9 ppm
      const ppm = Number(
        z
          .find((x) => x.name.startsWith('ppm'))!
          .wert.replace(/\./g, '')
          .replace(',', '.'),
      );
      expect(ppm).toBeCloseTo(249.87, 1);
    });

    it('Länge (neu): 1 Zoll = 25,4 mm', () => {
      const z = umrechnen(kat('Länge / Zoll'), 3, 1); // von Zoll
      expect(z.find((x) => x.name === 'mm')!.wert).toBe('25,4');
    });
  });

  // ==========================================================================
  // Wasserinhalt (WasserinhaltRechner.Berechne)
  //   Summe = Rohr(L × l/m) + FBH(L × l/m) + HK(Anzahl × Inhalt) + Erz + Puffer
  // ==========================================================================
  describe('Wasserinhalt', () => {
    const leer = {
      rohrLaenge: null,
      rohrLProM: 0.314,
      fbhLaenge: null,
      fbhLProM: 0.113,
      hkAnzahl: null,
      hkInhalt: null,
      erzeuger: null,
      puffer: null,
    };

    it('trägt exakt die 11 Rohr- und 3 FBH-Kennwerte der NotizApp', () => {
      expect(ROHR_TYPEN.map((r) => r.lProM)).toEqual([
        0.133, 0.201, 0.314, 0.491, 0.804, 0.113, 0.201, 0.314, 0.201, 0.366, 0.581,
      ]);
      expect(FBH_TYPEN.map((r) => r.lProM)).toEqual([0.113, 0.133, 0.201]);
    });

    it('Beispiel 1: 60 m Kupfer 22×1 + 8 HK à 5 l + 12 l Erzeuger → 70,8 Liter', () => {
      // 60 × 0,314 = 18,84 · 8 × 5 = 40 · + 12  ⇒ 70,84
      const e = wasserinhalt({
        ...leer,
        rohrLaenge: 60,
        hkAnzahl: 8,
        hkInhalt: 5,
        erzeuger: 12,
      })!;
      expect(e.summe).toBeCloseTo(70.84, 6);
      expect(zahlLiter(e.summe)).toBe('70,8'); // C# "#,##0.#"
      expect(e.teile.map((t) => `${t.label} ${zahlLiter(t.liter)} l`)).toEqual([
        'Rohr 18,8 l',
        'Heizkörper 40 l',
        'Erzeuger 12 l',
      ]);
    });

    it('Beispiel 2: 25 m Verbund 20×2 + 420 m FBH 16×2 + 8 l Erzeuger + 200 l Puffer → 260,5 l', () => {
      // 25 × 0,201 = 5,025 · 420 × 0,113 = 47,46 · + 8 + 200 ⇒ 260,485
      const e = wasserinhalt({
        ...leer,
        rohrLaenge: 25,
        rohrLProM: 0.201,
        fbhLaenge: 420,
        fbhLProM: 0.113,
        erzeuger: 8,
        puffer: 200,
      })!;
      expect(e.summe).toBeCloseTo(260.485, 6);
      expect(zahlLiter(e.summe)).toBe('260,5');
      expect(e.teile.map((t) => t.label)).toEqual(['Rohr', 'FBH', 'Erzeuger', 'Puffer']);
    });

    it('leere Anlage → kein Ergebnis (NotizApp zeigt „—")', () => {
      expect(wasserinhalt(leer)).toBeNull();
    });

    it('negative/unlesbare Felder zählen als 0 — genau wie `Wert(TextBox)` in C#', () => {
      const e = wasserinhalt({ ...leer, rohrLaenge: -50, erzeuger: 12 })!;
      expect(e.summe).toBe(12);
      expect(e.teile.map((t) => t.label)).toEqual(['Erzeuger']);
    });
  });

  // ==========================================================================
  // Ausdehnungsgefäß (AusdehnungsgefaessRechner.Berechne)
  //   V_n = (V_e + V_wv) · (p_e + 1) / (p_e − p_0)
  // ==========================================================================
  describe('Ausdehnungsgefäß (MAG)', () => {
    const beta = (t: string) => MAG_TEMPERATUREN.find((x) => x.wert === t)!.beta;

    it('trägt exakt die Koeffizienten und Nenngrößen der NotizApp', () => {
      expect(MAG_TEMPERATUREN.map((t) => t.beta)).toEqual([0.0121, 0.0171, 0.0228, 0.0289, 0.0359]);
      expect(MAG_NENNGROESSEN).toEqual([
        8, 12, 18, 25, 35, 50, 80, 100, 140, 200, 250, 300, 400, 500, 600, 800, 1000,
      ]);
    });

    it('Beispiel 1: 150 l, 70 °C, 5 m, SV 3,0 bar → nötig 13,22 l → 18-l-Gefäß', () => {
      // V_e = 150 × 0,0228 = 3,42 · V_wv = max(0,75; 3) = 3
      // p_0 = 5/10 + 0,3 = 0,8 · p_e = 3,0 − 0,5 = 2,5
      // V_n = (3,42 + 3) × 3,5 / 1,7 = 22,47 / 1,7 = 13,2176
      const r = ausdehnungsgefaess(150, beta('70'), 3.0, 5);
      expect(r.ok).toBe(true);
      if (!r.ok) return;
      expect(r.ergebnis.ve).toBeCloseTo(3.42, 6);
      expect(r.ergebnis.vwv).toBe(3);
      expect(r.ergebnis.p0).toBeCloseTo(0.8, 6);
      expect(r.ergebnis.pe).toBeCloseTo(2.5, 6);
      expect(r.ergebnis.vn).toBeCloseTo(13.2176, 3);
      expect(zahlKurz(r.ergebnis.vn)).toBe('13,22'); // C# "0.##"
      expect(r.ergebnis.empfohlen).toBe(18);
    });

    it('Beispiel 2: 300 l, 80 °C, 10 m, SV 2,5 bar → nötig 50,01 l → 80 l (50 reicht NICHT)', () => {
      // V_e = 8,67 · V_wv = 3 · p_0 = 1,3 · p_e = 2,0
      // V_n = 11,67 × 3,0 / 0,7 = 50,0143  ⇒ die 50er-Größe liegt darunter
      const r = ausdehnungsgefaess(300, beta('80'), 2.5, 10);
      expect(r.ok).toBe(true);
      if (!r.ok) return;
      expect(r.ergebnis.vn).toBeCloseTo(50.0143, 3);
      expect(zahlKurz(r.ergebnis.vn)).toBe('50,01');
      expect(r.ergebnis.empfohlen).toBe(80);
    });

    it('Wasservorlage ist mindestens 3 l (0,5 % erst ab 600 l Anlageninhalt maßgeblich)', () => {
      const klein = ausdehnungsgefaess(200, beta('70'), 3.0, 5);
      const gross = ausdehnungsgefaess(1000, beta('70'), 3.0, 5);
      expect(klein.ok && klein.ergebnis.vwv).toBe(3); // 0,005 × 200 = 1 → 3
      expect(gross.ok && gross.ergebnis.vwv).toBe(5); // 0,005 × 1000 = 5
    });

    it('Enddruck ≤ Vordruck (+0,1 bar) → kein Ergebnis, sondern Fehler', () => {
      // h = 20 m → p_0 = 2,3 bar; SV 2,5 → p_e = 2,0 bar
      expect(ausdehnungsgefaess(150, beta('70'), 2.5, 20)).toEqual({ ok: false, fehler: 'DRUCK' });
      // Grenze exakt 0,1 bar: p_0 = 1,9 (h = 16 m), p_e = 2,0 → abgelehnt
      expect(ausdehnungsgefaess(150, beta('70'), 2.5, 16)).toEqual({ ok: false, fehler: 'DRUCK' });
    });

    it('Anlageninhalt fehlt oder ≤ 0 → kein Ergebnis', () => {
      expect(ausdehnungsgefaess(null, beta('70'), 3.0, 5)).toEqual({ ok: false, fehler: 'INHALT' });
      expect(ausdehnungsgefaess(0, beta('70'), 3.0, 5)).toEqual({ ok: false, fehler: 'INHALT' });
    });

    it('über der größten Nenngröße → empfohlen = null (Sonderauslegung)', () => {
      // 12.000 l, 90 °C: V_n = (430,8 + 60) × 3,5 / 1,7 = 1.010,47 l > 1.000
      const r = ausdehnungsgefaess(12000, beta('90'), 3.0, 5);
      expect(r.ok).toBe(true);
      if (!r.ok) return;
      expect(r.ergebnis.vn).toBeCloseTo(1010.47, 1);
      expect(r.ergebnis.empfohlen).toBeNull();
    });

    it('fehlende statische Höhe zählt als 0 m (wie das unlesbare Feld in C#)', () => {
      const r = ausdehnungsgefaess(150, beta('70'), 3.0, null);
      expect(r.ok && r.ergebnis.p0).toBeCloseTo(0.3, 6);
    });
  });
});

/**
 * Aufmaß / Mengenermittlung — neu gebaut (nicht in der NotizApp). Der einzige
 * Rechner, dessen Zahl in eine Belegposition wandert: als MENGE (String), nie
 * als Betrag.
 */
describe('Aufmaß mit Verschnitt', () => {
  const art = (a: MessArt) => MESS_ARTEN.find((m) => m.wert === a)!;
  const teil = (t: Partial<TeilmassEingabe>): TeilmassEingabe => ({
    bezeichnung: '',
    anzahl: null,
    laenge: null,
    breite: null,
    hoehe: null,
    abzug: false,
    ...t,
  });

  it('Fliesen Bad: 3 Wände − Fenster − Tür, 10 % Verschnitt, Karton à 1,44 m²', () => {
    // 4 × 2,6 = 10,4 | 4 × 2,6 = 10,4 | 2 × 2,5 × 2,6 = 13
    // Abzug 1,2 × 1,4 = 1,68 | Abzug 0,9 × 2,0 = 1,8
    // netto = 30,32 · +10 % = 33,352 · /1,44 = 23,16 → 24 Kartons = 34,56 m²
    const e = aufmass(
      art('FLAECHE'),
      [
        teil({ bezeichnung: 'Wand Nord', laenge: 4, breite: 2.6 }),
        teil({ bezeichnung: 'Wand Süd', laenge: 4, breite: 2.6 }),
        teil({ bezeichnung: 'Wand Ost + West', anzahl: 2, laenge: 2.5, breite: 2.6 }),
        teil({ bezeichnung: 'Fenster', laenge: 1.2, breite: 1.4, abzug: true }),
        teil({ bezeichnung: 'Tür', laenge: 0.9, breite: 2.0, abzug: true }),
      ],
      10,
      1.44,
    )!;
    expect(e.einheit).toBe('m²');
    expect(e.netto).toBe(30.32);
    expect(e.verschnittMenge).toBe(3.032);
    expect(e.brutto).toBe(33.352);
    expect(e.gebindeAnzahl).toBe(24);
    expect(e.bestellmenge).toBe(34.56);
    expect(e.unvollstaendig).toBe(0);
    expect(mengeApi(e.bestellmenge)).toBe('34.56'); // API-String, Punkt, nie Komma
    expect(zahlMenge(e.bestellmenge)).toBe('34,56'); // Anzeige
    // Rechenweg je Teilmaß — er landet so in der Positionsbeschreibung.
    expect(e.teile[0].rechenweg).toBe('4 m × 2,6 m = 10,4 m²');
    expect(e.teile[2].rechenweg).toBe('2 × 2,5 m × 2,6 m = 13 m²');
    expect(e.teile[3].abzug).toBe(true);
  });

  it('Gebinde: exakt aufgehende Menge wird NICHT hochgerundet (Fließkomma-Toleranz)', () => {
    // 3 × 1,44 = 4,32 → in JS 4,32/1,44 = 3,0000000000000004 → ohne Toleranz 4 Gebinde
    const e = aufmass(art('FLAECHE'), [teil({ laenge: 4.32, breite: 1 })], 0, 1.44)!;
    expect(e.brutto).toBe(4.32);
    expect(e.gebindeAnzahl).toBe(3);
    expect(e.bestellmenge).toBe(4.32);
  });

  it('ohne Gebinde bleibt die Bruttomenge die Bestellmenge', () => {
    const e = aufmass(
      art('LAENGE'),
      [teil({ bezeichnung: 'Steigstrang', laenge: 12.5 })],
      5,
      null,
    )!;
    expect(e.netto).toBe(12.5);
    expect(e.brutto).toBe(13.125);
    expect(e.gebindeAnzahl).toBeNull();
    expect(e.bestellmenge).toBe(13.125);
    expect(mengeApi(e.bestellmenge)).toBe('13.125');
  });

  it('Stückzahl: Anzahl ist das Maß; Gebinde rundet auf volle VE auf', () => {
    const e = aufmass(art('STUECK'), [teil({ bezeichnung: 'Ventile', anzahl: 12 })], 0, 5)!;
    expect(e.einheit).toBe('Stk');
    expect(e.netto).toBe(12);
    expect(e.gebindeAnzahl).toBe(3);
    expect(e.bestellmenge).toBe(15);
    expect(e.teile[0].rechenweg).toBe('12 Stk');
  });

  it('Volumen: L × B × H (Estrich)', () => {
    const e = aufmass(art('VOLUMEN'), [teil({ laenge: 5, breite: 4, hoehe: 0.07 })], 0, null)!;
    expect(e.netto).toBe(1.4);
    expect(e.einheit).toBe('m³');
  });

  it('Anzahl leer = 1 (nur bei STUECK ist sie Pflicht)', () => {
    const e = aufmass(art('FLAECHE'), [teil({ laenge: 3, breite: 2 })], 0, null)!;
    expect(e.netto).toBe(6);
    const s = aufmass(art('STUECK'), [teil({ bezeichnung: 'Auslässe' })], 0, null);
    expect(s).toBeNull(); // Anzahl fehlt → unvollständig → keine Menge
  });

  it('unvollständiges Teilmaß zählt NICHT mit und wird gemeldet (nie still verschlucken)', () => {
    const e = aufmass(
      art('FLAECHE'),
      [
        teil({ bezeichnung: 'Wand', laenge: 4, breite: 2.5 }),
        teil({ bezeichnung: 'Decke', laenge: 4 }), // Breite fehlt
      ],
      0,
      null,
    )!;
    expect(e.netto).toBe(10);
    expect(e.unvollstaendig).toBe(1);
    expect(e.teile[1].status).toBe('UNVOLLSTAENDIG');
  });

  it('leere Zeile wird stillschweigend übergangen (kein Fehler)', () => {
    const e = aufmass(art('FLAECHE'), [teil({ laenge: 4, breite: 2.5 }), teil({})], 0, null)!;
    expect(e.unvollstaendig).toBe(0);
    expect(e.teile[1].status).toBe('LEER');
  });

  it('Nettomenge ≤ 0 → kein Ergebnis (die DB verlangt quantity > 0)', () => {
    expect(
      aufmass(art('FLAECHE'), [teil({ laenge: 2, breite: 1, abzug: true })], 10, null),
    ).toBeNull();
    expect(
      aufmass(
        art('FLAECHE'),
        [teil({ laenge: 2, breite: 1 }), teil({ laenge: 2, breite: 1, abzug: true })],
        10,
        null,
      ),
    ).toBeNull();
    expect(aufmass(art('FLAECHE'), [], 10, null)).toBeNull();
  });

  it('negativer Verschnitt liefert kein Ergebnis (kein stiller Abschlag)', () => {
    expect(aufmass(art('FLAECHE'), [teil({ laenge: 2, breite: 1 })], -5, null)).toBeNull();
  });

  it('ungültige Gebindegröße wird NICHT still als „keine Gebindegröße" gewertet', () => {
    // Leer ist gültig: keine Aufrundung.
    expect(gebindeAus('')).toEqual({ gebinde: null, ungueltig: false });
    expect(gebindeAus('   ')).toEqual({ gebinde: null, ungueltig: false });
    expect(gebindeAus(null)).toEqual({ gebinde: null, ungueltig: false });
    // Gültige Größe (deutsche Komma-Eingabe).
    expect(gebindeAus('1,44')).toEqual({ gebinde: 1.44, ungueltig: false });
    // Unlesbar, mehrdeutig („1.500") oder nicht positiv → FEHLER, nicht „keine VE".
    // Sonst bliebe die Aufrundung auf volle Kartons aus und es würde zu wenig
    // bestellt, ohne dass es jemand merkt.
    expect(gebindeAus('abc').ungueltig).toBe(true);
    expect(gebindeAus('1.500').ungueltig).toBe(true);
    expect(gebindeAus('0').ungueltig).toBe(true);
    expect(gebindeAus('-2').ungueltig).toBe(true);
    for (const roh of ['abc', '1.500', '0', '-2']) {
      expect(gebindeAus(roh).gebinde).toBeNull();
    }
  });

  it('mengeApi liefert einen API-Dezimalstring: Punkt, max. 3 Stellen, keine Gruppierung', () => {
    expect(mengeApi(100)).toBe('100');
    expect(mengeApi(1234.5)).toBe('1234.5');
    expect(mengeApi(12.3456)).toBe('12.346');
    expect(mengeApi(0.5)).toBe('0.5');
    expect(mengeApi(34.56)).toBe('34.56');
    expect(mengeApi(1000)).toBe('1000'); // nicht „1" — Nullen nur nach dem Punkt kappen
  });
});

/**
 * Heizkörper-Umrechnung — nicht aus der NotizApp portiert, neu gebaut.
 * Q = Q_norm * (ΔΘ_ln / 49,83)^n
 */
describe('Heizkörper-Umrechnung', () => {
  it('Normbedingung 75/65/20 ergibt ΔΘ_ln = 49,83 K', () => {
    const d = deltaLogarithmisch(75, 65, 20);
    expect(d.ok).toBe(true);
    expect((d as { ok: true; wert: number }).wert).toBeCloseTo(49.83, 2);
    expect(DELTA_NORM).toBe(49.83);
  });

  it('75/65/20 → Faktor 1: die Normleistung bleibt die Normleistung', () => {
    const r = heizkoerper(1000, 75, 65, 20, 1.3);
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.ergebnis.faktor).toBeCloseTo(1, 3);
  });

  it('1.500 W bei 55/45/20, n = 1,3 → 766 W (≈ 51 % der Normleistung)', () => {
    // ΔΘ_ln = 10 / ln(35/25) = 29,7201 K
    // Faktor = (29,7201 / 49,83)^1,3 = 0,51077
    const r = heizkoerper(1500, 55, 45, 20, 1.3);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.ergebnis.deltaLn).toBeCloseTo(29.7201, 3);
    expect(r.ergebnis.faktor).toBeCloseTo(0.5108, 3);
    expect(zahlGanz(r.ergebnis.watt)).toBe('766');
    expect(zahlKurz(r.ergebnis.faktor * 100)).toBe('51,08');
  });

  it('Grenzfall Vorlauf == Rücklauf: Grenzwert statt Division durch null', () => {
    const d = deltaLogarithmisch(50, 50, 20);
    expect(d.ok).toBe(true);
    if (d.ok) expect(d.wert).toBe(30);
  });

  it('lehnt physikalisch unmögliche Betriebspunkte ab (kein NaN im UI)', () => {
    expect(heizkoerper(1000, 18, 16, 20, 1.3)).toEqual({ ok: false, fehler: 'VL_UNTER_RAUM' });
    expect(heizkoerper(1000, 40, 18, 20, 1.3)).toEqual({ ok: false, fehler: 'RL_UNTER_RAUM' });
    expect(heizkoerper(1000, 45, 55, 20, 1.3)).toEqual({ ok: false, fehler: 'VL_KLEINER_RL' });
    expect(heizkoerper(0, 55, 45, 20, 1.3)).toEqual({ ok: false, fehler: 'EINGABE' });
  });
});
