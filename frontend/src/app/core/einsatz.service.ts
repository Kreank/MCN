import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  Plantafel,
  ServiceJobDetail,
  ServiceJobPage,
  ServiceJobQuery,
} from './einsatz.model';

/** Typisierter Zugriff auf die Planungs-/Einsatz-API (dev-Proxy: /api -> :8000). */
@Injectable({ providedIn: 'root' })
export class EinsatzService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/planung/einsaetze';

  list(query: ServiceJobQuery): Observable<ServiceJobPage> {
    let params = new HttpParams()
      .set('page', query.page)
      .set('page_size', query.page_size);
    const q = query.q?.trim();
    if (q) params = params.set('q', q);
    if (query.status) params = params.set('status', query.status);
    if (query.work_order_id) params = params.set('work_order_id', query.work_order_id);
    return this.http.get<ServiceJobPage>(this.base, { params });
  }

  get(id: string): Observable<ServiceJobDetail> {
    return this.http.get<ServiceJobDetail>(`${this.base}/${id}`);
  }

  /** Plantafel-Board für einen Zeitraum (Bahnen + verplante Einsätze). */
  plantafel(dateFrom: string, dateTo: string): Observable<Plantafel> {
    const params = new HttpParams()
      .set('date_from', dateFrom)
      .set('date_to', dateTo);
    return this.http.get<Plantafel>('/api/planung/plantafel', { params });
  }
}
