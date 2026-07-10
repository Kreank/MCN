import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  ArtikelDashboard,
  AuswertungQuery,
  Dashboard,
  Kunden,
  MitarbeitendeDashboard,
  ProjekteDashboard,
  UmsatzProjekt,
} from './auswertungen.model';

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

  kunden(query: AuswertungQuery = {}): Observable<Kunden> {
    let params = new HttpParams();
    if (query.date_from) params = params.set('date_from', query.date_from);
    if (query.date_to) params = params.set('date_to', query.date_to);
    return this.http.get<Kunden>(`${this.base}/kunden`, { params });
  }

  projekte(query: AuswertungQuery = {}): Observable<ProjekteDashboard> {
    let params = new HttpParams();
    if (query.date_from) params = params.set('date_from', query.date_from);
    if (query.date_to) params = params.set('date_to', query.date_to);
    return this.http.get<ProjekteDashboard>(`${this.base}/projekte`, { params });
  }

  artikel(query: AuswertungQuery = {}): Observable<ArtikelDashboard> {
    let params = new HttpParams();
    if (query.date_from) params = params.set('date_from', query.date_from);
    if (query.date_to) params = params.set('date_to', query.date_to);
    return this.http.get<ArtikelDashboard>(`${this.base}/artikel`, { params });
  }

  /** Mitarbeitenden-Auswertung eines Jahres (Recht hr/LESEN). */
  mitarbeitende(year?: number): Observable<MitarbeitendeDashboard> {
    let params = new HttpParams();
    if (year) params = params.set('year', year);
    return this.http.get<MitarbeitendeDashboard>(`${this.base}/mitarbeitende`, {
      params,
    });
  }
}
