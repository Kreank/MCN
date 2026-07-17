import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  ErsatzteilDetail,
  ErsatzteilPage,
  ErsatzteilQuery,
  Hersteller,
} from './geraetewissen.model';

/**
 * Typisierter Zugriff auf die Gerätewissen-API (dev-Proxy: /api -> :8000).
 * Read-only: gefilterte Sicht auf Hersteller-Ersatzteile aus `pricing.article`.
 */
@Injectable({ providedIn: 'root' })
export class GeraetewissenService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/geraetewissen';

  /** Die konfigurierten Hersteller mit Ersatzteilzahl (Filter-Chips). */
  listHersteller(): Observable<Hersteller[]> {
    return this.http.get<Hersteller[]>(`${this.base}/hersteller`);
  }

  /** Ersatzteile suchen/auflisten. Volltext + Hersteller-Filter + Paginierung. */
  listErsatzteile(query: ErsatzteilQuery): Observable<ErsatzteilPage> {
    let params = new HttpParams()
      .set('page', query.page)
      .set('page_size', query.page_size);
    const q = query.q?.trim();
    if (q) params = params.set('q', q);
    if (query.namespace) params = params.set('namespace', query.namespace);
    return this.http.get<ErsatzteilPage>(`${this.base}/ersatzteile`, { params });
  }

  /** Voll-Detail eines Ersatzteils (read-only). */
  getErsatzteil(id: string): Observable<ErsatzteilDetail> {
    return this.http.get<ErsatzteilDetail>(`${this.base}/ersatzteile/${id}`);
  }
}
