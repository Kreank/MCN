import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  EvidenceInput,
  Kundenhistorie,
  ResponsibilityInput,
  WorkOrderCreate,
  WorkOrderDetail,
  WorkOrderPage,
  WorkOrderPartyCreate,
  WorkOrderQuery,
  WorkOrderStatusInput,
} from './auftrag.model';

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

  /** Auftraggeber + Kundenhistorie (Anzahl Aufträge/Termine des Kunden). */
  kundenhistorie(id: string): Observable<Kundenhistorie> {
    return this.http.get<Kundenhistorie>(`${this.base}/${id}/kundenhistorie`);
  }

  get(id: string): Observable<WorkOrderDetail> {
    return this.http.get<WorkOrderDetail>(`${this.base}/${id}`);
  }

  // --- Schreiben (Session-Auth Pflicht) ------------------------------------
  /** Neuen Auftrag anlegen (Status ENTWURF; Recht workflow.ANLEGEN). */
  create(payload: WorkOrderCreate): Observable<WorkOrderDetail> {
    return this.http.post<WorkOrderDetail>(this.base, payload);
  }

  /** Beteiligten hinzufügen (Recht workflow.AENDERN). */
  addParty(id: string, payload: WorkOrderPartyCreate): Observable<WorkOrderDetail> {
    return this.http.post<WorkOrderDetail>(`${this.base}/${id}/parties`, payload);
  }

  /** Verantwortungsbereich bestätigen (Recht workflow.AENDERN). */
  confirmResponsibility(id: string, payload: ResponsibilityInput): Observable<WorkOrderDetail> {
    return this.http.post<WorkOrderDetail>(`${this.base}/${id}/responsibility`, payload);
  }

  /** Beauftragungsnachweis setzen (Recht workflow.AENDERN). */
  setEvidence(id: string, payload: EvidenceInput): Observable<WorkOrderDetail> {
    return this.http.post<WorkOrderDetail>(`${this.base}/${id}/evidence`, payload);
  }

  /**
   * Statuswechsel durchführen. Recht workflow.AENDERN — außer Wechsel nach
   * FREIGEGEBEN, das der Server als Freigabetor mit workflow.FREIGEBEN prüft.
   */
  advanceStatus(id: string, payload: WorkOrderStatusInput): Observable<WorkOrderDetail> {
    return this.http.post<WorkOrderDetail>(`${this.base}/${id}/status`, payload);
  }
}
