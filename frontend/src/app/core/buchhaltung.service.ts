import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  DunningList,
  OpenItemDetail,
  OpenItemPage,
  OpenItemQuery,
} from './buchhaltung.model';

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
}
