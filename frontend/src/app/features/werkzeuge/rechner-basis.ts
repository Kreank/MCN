import { Directive, input, output, signal } from '@angular/core';
import { inZwischenablage } from '../../shared/zwischenablage';

/**
 * Gemeinsames Verhalten aller Rechner: Kontextzeile (z. B. Liegenschaft),
 * „Kopieren" in die Zwischenablage und — nur dort, wo es fachlich passt — die
 * Übernahme des Ergebnisses als Textzeile in einen Beleg.
 *
 * WICHTIG: Übernommen wird ausschliesslich **Text**. Es wandert kein hier
 * gerechneter Zahlenwert als Menge oder Betrag in eine bepreiste Position —
 * der Server bleibt die einzige verbindliche Rechenstelle.
 */
@Directive()
export abstract class RechnerBasis {
  /** Freier Kontext (z. B. „Liegenschaft Ahornweg 3") — erscheint auf der Ausgabe. */
  readonly kontext = input('');
  /** Zeigt „Als Position übernehmen" (nur im Beleg-Editor sinnvoll). */
  readonly uebernahme = input(false);
  /** Einzeiliger Ergebnistext für die Belegposition. */
  readonly uebernehmen = output<string>();

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
}
