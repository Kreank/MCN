import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { QuoteDetail, QuotePage, QuoteQuery } from './beleg.model';

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
}
