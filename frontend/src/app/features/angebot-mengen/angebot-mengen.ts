import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { BelegService } from '../../core/beleg.service';
import {
  LINE_KIND_LABEL,
  LINE_TYPE_LABEL,
  LineKind,
  LineType,
  QUOTE_STATUS_LABEL,
  QuoteMengenDetail,
  QuoteStatus,
} from '../../core/beleg.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: QuoteMengenDetail }
  | VerbotenState
  | { kind: 'error' };

/**
 * Das Angebot **ohne Preise** — die Angebotsansicht des Monteurs (Migration 0102).
 *
 * ## Warum es diese Ansicht gibt
 *
 * Der Monteur muss wissen, **was beauftragt ist**: 12 m Kupferrohr DN20, sechs
 * Thermostatventile. Sonst baut er das Falsche ein oder übersieht eine Position.
 * Was es kostet, geht ihn nichts an — und der **Einkaufspreis** schon gar nicht.
 *
 * ## Zwei Regeln, die diese Komponente trägt
 *
 * **1. Sie zieht aus einem eigenen, preisfreien Endpunkt** (`GET
 * /invoicing/quotes/{id}/mengen`), nicht aus dem Angebotsdetail mit einer
 * ausgeblendeten Spalte. Ein Template, das eine Spalte nicht rendert, hat den Betrag
 * trotzdem im Speicher — und im Netzwerk-Tab. Hier kommt er gar nicht erst an.
 *
 * **2. Sie sagt, dass Preise fehlen** (`preise_ausgeblendet`). Spalten stillschweigend
 * wegzulassen wäre eine Lüge durch Auslassung: Der Nutzer könnte glauben, das Angebot
 * habe keine Preise. Dieselbe Ehrlichkeitsregel gilt in der Suche für die gekürzte
 * Trefferliste.
 */
@Component({
  selector: 'app-angebot-mengen',
  imports: [Mappe, RouterLink, KeinZugriff],
  templateUrl: './angebot-mengen.html',
  styleUrl: './angebot-mengen.scss',
})
export class AngebotMengen {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(BelegService);

  protected readonly tab = signal('positionen');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  protected readonly tabs: MappeTab[] = [
    { id: 'positionen', label: 'Positionen' },
    { id: 'uebersicht', label: 'Übersicht' },
  ];

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  /** Abschnitte (Rubriken) mit ihren Positionen — ohne Abschnitt bleibt eine Gruppe. */
  protected readonly abschnitte = computed(() => {
    const d = this.daten();
    if (!d) return [];
    const titel = new Map(d.rubriken.map((r) => [r.position_number, r.title]));
    const gruppen: { nr: number | null; titel: string | null; zeilen: typeof d.lines }[] = [];
    for (const l of d.lines) {
      const nr = l.rubrik ?? null;
      const letzte = gruppen[gruppen.length - 1];
      if (letzte && letzte.nr === nr) {
        letzte.zeilen.push(l);
        continue;
      }
      gruppen.push({
        nr,
        titel: nr === null ? null : (titel.get(nr) ?? null),
        zeilen: [l],
      });
    }
    return gruppen;
  });

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.tab.set('positionen');
      if (!id) {
        this.state.set({ kind: 'error' });
        return;
      }
      this.load(id);
    });
  }

  retry(): void {
    const id = this.route.snapshot.paramMap.get('id');
    if (id) this.load(id);
  }

  private load(id: string): void {
    const rid = ++this.reqId;
    this.state.set({ kind: 'loading' });
    this.svc.getQuoteMengen(id).subscribe({
      next: (data) => {
        if (rid === this.reqId) this.state.set({ kind: 'ready', data });
      },
      error: (err) => {
        if (rid === this.reqId) this.state.set(fehlerState(err));
      },
    });
  }

  // ---- Darstellungshelfer -------------------------------------------------

  /** Menge + Einheit. Ohne Menge bleibt die Zelle leer — nicht „0". */
  menge(qty: string | null, unit: string | null): string {
    if (qty === null) return '';
    const formatiert = new Intl.NumberFormat('de-DE', {
      maximumFractionDigits: 3,
    }).format(Number(qty));
    return unit ? `${formatiert} ${unit}` : formatiert;
  }

  statusLabel(s: QuoteStatus): string {
    return QUOTE_STATUS_LABEL[s] ?? s;
  }

  statusClass(s: QuoteStatus): string {
    if (s === 'ANGENOMMEN') return 'stamp--positive';
    if (s === 'ABGELEHNT' || s === 'ABGELAUFEN' || s === 'ERSETZT') return 'stamp--warn';
    return '';
  }

  lineTypeLabel(t: LineType): string {
    return LINE_TYPE_LABEL[t] ?? t;
  }

  /** '' für NORMAL — die Anmerkung erscheint nur, wo sie etwas bedeutet. */
  lineKindLabel(k: LineKind): string {
    return LINE_KIND_LABEL[k] ?? '';
  }

  isText(t: LineType): boolean {
    return t === 'TEXT' || t === 'ZWISCHENSUMME';
  }
}
