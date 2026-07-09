import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  JobAssignment,
  JobAssignmentInput,
  JobStatusInput,
  MaterialEntry,
  MaterialLogInput,
  Plantafel,
  ScheduleInput,
  ServiceJob,
  ServiceJobCreate,
  ServiceJobDetail,
  ServiceJobPage,
  ServiceJobQuery,
  TimeEntry,
  TimeLogInput,
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

  // --- Schreiben (Session-Auth Pflicht) ------------------------------------
  /** Neuen Einsatz anlegen (Status UNGEPLANT; Recht workflow.ANLEGEN, ALLE). */
  create(payload: ServiceJobCreate): Observable<ServiceJob> {
    return this.http.post<ServiceJob>(this.base, payload);
  }

  /** Planzeitraum setzen (Recht workflow.AENDERN, Disposition). */
  setSchedule(id: string, payload: ScheduleInput): Observable<ServiceJob> {
    return this.http.post<ServiceJob>(`${this.base}/${id}/schedule`, payload);
  }

  /** Statuswechsel (Recht workflow.AENDERN, Disposition). */
  advanceStatus(id: string, payload: JobStatusInput): Observable<ServiceJob> {
    return this.http.post<ServiceJob>(`${this.base}/${id}/status`, payload);
  }

  /** Mitarbeiter zuweisen (Recht workflow.AENDERN, Disposition). */
  assign(id: string, payload: JobAssignmentInput): Observable<JobAssignment> {
    return this.http.post<JobAssignment>(`${this.base}/${id}/assignments`, payload);
  }

  /** Zeit buchen (Recht workflow.AENDERN; auch Monteur auf eigenen Einsätzen). */
  logTime(id: string, payload: TimeLogInput): Observable<TimeEntry> {
    return this.http.post<TimeEntry>(`${this.base}/${id}/times`, payload);
  }

  /** Material buchen (Recht workflow.AENDERN; auch Monteur auf eigenen Einsätzen). */
  logMaterial(id: string, payload: MaterialLogInput): Observable<MaterialEntry> {
    return this.http.post<MaterialEntry>(`${this.base}/${id}/materials`, payload);
  }
}
