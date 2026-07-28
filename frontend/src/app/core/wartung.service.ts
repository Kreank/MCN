import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  ContractAssetsInput,
  ContractCreate,
  ContractDetail,
  ContractPage,
  ContractQuery,
  ContractStatusInput,
  ContractTriggerInput,
  MaintenanceContract,
} from './wartung.model';

/** Typisierter Zugriff auf die Wartungs-API (dev-Proxy: /api -> :8000). */
@Injectable({ providedIn: 'root' })
export class WartungService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/maintenance/contracts';

  list(query: ContractQuery): Observable<ContractPage> {
    let params = new HttpParams()
      .set('page', query.page)
      .set('page_size', query.page_size);
    const q = query.q?.trim();
    if (q) params = params.set('q', q);
    if (query.status) params = params.set('status', query.status);
    if (query.property_id) params = params.set('property_id', query.property_id);
    if (query.due) params = params.set('due', true);
    return this.http.get<ContractPage>(this.base, { params });
  }

  get(id: string): Observable<ContractDetail> {
    return this.http.get<ContractDetail>(`${this.base}/${id}`);
  }

  // --- Schreiben (Session-Auth Pflicht) ------------------------------------
  /** Neuen Wartungsvertrag anlegen (Status AKTIV; Recht workflow.ANLEGEN). */
  create(payload: ContractCreate): Observable<MaintenanceContract> {
    return this.http.post<MaintenanceContract>(this.base, payload);
  }

  /** Vertragsstatus wechseln (Recht workflow.AENDERN). */
  setStatus(id: string, payload: ContractStatusInput): Observable<MaintenanceContract> {
    return this.http.post<MaintenanceContract>(`${this.base}/${id}/status`, payload);
  }

  /**
   * Setzt die abgedeckten Anlagen **vollständig** (Recht maintenance.AENDERN).
   * Eine leere Liste ist gültig und heißt „gilt fürs ganze Objekt".
   */
  setAssets(id: string, payload: ContractAssetsInput): Observable<MaintenanceContract> {
    return this.http.put<MaintenanceContract>(`${this.base}/${id}/assets`, payload);
  }

  /** Fälligkeits-Aktion auslösen (Recht workflow.AENDERN; erzeugt Folgeobjekte). */
  trigger(id: string, payload: ContractTriggerInput): Observable<MaintenanceContract> {
    return this.http.post<MaintenanceContract>(`${this.base}/${id}/trigger`, payload);
  }
}
