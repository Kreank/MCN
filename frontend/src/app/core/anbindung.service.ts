import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  SupplierConnection,
  SupplierConnectionIn,
  SupplierConnectionPatch,
} from './anbindung.model';

/** Typisierter Zugriff auf die Lieferanten-Anbindungs-API (/api/pricing/supplier-connections). */
@Injectable({ providedIn: 'root' })
export class AnbindungService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/pricing/supplier-connections';

  list(includeInactive = true): Observable<SupplierConnection[]> {
    const params = new HttpParams().set('include_inactive', includeInactive);
    return this.http.get<SupplierConnection[]>(this.base, { params });
  }

  create(payload: SupplierConnectionIn): Observable<SupplierConnection> {
    return this.http.post<SupplierConnection>(this.base, payload);
  }

  update(id: string, payload: SupplierConnectionPatch): Observable<SupplierConnection> {
    return this.http.patch<SupplierConnection>(`${this.base}/${id}`, payload);
  }
}
