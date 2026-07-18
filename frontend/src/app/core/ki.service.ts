import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  Briefing,
  KiVorschlag,
  KiVorschlagDetail,
  ProposalStatus,
  VorschlagAnnahme,
} from './ki.model';

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

  // --- KI-Vorschläge (ai_proposal) ----------------------------------------

  /** Vorschläge eines Status (Default: die offenen — sie warten auf eine Entscheidung). */
  vorschlaege(status: ProposalStatus = 'PENDING'): Observable<KiVorschlag[]> {
    const params = new HttpParams().set('status', status);
    return this.http.get<KiVorschlag[]>(`${this.base}/proposals`, { params });
  }

  /** Ein Vorschlag samt vollem Entwurf. */
  vorschlag(id: string): Observable<KiVorschlagDetail> {
    return this.http.get<KiVorschlagDetail>(`${this.base}/proposals/${id}`);
  }

  /** Nimmt einen Vorschlag an — materialisiert ihn über die Fach-API (→ echter Bericht). */
  annehmen(id: string): Observable<VorschlagAnnahme> {
    return this.http.post<VorschlagAnnahme>(`${this.base}/proposals/${id}/approve`, {});
  }

  /** Lehnt einen Vorschlag ab — Begründung ist Pflicht (422 ohne). */
  ablehnen(id: string, reason: string): Observable<KiVorschlag> {
    return this.http.post<KiVorschlag>(`${this.base}/proposals/${id}/reject`, { reason });
  }

  /** Löscht einen abgelehnten/abgelaufenen Vorschlag (DSGVO Art. 17). */
  loeschen(id: string): Observable<{ detail: string }> {
    return this.http.delete<{ detail: string }>(`${this.base}/proposals/${id}`);
  }
}
