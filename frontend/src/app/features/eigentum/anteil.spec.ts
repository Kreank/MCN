import { FormControl } from '@angular/forms';
import {
  anteilFormatieren,
  anteilHinweis,
  anteilParsen,
  anteilValidator,
} from './anteil';

/**
 * Der Anteil ist das Feld, an dem Sascha beim Testen hängengeblieben ist
 * („Sinn des Zählers nicht ersichtlich. Nenner? was das?"). Jetzt ist es ein
 * Feld — und die Übersetzung in den exakten Bruch muss stimmen, sonst wäre der
 * Komfort mit Datenverlust bezahlt.
 */

function ok(eingabe: string) {
  const r = anteilParsen(eingabe);
  if (r.art !== 'ok') throw new Error(`erwartete gültige Eingabe, war: ${r.art}`);
  return `${r.anteil.zaehler}/${r.anteil.nenner}`;
}

function fehler(eingabe: string) {
  const r = anteilParsen(eingabe);
  return r.art === 'fehler' ? r.code : null;
}

describe('anteilParsen', () => {
  it('liest Brüche unverändert — und kürzt sie', () => {
    expect(ok('1/3')).toBe('1/3');
    expect(ok('1 / 3')).toBe('1/3');
    expect(ok('2/4')).toBe('1/2');
    expect(ok('1/1')).toBe('1/1');
  });

  it('liest Prozentangaben in jeder üblichen Schreibweise', () => {
    expect(ok('50 %')).toBe('1/2');
    expect(ok('50%')).toBe('1/2');
    expect(ok('50 Prozent')).toBe('1/2');
    expect(ok('12,5 %')).toBe('1/8');
    expect(ok('100 %')).toBe('1/1');
  });

  it('liest Dezimalzahlen unter 1 als Anteil', () => {
    expect(ok('0,25')).toBe('1/4');
    expect(ok('0,5')).toBe('1/2');
    expect(ok('1')).toBe('1/1');
  });

  it('liest eine nackte Zahl über 1 als Prozentwert — und sagt es', () => {
    expect(ok('50')).toBe('1/2');
    const r = anteilParsen('50');
    expect(r.art === 'ok' && r.alsProzent).toBe(true);
    expect(anteilHinweis('50')).toContain('Gelesen als');
    // Explizite Angaben werden NICHT als geraten markiert.
    const explizit = anteilParsen('50 %');
    expect(explizit.art === 'ok' && explizit.alsProzent).toBe(false);
  });

  it('behandelt leer als „Anteil unbekannt“ — kein Fehler', () => {
    expect(anteilParsen('').art).toBe('leer');
    expect(anteilParsen('   ').art).toBe('leer');
    expect(anteilParsen(null).art).toBe('leer');
    expect(anteilParsen(undefined).art).toBe('leer');
  });

  it('weist zurück, was kein Anteil ist', () => {
    expect(fehler('abc')).toBe('anteil');
    expect(fehler('1/0')).toBe('anteil');
    expect(fehler('0/3')).toBe('anteil');
    expect(fehler('0')).toBe('anteil');
    expect(fehler('-1/3')).toBe('anteil');
  });

  it('weist einen Anteil über dem Ganzen zurück', () => {
    expect(fehler('4/3')).toBe('anteilZuGross');
    expect(fehler('120 %')).toBe('anteilZuGross');
    // 101 als nackte Zahl wäre 101 % — auch das ist zu viel.
    expect(fehler('101')).toBe('anteilZuGross');
  });

  it('weist einen zu feinen Nenner zurück (Rechengrenze der Datenbank)', () => {
    expect(fehler('1/2000000')).toBe('anteilNenner');
    expect(fehler('0,3333333333')).toBe('anteilNenner');
  });

  it('rundet nie: 33,33 % ist NICHT ein Drittel', () => {
    expect(ok('33,33 %')).toBe('3333/10000');
    expect(ok('1/3')).toBe('1/3');
  });
});

describe('anteilFormatieren', () => {
  it('zeigt glatte Prozentwerte als Prozent, krumme Brüche als Bruch', () => {
    // Dieselbe Regel wie `anteil_text` auf dem Server.
    expect(anteilFormatieren(1, 2)).toBe('50 %');
    expect(anteilFormatieren(1, 4)).toBe('25 %');
    expect(anteilFormatieren(1, 3)).toBe('1/3');
    expect(anteilFormatieren(2, 3)).toBe('2/3');
  });

  it('sagt „unbekannt“, wenn nichts beziffert ist', () => {
    expect(anteilFormatieren(null, null)).toBe('unbekannt');
    expect(anteilFormatieren(1, null)).toBe('unbekannt');
  });

  it('ist die Umkehrung des Parsens (Rundlauf für die Korrektur-Maske)', () => {
    for (const [z, n] of [
      [1, 2],
      [1, 3],
      [2, 3],
      [3, 8],
      [1, 1],
    ]) {
      expect(ok(anteilFormatieren(z, n))).toBe(`${z}/${n}`);
    }
  });
});

describe('anteilValidator', () => {
  it('lässt leer und gültig durch', () => {
    expect(anteilValidator(new FormControl(''))).toBeNull();
    expect(anteilValidator(new FormControl('1/3'))).toBeNull();
    expect(anteilValidator(new FormControl('50 %'))).toBeNull();
  });

  it('meldet den Fehlercode, zu dem `feld-fehler.ts` den Text kennt', () => {
    expect(anteilValidator(new FormControl('abc'))).toEqual({ anteil: true });
    expect(anteilValidator(new FormControl('4/3'))).toEqual({ anteilZuGross: true });
    expect(anteilValidator(new FormControl('1/2000000'))).toEqual({
      anteilNenner: true,
    });
  });
});
