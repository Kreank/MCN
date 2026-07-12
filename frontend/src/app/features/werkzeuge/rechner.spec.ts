import {
  DELTA_NORM,
  GEBAEUDE_TYPEN,
  KATEGORIEN,
  deltaLogarithmisch,
  heizkoerper,
  heizlast,
  umrechnen,
  volumenstrom,
  zahlEine,
  zahlGanz,
  zahlKurz,
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
