import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  AssignableUser,
  JobAssignmentInput,
  JobAssignmentResult,
  JobStatusInput,
  MaterialEntry,
  MaterialLogInput,
  Plantafel,
  ScheduleInput,
  ScheduleResult,
  ServiceJob,
  ServiceJobCreate,
  ServiceJobDetail,
  ServiceJobPage,
  ServiceJobQuery,
  ServiceJobUpdate,
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

  /**
   * Aktive Benutzer als schlanke Zuweisungs-Auswahlliste (id + Name). Speist
   * Einsatz-Zuweisung und Mitarbeiter-Anlage (app_user_id). Recht workflow.LESEN;
   * ein Monteur (Scope EIGENE) bekommt bewusst 403.
   */
  listUsers(q?: string): Observable<AssignableUser[]> {
    let params = new HttpParams();
    const needle = q?.trim();
    if (needle) params = params.set('q', needle);
    return this.http.get<AssignableUser[]>('/api/planung/users', { params });
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

  /**
   * Angaben am Einsatz nachtragen — vor allem den Ansprechpartner vor Ort
   * (bei einer Begehung ist der Kontakt oft erst nach dem Termin bekannt).
   * Nur gesetzte Felder werden geändert; `null` löscht das Feld.
   * Recht workflow.AENDERN; ein Monteur darf auf seinem eigenen Einsatz nur
   * Kontakt und Zutrittshinweise nachtragen (Titel/Liegenschaft → 403).
   */
  update(id: string, payload: ServiceJobUpdate): Observable<ServiceJobDetail> {
    return this.http.patch<ServiceJobDetail>(`${this.base}/${id}`, payload);
  }

  /**
   * Planzeitraum setzen (Recht workflow.AENDERN, Disposition) — speist auch das
   * Verschieben einer Kachel auf der Plantafel. Die Antwort trägt `warnings`
   * (Doppelbelegung im neuen Fenster): nicht blockierend, aber anzuzeigen.
   */
  setSchedule(id: string, payload: ScheduleInput): Observable<ScheduleResult> {
    return this.http.post<ScheduleResult>(`${this.base}/${id}/schedule`, payload);
  }

  /** Statuswechsel (Recht workflow.AENDERN, Disposition). */
  advanceStatus(id: string, payload: JobStatusInput): Observable<ServiceJob> {
    return this.http.post<ServiceJob>(`${this.base}/${id}/status`, payload);
  }

  /** Mitarbeiter zuweisen (Recht workflow.AENDERN, Disposition). Antwort mit
   * nicht-blockierenden Doppelbelegungs-Hinweisen. */
  assign(id: string, payload: JobAssignmentInput): Observable<JobAssignmentResult> {
    return this.http.post<JobAssignmentResult>(`${this.base}/${id}/assignments`, payload);
  }

  /**
   * Zuweisung aufheben (Recht workflow.AENDERN, Disposition) — nötig, wenn eine
   * Kachel auf der Plantafel die Bahn wechselt. Nach Einsatzabschluss sperrt der
   * DB-Trigger (Historienschutz) → 422.
   */
  unassign(id: string, assigneeUserId: string): Observable<{ detail: string }> {
    return this.http.delete<{ detail: string }>(
      `${this.base}/${id}/assignments/${assigneeUserId}`,
    );
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
