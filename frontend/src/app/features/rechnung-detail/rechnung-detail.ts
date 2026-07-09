import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { BelegService } from '../../core/beleg.service';
import { InvoiceDetail, InvoiceStatus, InvoiceType, LineType } from '../../core/beleg.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: InvoiceDetail }
  | VerbotenState
  | { kind: 'error' };

@Component({
  selector: 'app-rechnung-detail',
  imports: [Mappe, RouterLink, KeinZugriff],
  templateUrl: './rechnung-detail.html',
  styleUrl: './rechnung-detail.scss',
})
export class RechnungDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(BelegService);

  protected readonly tab = signal('positionen');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  protected readonly tabs: MappeTab[] = [
    { id: 'positionen', label: 'Positionen' },
    { id: 'beteiligte', label: 'Beteiligte' },
    { id: 'uebersicht', label: 'Übersicht' },
  ];

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
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
    this.svc.getInvoice(id).subscribe({
      next: (data) => {
        if (rid === this.reqId) this.state.set({ kind: 'ready', data });
      },
      error: (err) => {
        if (rid === this.reqId) this.state.set(fehlerState(err));
      },
    });
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
    const n = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 3 }).format(
      Number(qty),
    );
    return unit ? `${n} ${unit}` : n;
  }

  typeLabel(t: InvoiceType): string {
    const map: Record<InvoiceType, string> = {
      RECHNUNG: 'Rechnung',
      ABSCHLAGSRECHNUNG: 'Abschlagsrechnung',
      TEILRECHNUNG: 'Teilrechnung',
      SCHLUSSRECHNUNG: 'Schlussrechnung',
      GUTSCHRIFT: 'Gutschrift',
      STORNO: 'Storno',
    };
    return map[t] ?? t;
  }

  statusLabel(s: InvoiceStatus): string {
    return s === 'VEROEFFENTLICHT' ? 'Veröffentlicht' : 'Entwurf';
  }
  statusClass(s: InvoiceStatus): string {
    return s === 'VEROEFFENTLICHT' ? 'stamp--positive' : '';
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

  roleLabel(r: string): string {
    const map: Record<string, string> = {
      INVOICE_DEBTOR: 'Rechnungsschuldner',
      INVOICE_RECIPIENT: 'Rechnungsempfänger',
      REPRESENTATIVE: 'Vertretung',
      COST_BEARER: 'Kostenträger',
    };
    return map[r] ?? r;
  }
  // Kurzform des Inhalts-Hashes (GoBD-Beleg-Fingerabdruck) für die Anzeige.
  hashKurz(h: string | null): string {
    return h ? h.slice(0, 12) + '…' : '—';
  }

  /** URL der on-the-fly gerenderten PDF-Ausfertigung (nur veröffentlicht). */
  pdfUrl(id: string): string {
    return `/api/invoicing/invoices/${id}/pdf`;
  }
}
