import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  SiteReport,
  SiteReportCreate,
  SiteReportListe,
  SiteReportSign,
  SiteReportUpdate,
} from './site-report.model';

/** Typisierter Zugriff auf die Baustellenbericht-API (dev-Proxy: /api -> :8000). */
@Injectable({ providedIn: 'root' })
export class SiteReportService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/workflow/site_reports';

  /** Berichte eines Auftrags (neueste zuerst; Recht workflow.LESEN). */
  list(workOrderId: string): Observable<SiteReportListe> {
    const params = new HttpParams().set('work_order_id', workOrderId);
    return this.http.get<SiteReportListe>(this.base, { params });
  }

  get(id: string): Observable<SiteReport> {
    return this.http.get<SiteReport>(`${this.base}/${id}`);
  }

  /** Neuen Bericht anlegen (Status ENTWURF; Recht workflow.ANLEGEN). */
  create(payload: SiteReportCreate): Observable<SiteReport> {
    return this.http.post<SiteReport>(this.base, payload);
  }

  /** Bericht ändern — nur im ENTWURF (Recht workflow.AENDERN). */
  update(id: string, payload: SiteReportUpdate): Observable<SiteReport> {
    return this.http.put<SiteReport>(`${this.base}/${id}`, payload);
  }

  /** Bericht mit der Kundenunterschrift besiegeln (Recht workflow.AENDERN). */
  sign(id: string, payload: SiteReportSign): Observable<SiteReport> {
    return this.http.post<SiteReport>(`${this.base}/${id}/sign`, payload);
  }
}
