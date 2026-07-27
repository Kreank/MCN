import { AbstractControl, ValidationErrors } from '@angular/forms';
import { deZuApiDezimal, DEZIMAL_UNGUELTIG } from '../../shared/formular/dezimal';

/**
 * Der Eigentumsanteil als **ein** Eingabefeld.
 *
 * Sascha beim Testen des Reiters „Eigentümer": *„Sinn des Zählers nicht
 * ersichtlich. Nenner? was das?"* — und er hat recht. Zwei Zahlenfelder mit den
 * Beschriftungen „Zähler" und „Nenner" sind Mathematikunterricht, kein
 * Bürowerkzeug. Wer eine Eigentümerliste abtippt, liest dort „1/3" oder „50 %"
 * und will genau das eingeben.
 *
 * **Der Bruch bleibt trotzdem der gespeicherte Wert.** Drei Erben zu je 1/3
 * sind dezimal nicht darstellbar; „33,33 % dreimal" ergäbe 99,99 %, und ein
 * vollständiger Eigentumsstand wäre nie erreichbar (die Datenbank rechnet über
 * das kleinste gemeinsame Vielfache und vergleicht exakt). Dieses Modul ist
 * deshalb nur die **Übersetzung** zwischen dem, was jemand schreibt, und dem,
 * was das Modell braucht — es macht aus dem Bruch keine Kommazahl.
 *
 * Verstanden werden:
 *
 * | Eingabe | Ergebnis |
 * |---|---|
 * | `1/3`     | 1/3 |
 * | `50 %`, `50%`, `50 Prozent` | 1/2 |
 * | `0,25`    | 1/4 |
 * | `50`      | 1/2 — eine nackte Zahl über 1 ist im Alltag ein Prozentwert |
 * | leer      | „Anteil unbekannt" (zulässig, solange der Stand nicht vollständig ist) |
 */

export interface Anteil {
  readonly zaehler: number;
  readonly nenner: number;
}

/** Fehlercodes — die Texte stehen zentral in `feld-fehler.ts`. */
export type AnteilFehler = 'anteil' | 'anteilZuGross' | 'anteilNenner';

export type AnteilErgebnis =
  | { readonly art: 'leer' }
  | { readonly art: 'ok'; readonly anteil: Anteil; readonly alsProzent: boolean }
  | { readonly art: 'fehler'; readonly code: AnteilFehler };

/** Obergrenze des Nenners — dieselbe wie CHECK und Service (Rechengrenze der LCM). */
export const MAX_NENNER = 1_000_000;

/** Mehr Nachkommastellen ergeben ohnehin einen zu feinen Nenner (10^9 > 10^6). */
const MAX_NACHKOMMA = 9;

const BRUCH_RE = /^(\d+)\s*\/\s*(\d+)$/;
const PROZENT_RE = /%|prozent/i;

export function anteilParsen(eingabe: string | null | undefined): AnteilErgebnis {
  const roh = (eingabe ?? '').trim();
  if (!roh) return { art: 'leer' };

  const bruch = BRUCH_RE.exec(roh);
  if (bruch) {
    return pruefen(Number(bruch[1]), Number(bruch[2]), false);
  }

  const alsProzent = PROZENT_RE.test(roh);
  const zahlText = roh.replace(/%/g, '').replace(/prozent/gi, '').trim();
  const api = deZuApiDezimal(zahlText);
  if (!api || api === DEZIMAL_UNGUELTIG || api.startsWith('-')) {
    return { art: 'fehler', code: 'anteil' };
  }

  const [ganz, nachkomma = ''] = api.split('.');
  if (nachkomma.length > MAX_NACHKOMMA) {
    return { art: 'fehler', code: 'anteilNenner' };
  }

  let zaehler = Number(ganz + nachkomma);
  let nenner = 10 ** nachkomma.length;

  // Eine nackte Zahl über 1 („50") ist im Sprachgebrauch der Eigentümerlisten
  // ein Prozentwert. Der Dialog zeigt die Lesart darunter an, statt sie zu
  // verschweigen — geraten wird hier nichts, es wird nur ausgesprochen.
  const prozentRechnung = alsProzent || zaehler > nenner;
  if (prozentRechnung) nenner *= 100;

  return pruefen(zaehler, nenner, prozentRechnung && !alsProzent);
}

function pruefen(zaehler: number, nenner: number, geraten: boolean): AnteilErgebnis {
  if (!Number.isFinite(zaehler) || !Number.isFinite(nenner)) {
    return { art: 'fehler', code: 'anteil' };
  }
  if (zaehler <= 0 || nenner <= 0) return { art: 'fehler', code: 'anteil' };
  if (zaehler > nenner) return { art: 'fehler', code: 'anteilZuGross' };

  const t = ggt(zaehler, nenner);
  const gekuerzt = { zaehler: zaehler / t, nenner: nenner / t };
  if (gekuerzt.nenner > MAX_NENNER) return { art: 'fehler', code: 'anteilNenner' };
  return { art: 'ok', anteil: gekuerzt, alsProzent: geraten };
}

/**
 * Formularprüfung: Ein leeres Feld ist gültig („Anteil unbekannt"), ein
 * unlesbares nicht. Die Meldungstexte stehen in `feld-fehler.ts` — an einer
 * Stelle, wie alle anderen Feldmeldungen auch.
 */
export function anteilValidator(control: AbstractControl): ValidationErrors | null {
  const r = anteilParsen(control.value as string);
  return r.art === 'fehler' ? { [r.code]: true } : null;
}

/**
 * Was aus der Eingabe gelesen wurde, als Satz unter dem Feld. Er ist der Grund,
 * warum die Lesart „50" → „50 %" vertretbar ist: Sie wird ausgesprochen.
 */
export function anteilHinweis(eingabe: string | null | undefined): string {
  const r = anteilParsen(eingabe);
  if (r.art === 'leer') return 'Leer lassen, wenn der Anteil unbekannt ist.';
  if (r.art === 'fehler') return 'z. B. 1/3, 50 % oder 0,25.';
  const text = anteilFormatieren(r.anteil.zaehler, r.anteil.nenner);
  const bruch = `${r.anteil.zaehler}/${r.anteil.nenner}`;
  const gelesen = text === bruch ? bruch : `${text} (${bruch})`;
  return r.alsProzent ? `Gelesen als ${gelesen}.` : `= ${gelesen}`;
}

/**
 * Bruch → lesbarer Text. **Dieselbe Regel wie der Server** (`anteil_text`):
 * Ein glatter Prozentwert wird als Prozent gezeigt („1/2" → „50 %"), krumme
 * Brüche bleiben Brüche — „1/3" ist die Wahrheit, „33,33 %" wäre gerundet.
 */
export function anteilFormatieren(
  zaehler: number | null | undefined,
  nenner: number | null | undefined,
): string {
  if (zaehler == null || nenner == null || nenner === 0) return 'unbekannt';
  const prozent = zaehler * 100;
  if (prozent % nenner === 0) return `${prozent / nenner} %`;
  return `${zaehler}/${nenner}`;
}

function ggt(a: number, b: number): number {
  let x = Math.abs(a);
  let y = Math.abs(b);
  while (y) {
    [x, y] = [y, x % y];
  }
  return x || 1;
}
