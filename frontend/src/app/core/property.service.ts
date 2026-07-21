import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  AdressDubletten,
  AdressDublettenQuery,
  Building,
  BuildingIn,
  BuildingPatch,
  PartyRole,
  PartyRoleIn,
  PropertyDetail,
  PropertyIn,
  PropertyPage,
  PropertyQuery,
  Unit,
  UnitIn,
  UnitPatch,
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

  /**
   * Liegenschaften suchen, die zu einer eingetippten Adresse passen könnten —
   * Grundlage der Dublettenwarnung bei der Erfassung. Reine Leseabfrage; das
   * Ergebnis ist ein HINWEIS, kein Blocker (Anlegen bleibt immer erlaubt).
   */
  adressDubletten(query: AdressDublettenQuery): Observable<AdressDubletten> {
    let params = new HttpParams().set('street', query.street.trim());
    const haus = query.house_number?.trim();
    if (haus) params = params.set('house_number', haus);
    const plz = query.postal_code?.trim();
    if (plz) params = params.set('postal_code', plz);
    const ort = query.city?.trim();
    if (ort) params = params.set('city', ort);
    if (query.limit) params = params.set('limit', query.limit);
    return this.http.get<AdressDubletten>(`${this.base}/adress-dubletten`, { params });
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

  /**
   * Gebäude korrigieren — Bezeichnung und Nummer (Befund I7).
   *
   * Die Anschrift ist bewusst nicht dabei; sie braucht eine Objektgrenze, die
   * noch nicht entschieden ist (siehe `BuildingPatch` in property.model.ts).
   *
   * Bis Migration 0124 gab es auf der Tabelle keinen Schreibpfad außer INSERT:
   * ein ohne Bezeichnung angelegtes Gebäude blieb dauerhaft „Gebäude 1".
   * Erfordert Recht property.AENDERN.
   */
  patchBuilding(buildingId: string, payload: BuildingPatch): Observable<Building> {
    return this.http.patch<Building>(`/api/property/buildings/${buildingId}`, payload);
  }

  /** Einheit korrigieren — Nummer, Typ, Geschoss. Erfordert Recht property.AENDERN. */
  patchUnit(unitId: string, payload: UnitPatch): Observable<Unit> {
    return this.http.patch<Unit>(`/api/property/units/${unitId}`, payload);
  }

  /** Party-Rolle an einer Liegenschaft zuordnen. Erfordert Recht property.AENDERN. */
  addPartyRole(propertyId: string, payload: PartyRoleIn): Observable<PartyRole> {
    return this.http.post<PartyRole>(`${this.base}/${propertyId}/parties`, payload);
  }
}
