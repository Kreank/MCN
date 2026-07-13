import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { SiteReportService } from '../../core/site-report.service';
import { BelegService } from '../../core/beleg.service';
import { AuthService } from '../../core/auth.service';
import { Quote, QuoteStatus } from '../../core/beleg.model';
import {
  SollIst,
  SollIstArt,
  sollIstArtClass,
  sollIstArtLabel,
  sollIstArtSymbol,
} from '../../core/site-report.model';
import { fehlerDetail } from '../http-fehler';
import { apiZuDeAnzeige } from '../formular/dezimal';

type Zustand =
  | { kind: 'loading' }
  | { kind: 'ready'; daten: SollIst }
  | { kind: 'error' };

/** Diese Status bilden KEIN Soll (Server: SOLL_AUSGESCHLOSSENE_STATUS). */
const KEIN_SOLL: QuoteStatus[] = ['ENTWURF', 'INTERN_GEPRUEFT', 'ABGELEHNT', 'ERSETZT'];

const STATUS_LABELS: Record<string, string> = {
  ENTWURF: 'Entwurf',
  INTERN_GEPRUEFT: 'Intern geprüft',
  FREIGEGEBEN: 'Freigegeben',
  VERSENDET: 'Versendet',
  ANGENOMMEN: 'Angenommen',
  ABGELEHNT: 'Abgelehnt',
  ABGELAUFEN: 'Abgelaufen',
  ERSETZT: 'Ersetzt',
};

/**
 * Soll-Ist-Abgleich eines Auftrags: das Angebots-**Soll** gegen das
 * Berichts-**Ist** über alle Baustellenberichte.
 *
 * **Keine Geldbeträge** — der Bericht führt keine Preise, also kann auch der
 * Abgleich keine ausweisen. Gerechnet wird ohnehin auf dem Server; das Frontend
 * zeigt nur an.
 *
 * **Das Soll steht und fällt mit der Zuordnung `quote.work_order_id`.** Deshalb
 * bedient dieser Abschnitt sie auch: Angebote der Liegenschaft lassen sich dem
 * Auftrag zuordnen und wieder lösen — ohne Datenbank-Handgriff. Das gilt in
 * **jedem** Status (Migration 0082): der Auftrag entsteht regelmäßig erst, nachdem
 * der Kunde das versendete Angebot angenommen hat. Ein Angebot im ENTWURF ist zwar
 * zuordenbar, bildet aber **noch kein Soll** (es ist nie hinausgegangen); das steht
 * auch so da.
 *
 * Die **Art** der Abweichung steht immer als Text da (plus Symbol) — nie allein
 * über die Farbe (WCAG 2.2 AA, 1.4.1).
 *
 * Fließen unsignierte Berichte ein (`enthaelt_entwuerfe`), ist der Abgleich
 * vorläufig. Das wird ausgewiesen, nicht verschwiegen.
 *
 * Der Endpunkt ist eine Dispositionssicht: Rollen mit row_scope EIGENE bekommen
 * 403. Deshalb wird der Abschnitt bei ihnen gar nicht erst eingebunden (Muster
 * `nurAlle`/`darfAlle`) — die Komponente behandelt einen 403 trotzdem sauber.
 */
@Component({
  selector: 'app-soll-ist',
  imports: [],
  templateUrl: './soll-ist.html',
  styleUrl: './soll-ist.scss',
})
export class SollIstAbgleich {
  private readonly svc = inject(SiteReportService);
  private readonly belegSvc = inject(BelegService);
  private readonly auth = inject(AuthService);

  readonly workOrderId = input.required<string>();
  /** Liegenschaft des Auftrags — Suchraum für zuordenbare Angebote. */
  readonly propertyId = input.required<string>();

  protected readonly zustand = signal<Zustand>({ kind: 'loading' });
  private reqId = 0;

  /** Angebote der Liegenschaft (für Zuordnung/Anzeige). */
  protected readonly angeboteDerLiegenschaft = signal<Quote[]>([]);
  protected readonly wahl = signal<string>('');
  protected readonly speichert = signal(false);
  protected readonly fehler = signal<string | null>(null);
  protected readonly ansage = signal<string>('');

  /** Zuordnung ändern verlangt das Recht am BELEG, nicht am Bericht. */
  protected readonly darfZuordnen = computed(() => this.auth.darf('invoicing', 'AENDERN'));

