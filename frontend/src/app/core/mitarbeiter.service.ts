import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { EmployeeDetail, EmployeePage, EmployeeQuery } from './mitarbeiter.model';

/** Typisierter Zugriff auf die Personal-API (dev-Proxy: /api -> :8000). */
@Injectable({ providedIn: 'root' })
export class MitarbeiterService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/hr/employees';

  list(query: EmployeeQuery): Observable<EmployeePage> {
    let params = new HttpParams()
      .set('page', query.page)
      .set('page_size', query.page_size);
    const q = query.q?.trim();
    if (q) params = params.set('q', q);
    if (query.status) params = params.set('status', query.status);
    return this.http.get<EmployeePage>(this.base, { params });
  }

  get(id: string): Observable<EmployeeDetail> {
    return this.http.get<EmployeeDetail>(`${this.base}/${id}`);
  }
}
