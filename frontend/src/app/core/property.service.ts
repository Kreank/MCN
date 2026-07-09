import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  Building,
  BuildingIn,
  PartyRole,
  PartyRoleIn,
  PropertyDetail,
  PropertyIn,
  PropertyPage,
  PropertyQuery,
  Unit,
  UnitIn,
} from './property.model';

/** Typisierter Zugriff auf die Liegenschaften-API (dev-Proxy: /api -> :8000). */
@Injectable({ providedIn: 'root' })
export class PropertyService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/property/properties';

  list(query: PropertyQuery): Observable<PropertyPage> {
    let params = new HttpParams()
      .set('page', query.page)
      .set('page_size', query.page_size);
    const q = query.q?.trim();
    if (q) {
      params = params.set('q', q);
    }
    if (query.property_type) {
      params = params.set('property_type', query.property_type);
    }
    if (query.status) {
      params = params.set('status', query.status);
    }
    return this.http.get<PropertyPage>(this.base, { params });
  }

  get(id: string): Observable<PropertyDetail> {
    return this.http.get<PropertyDetail>(`${this.base}/${id}`);
  }

  /** Neue Liegenschaft anlegen. Erfordert Recht property.ANLEGEN. */
  create(payload: PropertyIn): Observable<PropertyDetail> {
    return this.http.post<PropertyDetail>(this.base, payload);
  }

  /** Gebäude an einer Liegenschaft anlegen. Erfordert Recht property.ANLEGEN. */
  addBuilding(propertyId: string, payload: BuildingIn): Observable<Building> {
    return this.http.post<Building>(`${this.base}/${propertyId}/buildings`, payload);
  }

  /** Einheit in einem Gebäude anlegen. Erfordert Recht property.ANLEGEN. */
  addUnit(buildingId: string, payload: UnitIn): Observable<Unit> {
    return this.http.post<Unit>(`/api/property/buildings/${buildingId}/units`, payload);
  }

  /** Party-Rolle an einer Liegenschaft zuordnen. Erfordert Recht property.AENDERN. */
  addPartyRole(propertyId: string, payload: PartyRoleIn): Observable<PartyRole> {
    return this.http.post<PartyRole>(`${this.base}/${propertyId}/parties`, payload);
  }
}
