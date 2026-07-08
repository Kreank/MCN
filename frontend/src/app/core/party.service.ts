import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { PartyDetail, PartyPage, PartyQuery } from './party.model';

/** Typisierter Zugriff auf die Kontakte-API (dev-Proxy: /api -> :8000). */
@Injectable({ providedIn: 'root' })
export class PartyService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/identity/parties';

  list(query: PartyQuery): Observable<PartyPage> {
    let params = new HttpParams()
      .set('page', query.page)
      .set('page_size', query.page_size);
    const q = query.q?.trim();
    if (q) {
      params = params.set('q', q);
    }
    if (query.party_type) {
      params = params.set('party_type', query.party_type);
    }
    return this.http.get<PartyPage>(this.base, { params });
  }

  get(id: string): Observable<PartyDetail> {
    return this.http.get<PartyDetail>(`${this.base}/${id}`);
  }
}
