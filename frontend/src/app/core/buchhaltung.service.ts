import { HttpClient, HttpParams, HttpResponse } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';
import {
  CorrectionInput,
  CreditOutcome,
  DunningEmailResult,
  DunningIssue,
  DunningList,
  DunningNotice,
  OpenItemDetail,
  OpenItemPage,
  OpenItemQuery,
  PaymentDetail,
  PaymentRecord,
  PendingApproval,
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

  /**
   * Eine ausgestellte Mahnung/Zahlungserinnerung als E-Mail an den Schuldner
   * senden (Rechnung als PDF-Anhang). `toAddress` überschreibt die serverseitig
   * ermittelte Schuldner-Adresse. Reine Zustellung; Fehler (kein Empfänger/Konto,
   * SMTP) kommen als 422, unbekannte Mahnung als 404.
   */
  sendDunningEmail(noticeId: string, toAddress: string): Observable<DunningEmailResult> {
    return this.http.post<DunningEmailResult>(
      `${this.base}/dunning-notices/${noticeId}/send-email`,
      { to_address: toAddress },
    );
  }

  /**
   * Veröffentlichte Rechnung stornieren (Stornobeleg). Vier-Augen-pflichtig:
   * Der Endpunkt liefert 201 (Folgebeleg erzeugt) ODER 202 (Freigabeantrag
   * angelegt, wartet auf Genehmigung). Beide Fälle werden über den HTTP-Status
   * unterschieden — deshalb `observe: 'response'` statt nur des Bodys.
   */
  cancelInvoice(invoiceId: string): Observable<CreditOutcome> {
    return this.http
      .post<CreditRef | PendingApproval>(
        `${this.base}/invoices/${invoiceId}/cancel`,
        {},
        { observe: 'response' },
      )
      .pipe(map((res) => this.deuteFolgebeleg(res)));
  }

  /**
   * Rechnungskorrektur (Gutschrift) über die angegebenen Positionen. Ebenfalls
   * Vier-Augen-pflichtig (201 erzeugt / 202 wartet auf Freigabe).
   */
  correctInvoice(invoiceId: string, payload: CorrectionInput): Observable<CreditOutcome> {
    return this.http
      .post<CreditRef | PendingApproval>(
        `${this.base}/invoices/${invoiceId}/correction`,
        payload,
        { observe: 'response' },
      )
      .pipe(map((res) => this.deuteFolgebeleg(res)));
  }

  /**
   * Deutet die Antwort von Storno/Korrektur: 202 = Freigabeantrag angelegt
   * (noch nichts geschrieben), sonst (201) = Folgebeleg erzeugt.
   */
  private deuteFolgebeleg(
    res: HttpResponse<CreditRef | PendingApproval>,
  ): CreditOutcome {
    if (res.status === 202) {
      return { kind: 'wartet', pending: res.body as PendingApproval };
    }
    return { kind: 'erzeugt', credit: res.body as CreditRef };
  }
}
