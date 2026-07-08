import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { WorkOrderDetail, WorkOrderPage, WorkOrderQuery } from './auftrag.model';

/** Typisierter Zugriff auf die Auftrags-API (dev-Proxy: /api -> :8000). */
@Injectable({ providedIn: 'root' })
export class AuftragService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/workflow/work_orders';

  list(query: WorkOrderQuery): Observable<WorkOrderPage> {
    let params = new HttpParams()
      .set('page', query.page)
      .set('page_size', query.page_size);
    const q = query.q?.trim();
    if (q) params = params.set('q', q);
    if (query.status) params = params.set('status', query.status);
    if (query.project_id) params = params.set('project_id', query.project_id);
    if (query.property_id) params = params.set('property_id', query.property_id);
    if (query.service_case_id) {
      params = params.set('service_case_id', query.service_case_id);
    }
    return this.http.get<WorkOrderPage>(this.base, { params });
  }

  get(id: string): Observable<WorkOrderDetail> {
    return this.http.get<WorkOrderDetail>(`${this.base}/${id}`);
  }
}
