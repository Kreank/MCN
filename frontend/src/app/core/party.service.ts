import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  AddressIn,
  ContactPerson,
  ContactPersonIn,
  ContactPoint,
  ContactPointIn,
  OrganizationIn,
  PartyAddress,
  PartyDetail,
  PartyPage,
  PartyQuery,
  PersonIn,
} from './party.model';

/** Typisierter Zugriff auf die Kontakte-API (dev-Proxy: /api -> :8000). */
@Injectable({ providedIn: 'root' })
export class PartyService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/identity/parties';

  list(query: PartyQuery): Observable<PartyPage> {
    let params = new HttpParams()
      .set('page', query.page)
      .set('page_size', query.page_size);
    const q = query.q?.trim();
    if (q) {
      params = params.set('q', q);
    }
    if (query.party_type) {
      params = params.set('party_type', query.party_type);
    }
    return this.http.get<PartyPage>(this.base, { params });
  }

  get(id: string): Observable<PartyDetail> {
    return this.http.get<PartyDetail>(`${this.base}/${id}`);
  }

  /** Neue Person anlegen. Erfordert Recht identity.ANLEGEN. */
  createPerson(payload: PersonIn): Observable<PartyDetail> {
    return this.http.post<PartyDetail>(`${this.base}/person`, payload);
  }

  /** Neue Organisation anlegen. Erfordert Recht identity.ANLEGEN. */
  createOrganization(payload: OrganizationIn): Observable<PartyDetail> {
    return this.http.post<PartyDetail>(`${this.base}/organization`, payload);
  }

  /** Akquisekanal des Kontakts setzen/ändern (`sourceId=null` löst ihn). Recht
   * identity.AENDERN. */
  setAcquisitionSource(partyId: string, sourceId: string | null): Observable<PartyDetail> {
    return this.http.put<PartyDetail>(
      `${this.base}/${partyId}/acquisition-source`,
      { source_id: sourceId },
    );
  }

  // --- Kommunikationswege --------------------------------------------------
  listContactPoints(partyId: string): Observable<ContactPoint[]> {
    return this.http.get<ContactPoint[]>(`${this.base}/${partyId}/contact-points`);
  }

  createContactPoint(partyId: string, payload: ContactPointIn): Observable<ContactPoint> {
    return this.http.post<ContactPoint>(`${this.base}/${partyId}/contact-points`, payload);
  }

  deactivateContactPoint(partyId: string, contactPointId: string): Observable<ContactPoint> {
    return this.http.post<ContactPoint>(
      `${this.base}/${partyId}/contact-points/${contactPointId}/deactivate`,
      {},
    );
  }

  // --- Adressen ------------------------------------------------------------
  listAddresses(partyId: string): Observable<PartyAddress[]> {
    return this.http.get<PartyAddress[]>(`${this.base}/${partyId}/addresses`);
  }

  createAddress(partyId: string, payload: AddressIn): Observable<PartyAddress> {
    return this.http.post<PartyAddress>(`${this.base}/${partyId}/addresses`, payload);
  }

  // --- Ansprechpartner -----------------------------------------------------
  listContactPersons(partyId: string): Observable<ContactPerson[]> {
    return this.http.get<ContactPerson[]>(`${this.base}/${partyId}/contact-persons`);
  }

  createContactPerson(partyId: string, payload: ContactPersonIn): Observable<ContactPerson> {
    return this.http.post<ContactPerson>(`${this.base}/${partyId}/contact-persons`, payload);
  }

  removeContactPerson(partyId: string, relationshipId: string): Observable<ContactPerson> {
    return this.http.post<ContactPerson>(
      `${this.base}/${partyId}/contact-persons/${relationshipId}/remove`,
      {},
    );
  }
}
