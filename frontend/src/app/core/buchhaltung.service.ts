import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  CorrectionInput,
  DunningIssue,
  DunningList,
  DunningNotice,
  OpenItemDetail,
  OpenItemPage,
  OpenItemQuery,
  PaymentDetail,
  PaymentRecord,
} from './buchhaltung.model';
import { CreditRef } from './beleg.model';

/** Typisierter Zugriff auf die Buchhaltungs-API (dev-Proxy: /api -> :8000). */
@Injectable({ providedIn: 'root' })
export class BuchhaltungService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/buchhaltung';

  listOpenItems(query: OpenItemQuery): Observable<OpenItemPage> {
    let params = new HttpParams()
      .set('page', query.page)
      .set('page_size', query.page_size);
    const q = query.q?.trim();
    if (q) params = params.set('q', q);
    if (query.payment_status) params = params.set('payment_status', query.payment_status);
    if (query.overdue) params = params.set('overdue', true);
    if (query.invoice_type) params = params.set('invoice_type', query.invoice_type);
    return this.http.get<OpenItemPage>(`${this.base}/invoices`, { params });
  }

  getOpenItem(id: string): Observable<OpenItemDetail> {
    return this.http.get<OpenItemDetail>(`${this.base}/invoices/${id}`);
  }

  /** Mahnliste; level filtert die aktuelle Mahnstufe (0 = überfällig, ungemahnt). */
  listDunning(level?: number | null): Observable<DunningList> {
    let params = new HttpParams();
    if (level !== null && level !== undefined) params = params.set('level', level);
    return this.http.get<DunningList>(`${this.base}/dunning`, { params });
  }

  // --- Schreibend (Session-Auth Pflicht) -----------------------------------

  /** (Teil-)Zahlung erfassen. amount stets positiv; Vorzeichen ergibt der Typ. */
  recordPayment(invoiceId: string, payload: PaymentRecord): Observable<PaymentDetail> {
    return this.http.post<PaymentDetail>(
      `${this.base}/invoices/${invoiceId}/payments`,
      payload,
    );
  }

  /** Zahlung durch Gegenbuchung stornieren (append-only). */
  reversePayment(paymentId: string, paidAt?: string | null): Observable<PaymentDetail> {
    return this.http.post<PaymentDetail>(`${this.base}/payments/${paymentId}/reverse`, {
      paid_at: paidAt ?? null,
    });
  }

  /** Mahnstufe erzeugen (nach außen wirkende Kundenkommunikation). */
  issueDunning(invoiceId: string, payload: DunningIssue): Observable<DunningNotice> {
    return this.http.post<DunningNotice>(
      `${this.base}/invoices/${invoiceId}/dunning`,
      payload,
    );
  }

  /** Veröffentlichte Rechnung stornieren (Stornobeleg); erzeugt Folgebeleg. */
  cancelInvoice(invoiceId: string): Observable<CreditRef> {
    return this.http.post<CreditRef>(`${this.base}/invoices/${invoiceId}/cancel`, {});
  }

  /** Rechnungskorrektur (Gutschrift) über die angegebenen Positionen. */
  correctInvoice(invoiceId: string, payload: CorrectionInput): Observable<CreditRef> {
    return this.http.post<CreditRef>(
      `${this.base}/invoices/${invoiceId}/correction`,
      payload,
    );
  }
}
