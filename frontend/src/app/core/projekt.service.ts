import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  ProjectDetail,
  ProjectPage,
  ProjectQuery,
  ServiceCaseDetail,
} from './projekt.model';

/** Typisierter Zugriff auf die Projekt-API (dev-Proxy: /api -> :8000). */
@Injectable({ providedIn: 'root' })
export class ProjektService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/workflow/projects';

  list(query: ProjectQuery): Observable<ProjectPage> {
    let params = new HttpParams()
      .set('page', query.page)
      .set('page_size', query.page_size);
    const q = query.q?.trim();
    if (q) params = params.set('q', q);
    if (query.status) params = params.set('status', query.status);
    if (query.category_id) params = params.set('category_id', query.category_id);
    return this.http.get<ProjectPage>(this.base, { params });
  }

  get(id: string): Observable<ProjectDetail> {
    return this.http.get<ProjectDetail>(`${this.base}/${id}`);
  }

  getServiceCase(id: string): Observable<ServiceCaseDetail> {
    return this.http.get<ServiceCaseDetail>(
      `/api/workflow/service_cases/${id}`,
    );
  }
}

