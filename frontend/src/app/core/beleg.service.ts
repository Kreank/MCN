import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  InvoiceCreate,
  InvoiceDetail,
  InvoicePage,
  InvoicePartyCreate,
  InvoiceQuery,
  QuoteCreate,
  QuoteDetail,
  QuotePage,
  QuoteQuery,
} from './beleg.model';

/** Typisierter Zugriff auf die Beleg-API (dev-Proxy: /api -> :8000). */
@Injectable({ providedIn: 'root' })
export class BelegService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/invoicing/quotes';

  list(query: QuoteQuery): Observable<QuotePage> {
    let params = new HttpParams()
      .set('page', query.page)
      .set('page_size', query.page_size);
    const q = query.q?.trim();
    if (q) params = params.set('q', q);
    if (query.status) params = params.set('status', query.status);
    if (query.property_id) params = params.set('property_id', query.property_id);
    if (query.project_id) params = params.set('project_id', query.project_id);
    return this.http.get<QuotePage>(this.base, { params });
  }

  get(id: string): Observable<QuoteDetail> {
    return this.http.get<QuoteDetail>(`${this.base}/${id}`);
  }

  listInvoices(query: InvoiceQuery): Observable<InvoicePage> {
    let params = new HttpParams()
      .set('page', query.page)
      .set('page_size', query.page_size);
    const q = query.q?.trim();
    if (q) params = params.set('q', q);
    if (query.status) params = params.set('status', query.status);
    if (query.invoice_type) params = params.set('invoice_type', query.invoice_type);
    if (query.property_id) params = params.set('property_id', query.property_id);
    if (query.project_id) params = params.set('project_id', query.project_id);
    return this.http.get<InvoicePage>('/api/invoicing/invoices', { params });
  }

  getInvoice(id: string): Observable<InvoiceDetail> {
    return this.http.get<InvoiceDetail>(`/api/invoicing/invoices/${id}`);
  }

  // --- Schreibend (Session-Auth Pflicht) -----------------------------------

  /** Neues Angebot (Status ENTWURF) mit Positionen anlegen. */
  createQuote(payload: QuoteCreate): Observable<QuoteDetail> {
    return this.http.post<QuoteDetail>(this.base, payload);
  }

  /** Angebot versenden — unumkehrbar: DB vergibt die AN-Nummer und friert ein. */
  sendQuote(id: string): Observable<QuoteDetail> {
    return this.http.post<QuoteDetail>(`${this.base}/${id}/send`, {});
  }

  /** Neue Rechnung/Gutschrift (Status ENTWURF) mit Positionen anlegen. */
  createInvoice(payload: InvoiceCreate): Observable<InvoiceDetail> {
    return this.http.post<InvoiceDetail>('/api/invoicing/invoices', payload);
  }

  /** Beteiligten (Schuldner/Empfänger …) am Rechnungsentwurf ergänzen. */
  addInvoiceParty(id: string, payload: InvoicePartyCreate): Observable<InvoiceDetail> {
    return this.http.post<InvoiceDetail>(`/api/invoicing/invoices/${id}/parties`, payload);
  }

  /** Rechnung veröffentlichen — unumkehrbar: DB vergibt die Belegnummer,
   *  friert ein und prüft die Freigabe-Tore (422 bei Verstoß). */
  publishInvoice(id: string): Observable<InvoiceDetail> {
    return this.http.post<InvoiceDetail>(`/api/invoicing/invoices/${id}/publish`, {});
  }
}
