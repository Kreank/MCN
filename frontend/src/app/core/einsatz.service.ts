import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  Abwesend,
  AnrufIn,
  AnrufResult,
  AssignableUser,
  BoardJob,
  JobAssignmentInput,
  JobAssignmentResult,
  JobStatusInput,
  MaterialArtikel,
  MaterialEntry,
  MaterialLogInput,
  Plantafel,
  PlantafelQuery,
  ScheduleInput,
  ScheduleResult,
  SerieCreate,
  SerieResult,
  SerienTermin,
  ServiceJob,
  ServiceJobCreate,
  ServiceJobDetail,
  ServiceJobPage,
  ServiceJobQuery,
  ServiceJobUpdate,
  TerminCreate,
  TerminResult,
  TerminUpdate,
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
    if (query.scheduled_from) params = params.set('scheduled_from', query.scheduled_from);
    if (query.scheduled_to) params = params.set('scheduled_to', query.scheduled_to);
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

  /**
   * Plantafel-Board für einen Zeitraum: Bahnen (alle aktiven, auch leere),
   * Kacheln (alle Einsätze, die den Zeitraum ÜBERLAPPEN), Rückstand
   * (ungeplante Einsätze), Abwesenheiten, Feiertage und Konflikte.
   */
  plantafel(query: PlantafelQuery): Observable<Plantafel> {
    let params = new HttpParams()
      .set('date_from', query.date_from)
      .set('date_to', query.date_to);
    const q = query.q?.trim();
    if (q) params = params.set('q', q);
    if (query.category_id) params = params.set('category_id', query.category_id);
    if (query.trade_id) params = params.set('trade_id', query.trade_id);
    const bq = query.backlog_q?.trim();
    if (bq) params = params.set('backlog_q', bq);
    return this.http.get<Plantafel>('/api/planung/plantafel', { params });
  }

  /**
   * „Wer ist gerade nicht da?" — genehmigte Abwesenheiten im Zeitraum
   * (Default: heute). **Ohne Abwesenheitsart** (DSGVO Art. 9, siehe `Abwesend`).
   * Recht: `workflow/LESEN` — die Disposition darf das, ohne `hr` zu haben.
   */
  abwesend(von?: string, bis?: string): Observable<Abwesend[]> {
    let params = new HttpParams();
    if (von) params = params.set('von', von);
    if (bis) params = params.set('bis', bis);
    return this.http.get<Abwesend[]>('/api/planung/abwesend', { params });
  }

  /**
   * Termin anlegen — Einsatz, Kategorie, Mitarbeiter und Betriebsmittel in EINEM
   * Vorgang (der Server klammert alles in eine Transaktion). Ohne
   * `scheduled_start` landet der Termin bewusst im Rückstand.
   */
  createTermin(payload: TerminCreate): Observable<TerminResult> {
    return this.http.post<TerminResult>('/api/planung/termine', payload);
  }

  /**
   * Anruf-Durchstich: Kunde, Ort, Auftrag und Termin aus einem Telefonat — in
   * EINER Transaktion. Der Auftrag entsteht direkt freigegeben (das Telefonat
   * ist der Beauftragungsnachweis), damit der Monteur am Termintag losfahren
   * darf; ohne `termin.scheduled_start` landet der Termin im Rückstand.
   *
   * Erfordert workflow.ANLEGEN + AENDERN + FREIGEBEN und, je nachdem ob Kontakt
   * und Liegenschaft neu sind, identity/property ANLEGEN statt nur LESEN.
   */
  anruf(payload: AnrufIn): Observable<AnrufResult> {
    return this.http.post<AnrufResult>('/api/planung/anruf', payload);
  }

  /** Termin ändern (Vollersetzung der Zuweisungs-/Ressourcenlisten). */
  updateTermin(id: string, payload: TerminUpdate): Observable<TerminResult> {
    return this.http.patch<TerminResult>(`/api/planung/termine/${id}`, payload);
  }

  /**
   * Termin wiederholen — erzeugt echte, eigenständige Folgetermine (kein
   * virtuelles Vorkommen). Mitarbeiter, Ressourcen, Kategorie und Dauer werden
   * mitkopiert; jeder Folgetermin hat eigene Nummer und eigenen Status.
   */
  serieAnlegen(id: string, payload: SerieCreate): Observable<SerieResult> {
    return this.http.post<SerieResult>(`/api/planung/termine/${id}/serie`, payload);
  }

  /** Alle Termine der Serie (leer, wenn der Termin zu keiner gehört). */
  serie(id: string): Observable<SerienTermin[]> {
    return this.http.get<SerienTermin[]>(`/api/planung/termine/${id}/serie`);
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

  /**
   * **Preisfreie** Artikelsuche für die Materialbuchung (Recht workflow.AENDERN).
   *
   * Bewusst NICHT `GET /api/pricing/articles`: Jener liefert `list_price` und
   * hängt an `pricing/LESEN` — ein Recht, das der Monteur nicht hat und nicht
   * bekommen soll. Diese Antwort führt strukturell kein Geldfeld.
   */
  materialArtikelSuche(q: string, limit = 20): Observable<MaterialArtikel[]> {
    const params = new HttpParams().set('q', q).set('limit', String(limit));
    return this.http.get<MaterialArtikel[]>('/api/planung/material-artikel', {
      params,
    });
  }
}
