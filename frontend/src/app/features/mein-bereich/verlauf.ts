import { Component, computed, inject, signal } from '@angular/core';
import { ZeiterfassungService } from '../../core/zeiterfassung.service';
import {
  Arbeitstag,
  Stundenkonto,
  TAG_STATUS_LABEL,
  TAG_STATUS_ZEICHEN,
  dauerText,
  kalenderwoche,
  kontoZeitraum,
  saldoArt,
  saldoText,
  tagStatusClass,
  tagText,
} from '../../core/zeiterfassung.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';

type ViewState = { kind: 'loading' } | { kind: 'ready' } | VerbotenState | { kind: 'error' };

/**
 * „Mein Verlauf" — Stundenkonto und die vergangenen Arbeitstage.
 *
 * Befund E2, Teil „Dichte". Vorher stand alles auf einer Seite: Stempeluhr,
 * Tagessummen, die Buchungen von heute, das Stundenkonto und dreißig Tage
 * Historie. Das sind zwei verschiedene Fragen, die man zu verschiedenen
 * Zeitpunkten stellt — „was mache ich gerade?" beim Stempeln, „wie stehe ich
 * da?" beim Nachsehen. Die Recherche über etablierte Zeiterfassungswerkzeuge
 * zeigt diese Trennung durchgängig (Toggl, Clockify, Harvest, ZEP): Timer und
 * Tagesliste hier, Auswertung dort.
 *
 * Die Stempeluhr behält deshalb nur eine schmale Saldo-Zeile mit Verweis
 * hierher; der ausführliche Block steht jetzt an einem Ort, an dem man ihn auch
 * lesen will.
 */
@Component({
  selector: 'app-mein-verlauf',
  imports: [KeinZugriff],
  templateUrl: './verlauf.html',
  styleUrl: './verlauf.scss',
})
export class MeinVerlauf {
  private readonly svc = inject(ZeiterfassungService);

  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly konto = signal<Stundenkonto | null>(null);
  protected readonly tage = signal<Arbeitstag[]>([]);

  protected readonly dauerText = dauerText;
  protected readonly tagText = tagText;
  protected readonly tagStatusClass = tagStatusClass;
  protected readonly TAG_STATUS_LABEL = TAG_STATUS_LABEL;
  protected readonly TAG_STATUS_ZEICHEN = TAG_STATUS_ZEICHEN;

  /** Die Arbeitstage nach Kalenderwoche gebündelt, jüngste zuerst. */
  protected readonly wochen = computed(() => {
    const gruppen = new Map<string, { id: string; label: string; tage: Arbeitstag[] }>();
    for (const t of this.tage()) {
      const schluessel = kalenderwoche(t.day);
      const vorhanden = gruppen.get(schluessel.id);
      if (vorhanden) vorhanden.tage.push(t);
      else gruppen.set(schluessel.id, { ...schluessel, tage: [t] });
    }
    return [...gruppen.values()].map((g) => ({
      ...g,
      arbeit: g.tage.reduce((s, t) => s + t.arbeit_sekunden, 0),
      pause: g.tage.reduce((s, t) => s + t.pause_sekunden, 0),
    }));
  });

  constructor() {
    this.laden();
  }

  protected laden(): void {
    this.state.set({ kind: 'loading' });
    const [von, bis] = kontoZeitraum();
    this.svc.stundenkonto(undefined, von, bis).subscribe({
      next: (k) => this.konto.set(k),
      error: () => this.konto.set(null),
    });
    this.svc.meineTage().subscribe({
      next: (t) => {
        this.tage.set(t);
        this.state.set({ kind: 'ready' });
      },
      error: (err) => this.state.set(fehlerState(err)),
    });
  }

  protected stundenText(wert: string): string {
    const n = Number(wert);
    if (!Number.isFinite(n)) return wert;
    return `${new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1 }).format(n)} h`;
  }

  protected saldoText(wert: string): string {
    return saldoText(wert);
  }

  protected saldoArt(wert: string): 'plus' | 'minus' | 'null' {
    return saldoArt(wert);
  }

  protected hatAusgleich(wert: string): boolean {
    const n = Number(wert);
    return Number.isFinite(n) && n !== 0;
  }
}
