import { Bauteil } from '../../core/bauteilkatalog.model';
import { apiZahl, eingabe } from '../raumaufmass/raum-rechnen';

/**
 * **Woher kommt der U-Wert, der da im Feld steht?**
 *
 * Der Katalogwert wird beim Erfassen KOPIERT, nicht verlinkt — und er ist
 * überschreibbar: ein gemessener Wert schlägt die Vorlage. Genau deshalb muss
 * der Bediener jederzeit sehen, ob der Wert *aus der Vorlage* stammt oder
 * *abweichend eingegeben* wurde (dasselbe Muster wie der § 35a-Arbeitskosten-
 * anteil im Beleg-Editor: „abgeleitet" vs. „abweichend angegeben").
 *
 * Reine Funktion, kein Angular — direkt testbar.
 */
export type UHerkunft =
  /** Keine Vorlage gewählt — der Wert (falls einer da ist) ist Handeingabe. */
  | { art: 'ohne-vorlage' }
  /** Vorlage gewählt, im Katalog steht kein U-Wert, das Feld ist leer. */
  | { art: 'vorlage-ohne-wert' }
  /** Vorlage ohne Katalogwert, aber hier von Hand ein Wert eingetragen. */
  | { art: 'eigener-wert' }
  /** Der Wert im Feld ist der Katalogwert. */
  | { art: 'aus-vorlage'; vorlage: string }
  /** Der Wert im Feld weicht vom Katalogwert ab (gemessen/korrigiert). */
  | { art: 'abweichend'; vorlage: string }
  /** Die Vorlage hätte einen Wert, das Feld ist aber leer — Heizlast bleibt unbekannt. */
  | { art: 'fehlt'; vorlage: string };

/** Katalogwert als deutsche Anzeige („0,24") — exakt, ohne Rundung auf zwei Stellen. */
export function vorlagenWert(v: Bauteil): string | null {
  if (v.u_value == null || v.u_value === '') return null;
  const n = Number(v.u_value);
  return Number.isFinite(n) ? apiZahl(n).replace('.', ',') : String(v.u_value);
}

/**
 * @param vorlage Die gewählte Katalog-Vorlage (oder null).
 * @param roh     Der U-Wert **so wie er im Eingabefeld steht** (deutsche Form).
 */
export function uHerkunft(vorlage: Bauteil | null | undefined, roh: string): UHerkunft {
  if (!vorlage) return { art: 'ohne-vorlage' };

  const anzeige = vorlagenWert(vorlage);
  const e = eingabe(roh);

  if (anzeige == null) {
    // Auslieferungszustand: der Katalog kennt (noch) keinen U-Wert.
    return e.art === 'wert' ? { art: 'eigener-wert' } : { art: 'vorlage-ohne-wert' };
  }

  if (e.art === 'leer') return { art: 'fehlt', vorlage: anzeige };
  // Unlesbar/mehrdeutig meldet das Feld selbst; von der Vorlage weicht es allemal ab.
  if (e.art === 'fehler') return { art: 'abweichend', vorlage: anzeige };

  const katalog = Number(vorlage.u_value);
  return Math.abs(e.zahl - katalog) <= 1e-6
    ? { art: 'aus-vorlage', vorlage: anzeige }
    : { art: 'abweichend', vorlage: anzeige };
}
