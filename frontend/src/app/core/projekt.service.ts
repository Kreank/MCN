import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  Checklist,
  ChecklistCreate,
  LogEntry,
  LogEntryCreate,
  ProjectCreate,
  ProjectDetail,
  ProjectPage,
  ProjectQuery,
  ServiceCaseCreate,
  ServiceCaseDetail,
  ServiceCaseRef,
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

  getProjectLog(id: string): Observable<LogEntry[]> {
    return this.http.get<LogEntry[]>(`${this.base}/${id}/log`);
  }

  getChecklists(id: string): Observable<Checklist[]> {
    return this.http.get<Checklist[]>(`${this.base}/${id}/checklists`);
  }

  // --- Schreiben (Session-Auth Pflicht) ------------------------------------
  /** Neues Projekt anlegen (Recht workflow.ANLEGEN). */
  create(payload: ProjectCreate): Observable<ProjectDetail> {
    return this.http.post<ProjectDetail>(this.base, payload);
  }

  /** Logbuch-Eintrag anlegen (Recht workflow.AENDERN). */
  addLog(id: string, payload: LogEntryCreate): Observable<LogEntry> {
    return this.http.post<LogEntry>(`${this.base}/${id}/log`, payload);
  }

  /** Checkliste anlegen (Recht workflow.ANLEGEN). */
  createChecklist(id: string, payload: ChecklistCreate): Observable<Checklist> {
    return this.http.post<Checklist>(`${this.base}/${id}/checklists`, payload);
  }

  /** Vorgang unter dem Projekt anlegen (Recht workflow.ANLEGEN). */
  createServiceCase(id: string, payload: ServiceCaseCreate): Observable<ServiceCaseRef> {
    return this.http.post<ServiceCaseRef>(`${this.base}/${id}/service_cases`, payload);
  }
}

