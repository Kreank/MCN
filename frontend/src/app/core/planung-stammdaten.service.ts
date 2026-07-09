import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  AppointmentCategory,
  CategoryCreate,
  CategoryUpdate,
  Resource,
  ResourceAssignResult,
  ResourceCreate,
  ResourceType,
  ResourceUpdate,
  ServiceJob,
} from './einsatz.model';

/** Zugriff auf die Planungs-Stammdaten (Terminkategorien, Ressourcen) und deren
 * Zuordnung zum Einsatz. Recht: Modul `workflow` (Lesen/Anlegen/Ändern). */
@Injectable({ providedIn: 'root' })
export class PlanungStammdatenService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/planung';

  // --- Terminkategorien ----------------------------------------------------
  listKategorien(includeArchived = false): Observable<AppointmentCategory[]> {
    let params = new HttpParams();
    if (includeArchived) params = params.set('include_archived', 'true');
    return this.http.get<AppointmentCategory[]>(`${this.base}/kategorien`, { params });
  }

  createKategorie(payload: CategoryCreate): Observable<AppointmentCategory> {
    return this.http.post<AppointmentCategory>(`${this.base}/kategorien`, payload);
  }

  updateKategorie(id: string, payload: CategoryUpdate): Observable<AppointmentCategory> {
    return this.http.patch<AppointmentCategory>(`${this.base}/kategorien/${id}`, payload);
  }

  archiveKategorie(id: string): Observable<AppointmentCategory> {
    return this.http.post<AppointmentCategory>(
      `${this.base}/kategorien/${id}/archivieren`,
      {},
    );
  }

  // --- Ressourcen ----------------------------------------------------------
  listRessourcen(opts?: {
    q?: string;
    resource_type?: ResourceType | null;
    includeInactive?: boolean;
  }): Observable<Resource[]> {
    let params = new HttpParams();
    const q = opts?.q?.trim();
    if (q) params = params.set('q', q);
    if (opts?.resource_type) params = params.set('resource_type', opts.resource_type);
    if (opts?.includeInactive) params = params.set('include_inactive', 'true');
    return this.http.get<Resource[]>(`${this.base}/ressourcen`, { params });
  }

  createRessource(payload: ResourceCreate): Observable<Resource> {
    return this.http.post<Resource>(`${this.base}/ressourcen`, payload);
  }

  updateRessource(id: string, payload: ResourceUpdate): Observable<Resource> {
    return this.http.patch<Resource>(`${this.base}/ressourcen/${id}`, payload);
  }

  setRessourceStatus(id: string, toStatus: string): Observable<Resource> {
    return this.http.post<Resource>(`${this.base}/ressourcen/${id}/status`, {
      to_status: toStatus,
    });
  }

  // --- Zuordnung am Einsatz ------------------------------------------------
  setJobKategorie(jobId: string, categoryId: string | null): Observable<ServiceJob> {
    return this.http.post<ServiceJob>(`${this.base}/einsaetze/${jobId}/kategorie`, {
      category_id: categoryId,
    });
  }

  assignRessource(jobId: string, resourceId: string): Observable<ResourceAssignResult> {
    return this.http.post<ResourceAssignResult>(
      `${this.base}/einsaetze/${jobId}/ressourcen`,
      { resource_id: resourceId },
    );
  }

  unassignRessource(jobId: string, resourceId: string): Observable<{ detail: string }> {
    return this.http.delete<{ detail: string }>(
      `${this.base}/einsaetze/${jobId}/ressourcen/${resourceId}`,
    );
  }
}
