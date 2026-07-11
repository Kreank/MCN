import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { WageGroup, WageGroupInput, WageGroupPatch } from './lohngruppe.model';

/**
 * Lohn-/Maschinengruppen (Modul `pricing`, `/pricing/wage-groups`). Lesen mit
 * pricing/LESEN, Anlegen/Ändern mit pricing/ANLEGEN bzw. AENDERN — der Server
 * setzt das durch, das UI blendet nur aus. Kein Löschen: Deaktivieren über den
 * Status.
 */
@Injectable({ providedIn: 'root' })
export class LohngruppeService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/pricing/wage-groups';

  list(includeInactive = true): Observable<WageGroup[]> {
    const params = new HttpParams().set('include_inactive', includeInactive);
    return this.http.get<WageGroup[]>(this.base, { params });
  }

  create(payload: WageGroupInput): Observable<WageGroup> {
    return this.http.post<WageGroup>(this.base, payload);
  }

  update(id: string, payload: WageGroupPatch): Observable<WageGroup> {
    return this.http.put<WageGroup>(`${this.base}/${id}`, payload);
  }
}
