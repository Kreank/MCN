import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { Briefing } from './ki.model';

/** Typisierter Zugriff auf die KI-API (dev-Proxy: /api -> :8000). */
@Injectable({ providedIn: 'root' })
export class KiService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/ai';

  /**
   * Tagesbriefing für die Leitstand-Kachel. Serverseitig gecacht; `refresh=true`
   * (der „Aktualisieren"-Knopf) erzwingt eine Neuberechnung.
   */
  briefing(refresh = false): Observable<Briefing> {
    let params = new HttpParams();
    if (refresh) params = params.set('refresh', '1');
    return this.http.get<Briefing>(`${this.base}/briefing`, { params });
  }
}
