import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  DueItem,
  DueItemErledigen,
  DueItemErledigt,
  DueItemPage,
  DueItemQuery,
  Inspection,
  InspectionCreate,
  InspectionPage,
  InspectionType,
  InspectionTypeCreate,
  PruefStatus,
  Warranty,
  WarrantyPage,
  WarrantyPatch,
} from './faelligkeit.model';

/**
 * Zugriff auf die Fälligkeiten-Engine (/api/maintenance, Migration 0071).
 * Rechtemodul ist `maintenance`; Verwerfen verlangt STORNIEREN.
 */
@Injectable({ providedIn: 'root' })
export class FaelligkeitService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/maintenance';

  // --- Fälligkeiten ---------------------------------------------------------

  list(query: DueItemQuery): Observable<DueItemPage> {
    let params = new HttpParams()
      .set('page', query.page)
      .set('page_size', query.page_size);
    if (query.status) params = params.set('status', query.status);
    if (query.kind) params = params.set('kind', query.kind);
    if (query.property_id) params = params.set('property_id', query.property_id);
    if (query.von) params = params.set('von', query.von);
    if (query.bis) params = params.set('bis', query.bis);
    return this.http.get<DueItemPage>(`${this.base}/due-items`, { params });
  }

  /** Erledigen: erzeugt das Folgeobjekt über den normalen Service des Bereichs. */
  erledigen(id: string, payload: DueItemErledigen): Observable<DueItemErledigt> {
    return this.http.post<DueItemErledigt>(
      `${this.base}/due-items/${id}/erledigen`,
      payload,
    );
  }

  /** Verwerfen: begründungspflichtig, kein Löschen (GoBD). Recht: STORNIEREN. */
  verwerfen(id: string, begruendung: string): Observable<DueItem> {
    return this.http.post<DueItem>(`${this.base}/due-items/${id}/verwerfen`, {
      begruendung,
    });
  }

  // --- Prüfarten ------------------------------------------------------------

  pruefarten(nurAktive = true): Observable<InspectionType[]> {
    const params = new HttpParams().set('nur_aktive', nurAktive);
    return this.http.get<InspectionType[]>(`${this.base}/inspection-types`, { params });
  }

  pruefartAnlegen(payload: InspectionTypeCreate): Observable<InspectionType> {
    return this.http.post<InspectionType>(`${this.base}/inspection-types`, payload);
  }

  pruefartAendern(
    id: string,
    payload: Partial<InspectionTypeCreate> & { is_active?: boolean },
  ): Observable<InspectionType> {
    return this.http.patch<InspectionType>(
      `${this.base}/inspection-types/${id}`,
      payload,
    );
  }

  // --- Prüfungen ------------------------------------------------------------

  pruefungen(
    query: { page: number; page_size: number; q?: string; status?: PruefStatus | null },
  ): Observable<InspectionPage> {
    let params = new HttpParams()
      .set('page', query.page)
      .set('page_size', query.page_size);
    const q = query.q?.trim();
    if (q) params = params.set('q', q);
    if (query.status) params = params.set('status', query.status);
    return this.http.get<InspectionPage>(`${this.base}/inspections`, { params });
  }

  pruefungAnlegen(payload: InspectionCreate): Observable<Inspection> {
    return this.http.post<Inspection>(`${this.base}/inspections`, payload);
  }

  pruefungStatus(id: string, to_status: PruefStatus): Observable<Inspection> {
    return this.http.post<Inspection>(`${this.base}/inspections/${id}/status`, {
      to_status,
    });
  }

  // --- Gewährleistung -------------------------------------------------------

  gewaehrleistungen(
    query: { page: number; page_size: number; work_order_id?: string | null },
  ): Observable<WarrantyPage> {
    let params = new HttpParams()
      .set('page', query.page)
      .set('page_size', query.page_size);
    if (query.work_order_id) params = params.set('work_order_id', query.work_order_id);
    return this.http.get<WarrantyPage>(`${this.base}/warranties`, { params });
  }

  gewaehrleistungAendern(id: string, payload: WarrantyPatch): Observable<Warranty> {
    return this.http.patch<Warranty>(`${this.base}/warranties/${id}`, payload);
  }
}
