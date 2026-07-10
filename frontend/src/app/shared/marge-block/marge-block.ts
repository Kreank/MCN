import { Component, computed, input } from '@angular/core';
import { Marge } from '../../core/auswertungen.model';

/**
 * Zeigt einen Deckungsbeitrag-/Marge-Block als KPI-Trio (Basis · DB · Marge)
 * plus einen EHRLICHEN Deckungshinweis.
 *
 * Kernregel (wie der Angebotseditor / DB-Kalkulation): fehlt an Positionen der
 * Einkaufspreis, ist deren Marge NICHT 0 und NICHT 100 %, sondern UNBEKANNT.
 * Deckungsbeitrag/Marge beziehen sich dann nur auf den gedeckten Netto-Anteil;
 * der ungedeckte Anteil wird als „x Positionen ohne EK — Marge unbekannt"
 * ausgewiesen (Text + Glyph, nicht nur Farbe = WCAG). „unbekannt" wird als Wort
 * gezeigt, nie als Zahl 0.
 */
@Component({
  selector: 'app-marge-block',
  imports: [],
  templateUrl: './marge-block.html',
  styleUrl: './marge-block.scss',
})
export class MargeBlock {
  /** Der Marge-Block (Beträge als Decimal-String). */
  readonly marge = input.required<Marge>();
  /** Beschriftung (z. B. „Realisiert" / „Geplant"). */
  readonly label = input<string>('Deckungsbeitrag & Marge');

  protected readonly hatLuecke = computed(() => this.marge().positionen_ohne_ek > 0);
  protected readonly belastbar = computed(
    () => this.marge().ek_vollstaendig && this.marge().positionen > 0,
  );

  /** Deckungsbeitrag als Euro oder das Wort „unbekannt". */
  protected readonly dbText = computed(() => {
    const db = this.marge().deckungsbeitrag;
    return db === null ? 'unbekannt' : this.euro(db);
  });

  /** Marge in Prozent (de-DE) oder das Wort „unbekannt". */
  protected readonly margeText = computed(() => {
    const p = this.marge().marge_prozent;
    if (p === null) return 'unbekannt';
    return (
      new Intl.NumberFormat('de-DE', {
        minimumFractionDigits: 1,
        maximumFractionDigits: 2,
      }).format(Number(p)) + ' %'
    );
  });

  euro(amount: string | null): string {
    if (amount === null) return '—';
    return new Intl.NumberFormat('de-DE', {
      style: 'currency',
      currency: 'EUR',
    }).format(Number(amount));
  }
}
