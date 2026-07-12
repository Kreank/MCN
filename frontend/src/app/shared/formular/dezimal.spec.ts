import { FormControl } from '@angular/forms';
import {
  DEZIMAL_UNGUELTIG,
  apiZuDeAnzeige,
  apiZuDeEingabe,
  deZuApiDezimal,
  dezimalValidator,
  istDezimalApiWert,
  istMehrdeutigeDezimalEingabe,
} from './dezimal';

describe('deZuApiDezimal', () => {
  it('wandelt deutsches Format in Punkt-String', () => {
    expect(deZuApiDezimal('1.234,56')).toBe('1234.56');
    expect(deZuApiDezimal('1234,5')).toBe('1234.5');
    expect(deZuApiDezimal('1.000.000,00')).toBe('1000000.00');
  });

  it('lässt Punkt-Dezimal und Ganzzahlen unverändert', () => {
    expect(deZuApiDezimal('1234.56')).toBe('1234.56');
    expect(deZuApiDezimal('42')).toBe('42');
    expect(deZuApiDezimal('1200')).toBe('1200');
    // Führende Null: als Tausendertrennung unmöglich → eindeutig 0,5.
    expect(deZuApiDezimal('0.500')).toBe('0.500');
  });

  it('lehnt mehrdeutige Punktgruppen ab, statt sie zu raten', () => {
    // „1.500" könnte 1500 (deutsche Tausendertrennung) oder 1,5 (Punkt-Dezimal)
    // sein. Früher wurde still 1,5 daraus — Faktor 1000 Datenverlust.
    expect(deZuApiDezimal('1.500')).toBe(DEZIMAL_UNGUELTIG);
    expect(deZuApiDezimal('12.345')).toBe(DEZIMAL_UNGUELTIG);
    expect(deZuApiDezimal('1.000.000')).toBe(DEZIMAL_UNGUELTIG);
    expect(deZuApiDezimal('-1.500')).toBe(DEZIMAL_UNGUELTIG);
    expect(istDezimalApiWert(deZuApiDezimal('1.500'))).toBe(false);
  });

  it('lehnt Unlesbares mit dem Sentinel ab', () => {
    expect(deZuApiDezimal('abc')).toBe(DEZIMAL_UNGUELTIG);
    expect(deZuApiDezimal('1,2,3')).toBe(DEZIMAL_UNGUELTIG);
    expect(deZuApiDezimal('1..2')).toBe(DEZIMAL_UNGUELTIG);
    expect(deZuApiDezimal('30 Tage')).toBe(DEZIMAL_UNGUELTIG);
  });

  it('behandelt leer/null als leeren String', () => {
    expect(deZuApiDezimal('')).toBe('');
    expect(deZuApiDezimal(null)).toBe('');
    expect(deZuApiDezimal(undefined)).toBe('');
    expect(deZuApiDezimal('  ')).toBe('');
    expect(istDezimalApiWert('')).toBe(true);
  });
});

describe('apiZuDeEingabe (Eingabefelder)', () => {
  it('gruppiert NICHT — sonst wird der Wert beim Zurücklesen mehrdeutig', () => {
    expect(apiZuDeEingabe('1200')).toBe('1200');
    expect(apiZuDeEingabe('1234.56', 2)).toBe('1234,56');
    expect(apiZuDeEingabe('1000000', 2)).toBe('1000000,00');
  });

  it('ist verlustfrei über den Roundtrip Anzeige → Eingabe → API', () => {
    for (const wert of ['1200', '1500', '1000000', '12.5', '0.5']) {
      expect(deZuApiDezimal(apiZuDeEingabe(wert))).toBe(String(Number(wert)));
    }
    // Der konkrete Bug-Fall: Menge 1200, Nutzer ändert auf 1500.
    expect(apiZuDeEingabe('1200')).toBe('1200');
    expect(deZuApiDezimal('1500')).toBe('1500');
  });

  it('gibt bei leer einen leeren String zurück', () => {
    expect(apiZuDeEingabe('')).toBe('');
    expect(apiZuDeEingabe(null)).toBe('');
  });
});

describe('apiZuDeAnzeige (reine Anzeige)', () => {
  it('formatiert Punkt-String mit Tausenderpunkt', () => {
    expect(apiZuDeAnzeige('1234.56', 2)).toBe('1.234,56');
    expect(apiZuDeAnzeige('1000000', 2)).toBe('1.000.000,00');
  });

  it('gibt bei leer einen leeren String zurück', () => {
    expect(apiZuDeAnzeige('')).toBe('');
    expect(apiZuDeAnzeige(null)).toBe('');
  });
});

describe('dezimalValidator', () => {
  const val = (v: string) => dezimalValidator(new FormControl(v));

  it('akzeptiert leere Eingabe (Pflicht separat)', () => {
    expect(val('')).toBeNull();
  });

  it('akzeptiert deutsches und Punkt-Format', () => {
    expect(val('1.234,56')).toBeNull();
    expect(val('1234.56')).toBeNull();
    expect(val('42')).toBeNull();
    expect(val('1200')).toBeNull();
    expect(val('-3,5')).toBeNull();
  });

  it('markiert mehrdeutige Eingaben mit eigenem Fehlerschlüssel', () => {
    expect(val('1.500')).toEqual({ dezimalMehrdeutig: true });
    expect(val('1.000.000')).toEqual({ dezimalMehrdeutig: true });
    expect(istMehrdeutigeDezimalEingabe('1.500')).toBe(true);
    expect(istMehrdeutigeDezimalEingabe('1500')).toBe(false);
    expect(istMehrdeutigeDezimalEingabe('1,5')).toBe(false);
  });

  it('lehnt Unsinn ab', () => {
    expect(val('abc')).toEqual({ dezimal: true });
    expect(val('1,2,3')).toEqual({ dezimal: true });
    expect(val('1..2')).toEqual({ dezimal: true });
  });
});
