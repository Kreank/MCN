import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { BelegService } from '../../core/beleg.service';
import { AuthService } from '../../core/auth.service';
import { LineType, QuoteDetail, QuoteStatus } from '../../core/beleg.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { Dateien } from '../../shared/dateien/dateien';
import { ZielFilter } from '../../core/datei.model';
import { VerbotenState, fehlerDetail, fehlerState, istVerboten } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: QuoteDetail }
  | VerbotenState
  | { kind: 'error' };

type Meldung = { art: 'erfolg' | 'fehler'; text: string };

@Component({
  selector: 'app-beleg-detail',
  imports: [Mappe, RouterLink, KeinZugriff, Bestaetigung, Dateien],
  templateUrl: './beleg-detail.html',
  styleUrl: './beleg-detail.scss',
})
export class BelegDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(BelegService);
  private readonly auth = inject(AuthService);

  protected readonly tab = signal('positionen');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  // --- Versenden (unumkehrbar) --------------------------------------------
  protected readonly darfVersenden = computed(() => this.auth.darf('invoicing', 'VERSENDEN'));
  protected readonly meldung = signal<Meldung | null>(null);
  protected readonly versendenOffen = signal(false);
  protected readonly versendenLaedt = signal(false);

  /** Versenden ist nur vor dem Versand sinnvoll (Server setzt die Tore durch). */
  protected readonly kannVersenden = computed(() => {
    const d = this.daten();
    if (!d) return false;
    return d.status === 'ENTWURF' || d.status === 'INTERN_GEPRUEFT' || d.status === 'FREIGEGEBEN';
  });

  protected readonly tabs: MappeTab[] = [
    { id: 'positionen', label: 'Positionen' },
    { id: 'uebersicht', label: 'Übersicht' },
    { id: 'dateien', label: 'Dateien' },
  ];

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  /**
   * Zielreferenz fuer den Dateien-Tab. Diese Mappe zeigt ausschliesslich
   * Angebote (invoicing.quote) — daher `quote_id`. Rechnungen haben eine eigene
   * Mappe (rechnung-detail). Stabile Referenz (nur bei Belegwechsel neu).
   */
  protected readonly dateienZiel = computed<ZielFilter>(() => ({
    quote_id: this.daten()?.id ?? '',
  }));

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
    this.svc.get(id).subscribe({
      next: (data) => {
        if (rid === this.reqId) this.state.set({ kind: 'ready', data });
      },
      error: (err) => {
        if (rid === this.reqId) this.state.set(fehlerState(err));
      },
    });
  }

  // ---- Versenden ----------------------------------------------------------
  versendenFragen(): void {
    this.meldung.set(null);
    this.versendenOffen.set(true);
  }

  versendenAbbrechen(): void {
    if (!this.versendenLaedt()) this.versendenOffen.set(false);
  }

  versendenBestaetigen(): void {
    const d = this.daten();
    if (!d || this.versendenLaedt()) return;
    this.versendenLaedt.set(true);
    this.svc.sendQuote(d.id).subscribe({
      next: (aktualisiert) => {
        this.versendenLaedt.set(false);
        this.versendenOffen.set(false);
        this.state.set({ kind: 'ready', data: aktualisiert });
        this.meldung.set({
          art: 'erfolg',
          text: `Angebot versendet. Belegnummer ${aktualisiert.quote_number ?? '—'} wurde vergeben.`,
        });
      },
      error: (err) => {
        this.versendenLaedt.set(false);
        this.versendenOffen.set(false);
        this.meldung.set({ art: 'fehler', text: this.fehlerText(err) });
      },
    });
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  private fehlerText(err: unknown): string {
    if (istVerboten(err)) return fehlerDetail(err) ?? 'Keine Berechtigung für diese Aktion.';
    return fehlerDetail(err) ?? 'Die Aktion ist fehlgeschlagen. Bitte erneut versuchen.';
  }

  // ---- Darstellungshelfer -------------------------------------------------
  euro(amount: string | null): string {
    if (amount === null) return '—';
    return new Intl.NumberFormat('de-DE', {
      style: 'currency',
      currency: 'EUR',
    }).format(Number(amount));
  }

  menge(qty: string | null, unit: string | null): string {
    if (qty === null) return '';
    // Trailing-Nullen der numeric(15,3) glätten.
    const n = Number(qty);
    const formatted = new Intl.NumberFormat('de-DE', {
      maximumFractionDigits: 3,
    }).format(n);
    return unit ? `${formatted} ${unit}` : formatted;
  }

  statusLabel(s: QuoteStatus): string {
    const map: Record<QuoteStatus, string> = {
      ENTWURF: 'Entwurf',
      INTERN_GEPRUEFT: 'Intern geprüft',
      FREIGEGEBEN: 'Freigegeben',
      VERSENDET: 'Versendet',
      ANGENOMMEN: 'Angenommen',
      ABGELEHNT: 'Abgelehnt',
      ABGELAUFEN: 'Abgelaufen',
      ERSETZT: 'Ersetzt',
    };
    return map[s] ?? s;
  }
  statusClass(s: QuoteStatus): string {
    if (s === 'ANGENOMMEN') return 'stamp--positive';
    if (s === 'ABGELEHNT' || s === 'ABGELAUFEN' || s === 'ERSETZT') return 'stamp--warn';
    return '';
  }

  lineTypeLabel(t: LineType): string {
    const map: Record<LineType, string> = {
      MATERIAL: 'Material',
      ARBEITSZEIT: 'Arbeitszeit',
      PAUSCHALE: 'Pauschale',
      FREMDLEISTUNG: 'Fremdleistung',
      FAHRT: 'Fahrt',
      ZUSCHLAG: 'Zuschlag',
      TEXT: 'Text',
      ZWISCHENSUMME: 'Zwischensumme',
    };
    return map[t] ?? t;
  }

  isText(t: LineType): boolean {
    return t === 'TEXT' || t === 'ZWISCHENSUMME';
  }

  // Kurzform des Inhalts-Hashes (Beleg-Fingerabdruck) für die Anzeige.
  hashKurz(h: string | null): string {
    return h ? h.slice(0, 12) + '…' : '—';
  }
}
