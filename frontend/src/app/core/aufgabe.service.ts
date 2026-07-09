import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { Task, TaskCreate, TaskPage, TaskQuery } from './aufgabe.model';

/** Typisierter Zugriff auf die Aufgaben-API (dev-Proxy: /api -> :8000). */
@Injectable({ providedIn: 'root' })
export class AufgabeService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/workflow/tasks';

  list(query: TaskQuery): Observable<TaskPage> {
    let params = new HttpParams()
      .set('page', query.page)
      .set('page_size', query.page_size);
    const q = query.q?.trim();
    if (q) params = params.set('q', q);
    if (query.status) params = params.set('status', query.status);
    if (query.project_id) params = params.set('project_id', query.project_id);
    if (query.party_id) params = params.set('party_id', query.party_id);
    return this.http.get<TaskPage>(this.base, { params });
  }

  /** Neue Aufgabe anlegen (Status OFFEN). Erfordert Recht workflow.ANLEGEN. */
  create(payload: TaskCreate): Observable<Task> {
    return this.http.post<Task>(this.base, payload);
  }

  // Statusaktionen (erfordern Session; UI-Verdrahtung folgt mit Auth).
  complete(id: string): Observable<Task> {
    return this.http.post<Task>(`${this.base}/${id}/complete`, {});
  }
  discard(id: string): Observable<Task> {
    return this.http.post<Task>(`${this.base}/${id}/discard`, {});
  }
  reopen(id: string): Observable<Task> {
    return this.http.post<Task>(`${this.base}/${id}/reopen`, {});
  }
}
