import { FormControl } from '@angular/forms';
import { apiZuDeDezimal, deZuApiDezimal, dezimalValidator } from './dezimal';

describe('deZuApiDezimal', () => {
  it('wandelt deutsches Format in Punkt-String', () => {
    expect(deZuApiDezimal('1.234,56')).toBe('1234.56');
    expect(deZuApiDezimal('1234,5')).toBe('1234.5');
    expect(deZuApiDezimal('1.000.000,00')).toBe('1000000.00');
  });

  it('lässt Punkt-Dezimal und Ganzzahlen unverändert', () => {
    expect(deZuApiDezimal('1234.56')).toBe('1234.56');
    expect(deZuApiDezimal('42')).toBe('42');
  });

  it('behandelt leer/null als leeren String', () => {
    expect(deZuApiDezimal('')).toBe('');
    expect(deZuApiDezimal(null)).toBe('');
    expect(deZuApiDezimal(undefined)).toBe('');
    expect(deZuApiDezimal('  ')).toBe('');
  });
});

describe('apiZuDeDezimal', () => {
  it('formatiert Punkt-String in deutsche Anzeige', () => {
    expect(apiZuDeDezimal('1234.56', 2)).toBe('1.234,56');
    expect(apiZuDeDezimal('1000000', 2)).toBe('1.000.000,00');
  });

  it('gibt bei leer einen leeren String zurück', () => {
    expect(apiZuDeDezimal('')).toBe('');
    expect(apiZuDeDezimal(null)).toBe('');
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
    expect(val('-3,5')).toBeNull();
  });

  it('lehnt Unsinn ab', () => {
    expect(val('abc')).toEqual({ dezimal: true });
    expect(val('1,2,3')).toEqual({ dezimal: true });
    expect(val('1..2')).toEqual({ dezimal: true });
  });
});
