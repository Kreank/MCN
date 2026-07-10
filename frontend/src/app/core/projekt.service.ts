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
  ServiceCaseBoard,
  ServiceCaseBoardQuery,
  ServiceCaseCreate,
  ServiceCaseDetail,
  ServiceCaseRef,
  ServiceCaseStatusInput,
  ServiceCaseTransition,
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

  /**
   * Vorgänge über alle Projekte fürs Kanban-Board (Recht workflow.LESEN).
   * Liefert zusätzlich die Spalten (Statuskatalog). Ohne status-Filter werden
   * Endspalten-Vorgänge nur mit include_terminal geladen.
   */
  listServiceCases(query: ServiceCaseBoardQuery = {}): Observable<ServiceCaseBoard> {
    let params = new HttpParams();
    if (query.project_id) params = params.set('project_id', query.project_id);
    if (query.status) params = params.set('status', query.status);
    const q = query.q?.trim();
    if (q) params = params.set('q', q);
    if (query.include_terminal) params = params.set('include_terminal', 'true');
    if (query.page) params = params.set('page', query.page);
    if (query.page_size) params = params.set('page_size', query.page_size);
    return this.http.get<ServiceCaseBoard>('/api/workflow/service_cases', { params });
  }

  getServiceCase(id: string): Observable<ServiceCaseDetail> {
    return this.http.get<ServiceCaseDetail>(
      `/api/workflow/service_cases/${id}`,
    );
  }

  /** Erlaubte nächste Status eines Vorgangs (Recht workflow.LESEN). */
  getServiceCaseTransitions(id: string): Observable<ServiceCaseTransition[]> {
    return this.http.get<ServiceCaseTransition[]>(
      `/api/workflow/service_cases/${id}/transitions`,
    );
  }

  /**
   * Statuswechsel eines Vorgangs durchführen. Recht workflow.AENDERN — außer
   * Wechsel nach BEAUFTRAGT (Beauftragung/Freigabe), den der Server als
   * Freigabetor mit workflow.FREIGEBEN prüft.
   */
  advanceServiceCaseStatus(
    id: string,
    payload: ServiceCaseStatusInput,
  ): Observable<ServiceCaseDetail> {
    return this.http.post<ServiceCaseDetail>(
      `/api/workflow/service_cases/${id}/status`,
      payload,
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