  /** Diesem Auftrag zugeordnete Angebote. */
  protected readonly zugeordnet = computed(() =>
    this.angeboteDerLiegenschaft().filter((q) => q.work_order_id === this.workOrderId()),
  );

  /**
   * Zuordenbar: jedes Angebot der Liegenschaft, das noch keinem Auftrag zugeordnet
   * ist — unabhängig vom Status. Die Einfrierung nach Versand (B-30) erfasst den
   * Auftragsbezug nicht mehr (0082).
   */
  protected readonly kandidaten = computed(() =>
    this.angeboteDerLiegenschaft().filter((q) => !q.work_order_id),
  );

  constructor() {
    effect(() => {
      const id = this.workOrderId();
      const prop = this.propertyId();
      if (id) this.laden(id);
      if (prop) this.angeboteLaden(prop);
    });
  }

  private laden(id: string): void {
    const rid = ++this.reqId;
    this.zustand.set({ kind: 'loading' });
    this.svc.sollIst(id).subscribe({
      next: (daten) => {
        if (rid === this.reqId) this.zustand.set({ kind: 'ready', daten });
      },
      error: () => {
        if (rid === this.reqId) this.zustand.set({ kind: 'error' });
      },
    });
  }

  private angeboteLaden(propertyId: string): void {
    this.belegSvc.list({ page: 1, page_size: 100, property_id: propertyId }).subscribe({
      next: (seite) => this.angeboteDerLiegenschaft.set(seite.items),
      // Kein Recht auf Angebote (invoicing/LESEN)? Dann gibt es hier nichts zu
      // zeigen — der Abgleich selbst bleibt trotzdem lesbar.
      error: () => this.angeboteDerLiegenschaft.set([]),
    });
  }

  neuLaden(): void {
    this.laden(this.workOrderId());
    this.angeboteLaden(this.propertyId());
  }

  zuordnen(): void {
    const id = this.wahl();
    if (!id || this.speichert()) return;
    this.speichert.set(true);
    this.fehler.set(null);
    this.belegSvc.updateQuote(id, { work_order_id: this.workOrderId() }).subscribe({
      next: () => {
        this.speichert.set(false);
        this.wahl.set('');
        this.ansage.set('Angebot dem Auftrag zugeordnet. Das Soll wurde neu berechnet.');
        this.neuLaden();
      },
      error: (err) => {
        this.speichert.set(false);
        this.fehler.set(
          fehlerDetail(err) ?? 'Das Angebot konnte dem Auftrag nicht zugeordnet werden.',
        );
      },
    });
  }

  loesen(quote: Quote): void {
    if (this.speichert()) return;
    this.speichert.set(true);
    this.fehler.set(null);
    this.belegSvc.updateQuote(quote.id, { work_order_id: null }).subscribe({
      next: () => {
        this.speichert.set(false);
        this.ansage.set('Zuordnung gelöst. Das Angebot bildet kein Soll mehr.');
        this.neuLaden();
      },
      error: (err) => {
        this.speichert.set(false);
        this.fehler.set(fehlerDetail(err) ?? 'Die Zuordnung konnte nicht gelöst werden.');
      },
    });
  }

  wahlSetzen(wert: string): void {
    this.wahl.set(wert);
  }

  /** Zählt dieses zugeordnete Angebot ins Soll? Entwürfe zählen nicht. */
  zaehltInsSoll(q: Quote): boolean {
    return !KEIN_SOLL.includes(q.status);
  }

  statusLabel(s: string): string {
    return STATUS_LABELS[s] ?? s;
  }

  bezeichner(q: Quote): string {
    return q.quote_number ? `${q.quote_number} · ${q.title}` : q.title;
  }

  /** Menge (nie Geld) in deutscher Anzeigeform — mit Tausenderpunkt. */
  menge(wert: string | null): string {
    return apiZuDeAnzeige(wert) || '—';
  }

  /** Differenz mit ausdrücklichem Vorzeichen: „+3,5" liest sich als Mehrmenge. */
  differenz(wert: string): string {
    const de = apiZuDeAnzeige(wert);
    if (!de) return '—';
    return Number(wert) > 0 ? `+${de}` : de;
  }

  artLabel(a: SollIstArt): string {
    return sollIstArtLabel(a);
  }
  artSymbol(a: SollIstArt): string {
    return sollIstArtSymbol(a);
  }
  artClass(a: SollIstArt): string {
    return sollIstArtClass(a);
  }
}
