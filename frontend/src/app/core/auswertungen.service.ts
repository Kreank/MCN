import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { AuswertungQuery, Dashboard, UmsatzProjekt } from './auswertungen.model';

/** Typisierter Zugriff auf die Auswertungen-API (dev-Proxy: /api -> :8000). */
@Injectable({ providedIn: 'root' })
export class AuswertungService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/auswertungen';

  listDashboards(): Observable<Dashboard[]> {
    return this.http.get<Dashboard[]>(`${this.base}/dashboards`);
  }

  umsatzProjektuebersicht(query: AuswertungQuery = {}): Observable<UmsatzProjekt> {
    let params = new HttpParams();
    if (query.date_from) params = params.set('date_from', query.date_from);
    if (query.date_to) params = params.set('date_to', query.date_to);
    return this.http.get<UmsatzProjekt>(`${this.base}/umsatz-projektuebersicht`, {
      params,
    });
  }
}
