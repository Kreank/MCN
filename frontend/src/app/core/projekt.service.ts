import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  Checklist,
  ChecklistCreate,
  LogEntry,
  LogEntryCreate,
  ProjectCategory,
  ProjectCreate,
  ProjectDetail,
  ProjectInternalNoteInput,
  ProjectPage,
  ProjectQuery,
  ProjectResponsibleInput,
  QuickIntakeIn,
  QuickIntakeOut,
  ServiceCaseBoard,
  ServiceCaseBoardQuery,
  ServiceCaseCreate,
  ServiceCaseDetail,
  ServiceCaseRef,
  ServiceCaseStatusInput,
  ServiceCaseTransition,
  UserRef,
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
    if (query.property_id) params = params.set('property_id', query.property_id);
    return this.http.get<ProjectPage>(this.base, { params });
  }

  get(id: string): Observable<ProjectDetail> {
    return this.http.get<ProjectDetail>(`${this.base}/${id}`);
  }

  /** Aktive Projektkategorien (Gewerk/Ordner) für den Anlagedialog (Recht workflow.LESEN). */
  listCategories(): Observable<ProjectCategory[]> {
    return this.http.get<ProjectCategory[]>('/api/workflow/project-categories');
  }

  /**
   * Aktive Benutzer als schlanke Zuweisungs-Auswahlliste (id + Name) für den
   * Verantwortlichen. Recht workflow.LESEN mit Scope ALLE — ein Monteur (EIGENE)
   * bekommt bewusst 403 und sieht die Auswahl im UI gar nicht erst.
   */
  listAssignableUsers(q?: string): Observable<UserRef[]> {
    let params = new HttpParams();
    const needle = q?.trim();
    if (needle) params = params.set('q', needle);
    return this.http.get<UserRef[]>('/api/planung/users', { params });
  }

  /**
   * Vorgänge über alle Projekte fürs Kanban-Board (Recht workflow.LESEN).
   * Liefert zusätzlich die Spalten (Statuskatalog). Ohne status-Filter werden
   * Endspalten-Vorgänge nur mit include_terminal geladen.
   */
  listServiceCases(query: ServiceCaseBoardQuery = {}): Observable<ServiceCaseBoard> {
    let params = new HttpParams();
    if (query.project_id) params = params.set('project_id', query.project_id);
    if (query.property_id) params = params.set('property_id', query.property_id);
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

  /**
   * Verantwortlichen setzen/entfernen (Recht workflow.AENDERN). `responsible_user_id
   * = null` entfernt die Zuweisung. Liefert das aktualisierte Projektdetail.
   */
  setResponsible(id: string, payload: ProjectResponsibleInput): Observable<ProjectDetail> {
    return this.http.post<ProjectDetail>(`${this.base}/${id}/responsible`, payload);
  }

  /**
   * Freies Notizfeld setzen/leeren (Recht workflow.AENDERN, Projekte-7).
   * `internal_note = null`/leer entfernt die Notiz. Liefert das aktualisierte
   * Projektdetail.
   */
  setInternalNote(id: string, payload: ProjectInternalNoteInput): Observable<ProjectDetail> {
    return this.http.post<ProjectDetail>(`${this.base}/${id}/internal-note`, payload);
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

  /**
   * Vorgang zum Projekt hochstufen: legt ein neues Projekt an und hängt den
   * Vorgang samt Aufträgen darunter (Recht workflow.ANLEGEN). `name` = null/leer
   * lässt der Server auf den Vorgangsbetreff zurückfallen. Liefert das neue
   * Projekt (gleiche Struktur wie GET /api/workflow/projects/{id}).
   */
  promoteToProject(
    caseId: string,
    payload: { name: string | null },
  ): Observable<ProjectDetail> {
    return this.http.post<ProjectDetail>(
      `/api/workflow/service_cases/${caseId}/promote-to-project`,
      payload,
    );
  }

  /**
   * Schnelleinstieg „Meldung erfassen": legt Person + Liegenschaft + Vorgang
   * (ohne Projekt) atomar in EINEM Aufruf an. Der Server prüft die Tore der
   * beteiligten Bereiche (identity.ANLEGEN, property.ANLEGEN/AENDERN,
   * workflow.ANLEGEN) und rollt bei einem Fehler alles zurück.
   */
  quickIntake(payload: QuickIntakeIn): Observable<QuickIntakeOut> {
    return this.http.post<QuickIntakeOut>('/api/workflow/quick-intake', payload);
  }
}

