import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { BelegService } from '../../core/beleg.service';
import { QUOTE_STATUS_LABEL, QuoteMengen, QuoteStatus } from '../../core/beleg.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; items: QuoteMengen[]; total: number }
  | VerbotenState
  | { kind: 'error' };

/**
 * „Was ist an meinen Objekten beauftragt?" — die Angebotsliste **ohne Preise**.
 *
 * Sie ersetzt für row_scope EIGENE (Monteur) das Belegregister (`features/dokumente`):
 * Dessen Listen führen Beträge und seine Dialoge legen Belege an — beides antwortet
 * dem Monteur mit 403. Statt ihn auf eine Fehlerseite zu schicken (oder den
 * Navigationspunkt wortlos zu verstecken), bekommt er hier die Sicht, die ihm
 * zusteht: seine Angebote, mit Mengen, ohne Geld.
 *
 * Ohne Paginierung: Die Grundmenge sind die versendeten Angebote an den Objekten,
 * an denen dieser Mensch je einen Einsatz hatte — das sind Dutzende, keine
 * Tausende. Kommen mehr als `SEITE` zusammen, sagt die Ansicht es (kein stilles
 * Abschneiden) und der Filter grenzt ein.
 */
const SEITE = 100;

@Component({
  selector: 'app-angebot-mengen-liste',
  imports: [RouterLink, KeinZugriff],
  templateUrl: './angebot-mengen-liste.html',
  styleUrl: './angebot-mengen-liste.scss',
})
export class AngebotMengenListe {
  private readonly svc = inject(BelegService);

  protected readonly state = signal<ViewState>({ kind: 'loading' });

  protected readonly items = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.items : [];
  });

  /** Es gäbe mehr, als geliefert wurde — das sagt die Ansicht, statt zu schweigen. */
  protected readonly gekuerzt = computed(() => {
    const s = this.state();
    return s.kind === 'ready' && s.total > s.items.length;
  });

  protected readonly ansage = computed(() => {
    const s = this.state();
    if (s.kind !== 'ready') return '';
    const n = s.items.length;
    return n === 1 ? '1 Angebot gefunden.' : `${n} Angebote gefunden.`;
  });

  constructor() {
    this.laden();
  }

  laden(): void {
    this.state.set({ kind: 'loading' });
    this.svc.listQuotesMengen({ page: 1, page_size: SEITE }).subscribe({
      next: (seite) => this.state.set({ kind: 'ready', items: seite.items, total: seite.total }),
      error: (err) => this.state.set(fehlerState(err)),
    });
  }

  statusLabel(s: QuoteStatus): string {
    return QUOTE_STATUS_LABEL[s] ?? s;
  }

  statusClass(s: QuoteStatus): string {
    return s === 'ANGENOMMEN' ? 'stamp--positive' : '';
  }
}
