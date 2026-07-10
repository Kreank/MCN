import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  AssignableUser,
  Task,
  TaskCreate,
  TaskPage,
  TaskQuery,
  TaskUpdate,
} from './aufgabe.model';

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

  /**
   * Aufgabe bearbeiten (Recht workflow.AENDERN). Nur gesendete Felder werden
   * geändert; Statuswechsel läuft über die eigenen Aktionen, nicht hierüber.
   */
  update(id: string, payload: TaskUpdate): Observable<Task> {
    return this.http.patch<Task>(`${this.base}/${id}`, payload);
  }

  /**
   * Aktive Benutzer als schlanke Zuweisungs-Auswahlliste (id + Name) für die
   * Fremdzuweisung. Recht workflow.LESEN mit Scope ALLE — ein Monteur (EIGENE)
   * bekommt bewusst 403 und sieht die Auswahl im UI gar nicht erst.
   */
  listAssignableUsers(q?: string): Observable<AssignableUser[]> {
    let params = new HttpParams();
    const needle = q?.trim();
    if (needle) params = params.set('q', needle);
    return this.http.get<AssignableUser[]>('/api/planung/users', { params });
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
