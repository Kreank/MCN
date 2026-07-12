import { Directive, input, output, signal } from '@angular/core';
import { inZwischenablage } from '../../shared/zwischenablage';

/**
 * Ein Aufmaß-Ergebnis, das als **bepreisbare Position** in einen Beleg geht:
 * Menge + Einheit + nachvollziehbare Rechenaufstellung.
 *
 * Warum das die Invariante NICHT bricht: `menge` ist eine **Menge**, kein
 * Geldbetrag — dasselbe, was ein Mensch als Aufmaß ins Mengenfeld tippt. Sie
 * wird als **Punkt-String** übergeben (`mengeApi`, nie `number`) und der Server
 * bleibt die verbindliche Rechenstelle für Positionsnetto, Steuer und Summen.
 * Ein Preis wird hier ausdrücklich NICHT mitgeliefert.
 */
export interface RechnerPosition {
  /** Positionstext inkl. Rechenaufstellung (mehrzeilig). */
  readonly beschreibung: string;
  /** Menge als API-Dezimalstring mit Punkt, z. B. „20.16". Niemals `number`. */
  readonly menge: string;
  /** Einheit der Menge, z. B. „m²". */
  readonly einheit: string;
}

/**
 * Gemeinsames Verhalten aller Rechner: Kontextzeile (z. B. Liegenschaft),
 * „Kopieren" in die Zwischenablage und — nur dort, wo es fachlich passt — die
 * Übernahme des Ergebnisses in einen Beleg.
 *
 * Zwei getrennte Wege, bewusst nicht vermischt:
 *  - `uebernehmen` (**Text**): ein Überschlagswert dokumentiert eine Annahme
 *    (Heizlast, Volumenstrom, MAG) — er wird zur TEXTZEILE, nie zu Menge/Preis.
 *  - `uebernehmenPosition` (**Menge**): nur das Aufmaß. Es ermittelt eine Menge,
 *    keinen Betrag; sie geht als String in die Position, der Preis kommt vom
 *    Anwender bzw. aus dem Artikelstamm, und der Server rechnet die Summe.
 */
@Directive()
export abstract class RechnerBasis {
  /** Freier Kontext (z. B. „Liegenschaft Ahornweg 3") — erscheint auf der Ausgabe. */
  readonly kontext = input('');
  /** Zeigt „Als Position übernehmen" (nur im Beleg-Editor sinnvoll). */
  readonly uebernahme = input(false);
  /** Einzeiliger Ergebnistext für die Belegposition. */
  readonly uebernehmen = output<string>();
  /** Aufmaß-Ergebnis als bepreisbare Position (nur der Aufmaß-Rechner sendet das). */
  readonly uebernehmenPosition = output<RechnerPosition>();

  /** Rückmeldung an den Anwender (kopiert / übernommen / fehlgeschlagen). */
  protected readonly rueckmeldung = signal('');

  /** Der vollständige, mehrzeilige Klartext des Ergebnisses (leer = ungültig). */
  protected abstract ergebnisText(): string;
  /** Einzeiliger Text für die Übernahme in einen Beleg (leer = ungültig). */
  protected abstract positionsText(): string;

  /** Kontextzeile für die Ausgabe, oder null. */
  protected kontextZeile(): string | null {
    const k = this.kontext().trim();
    return k ? `- Objekt: ${k}` : null;
  }

  protected async kopieren(): Promise<void> {
    const text = this.ergebnisText();
    if (!text) {
      this.rueckmeldung.set('Bitte zuerst gültige Werte eingeben.');
      return;
    }
    const ok = await inZwischenablage(text);
    this.rueckmeldung.set(
      ok ? 'Ergebnis in die Zwischenablage kopiert.' : 'Kopieren ist fehlgeschlagen.',
    );
  }

  protected uebernehmenKlick(): void {
    const text = this.positionsText();
    if (!text) {
      this.rueckmeldung.set('Bitte zuerst gültige Werte eingeben.');
      return;
    }
    this.uebernehmen.emit(text);
  }

  /**
   * Mengenposition des Rechners — Standard: keine. Nur der Aufmaß-Rechner
   * überschreibt das (er ermittelt eine Menge, kein Überschlagswert).
   */
  protected position(): RechnerPosition | null {
    return null;
  }

  protected uebernehmenPositionKlick(): void {
    const p = this.position();
    if (!p) {
      this.rueckmeldung.set('Bitte zuerst gültige Werte eingeben.');
      return;
    }
    this.uebernehmenPosition.emit(p);
  }
}
