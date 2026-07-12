import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  InvoiceCreate,
  InvoiceUpdate,
  InvoiceDetail,
  InvoiceEmailResult,
  InvoicePage,
  InvoicePartyCreate,
  InvoiceQuery,
  Kalkulation,
  QuoteCreate,
  QuoteDetail,
  QuoteEmailResult,
  QuotePage,
  QuoteQuery,
  QuoteUpdate,
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

  /**
   * Interne Kalkulationsübersicht je Abschnitt (EK, Deckungsbeitrag, Marge).
   * Verlangt `pricing/LESEN` — wer den Beleg lesen darf, sieht nicht zwingend die
   * Marge. Ein 403 ist hier also ein normaler Zustand, kein Fehler.
   */
  kalkulation(id: string): Observable<Kalkulation> {
    return this.http.get<Kalkulation>(`${this.base}/${id}/kalkulation`);
  }

  invoiceKalkulation(id: string): Observable<Kalkulation> {
    return this.http.get<Kalkulation>(`/api/invoicing/invoices/${id}/kalkulation`);
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

  /**
   * Angebotsentwurf speichern (PUT). Positionen und Abschnitte werden vollständig
   * ersetzt — der Editor schickt immer den ganzen Beleg. 422, wenn das Angebot
   * bereits versendet ist. Der Server berechnet Summen und Kalkulation neu.
   */
  updateQuote(id: string, payload: QuoteUpdate): Observable<QuoteDetail> {
    return this.http.put<QuoteDetail>(`${this.base}/${id}`, payload);
  }

  /** Angebot versenden — unumkehrbar: DB vergibt die AN-Nummer und friert ein. */
  sendQuote(id: string): Observable<QuoteDetail> {
    return this.http.post<QuoteDetail>(`${this.base}/${id}/send`, {});
  }

  /**
   * Versendetes Angebot als PDF-Anhang per E-Mail versenden. `toAddress`
   * überschreibt die serverseitig best-effort abgeleitete Empfänger-Adresse.
   * Reine Zustellung (kein Statuswechsel); Fehler (kein Empfänger/Konto, SMTP)
   * kommen als 422.
   */
  sendQuoteEmail(id: string, toAddress: string): Observable<QuoteEmailResult> {
    return this.http.post<QuoteEmailResult>(
      `${this.base}/${id}/send-email`,
      { to_address: toAddress },
    );
  }

  /** Neue Rechnung/Gutschrift (Status ENTWURF) mit Positionen anlegen. */
  createInvoice(payload: InvoiceCreate): Observable<InvoiceDetail> {
    return this.http.post<InvoiceDetail>('/api/invoicing/invoices', payload);
  }

  /** Rechnungsentwurf ändern (Positionen/Abschnitte werden vollständig ersetzt). */
  updateInvoice(id: string, payload: InvoiceUpdate): Observable<InvoiceDetail> {
    return this.http.put<InvoiceDetail>(`/api/invoicing/invoices/${id}`, payload);
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

  /**
   * Veröffentlichte Rechnung als PDF-Anhang per E-Mail versenden. `toAddress`
   * überschreibt die serverseitig ermittelte Empfänger-Adresse. Reine Zustellung
   * (kein Statuswechsel); Fehler (kein Empfänger/Konto, SMTP) kommen als 422.
   */
  sendInvoiceEmail(id: string, toAddress: string): Observable<InvoiceEmailResult> {
    return this.http.post<InvoiceEmailResult>(
      `/api/invoicing/invoices/${id}/send-email`,
      { to_address: toAddress },
    );
  }

  /**
   * E-Rechnung (ZUGFeRD/Factur-X): Hybrid-PDF (PDF/A-3 mit eingebettetem
   * CII-XML) einer veröffentlichten Rechnung.
   *
   * Bewusst als Blob über den HttpClient und NICHT als `window.open`/Direkt-URL:
   * nur so gehen Session-Cookie und CSRF-Header durch den Interceptor. Lässt die
   * Datenlage kein gültiges EN16931-XML zu (kein Firmenprofil, kein Empfänger),
   * antwortet der Server mit 422 — der Fehlerkörper ist dann ein Blob, den der
   * Aufrufer als Text liest (Muster wie beim DATEV-Export).
   */
  zugferdPdf(id: string): Observable<Blob> {
    return this.http.get(`/api/invoicing/invoices/${id}/zugferd.pdf`, {
      responseType: 'blob',
    });
  }
}
