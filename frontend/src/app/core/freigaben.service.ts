import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { Approval, ApprovalStatus } from './freigaben.model';

/**
 * Typisierter Zugriff auf die Vier-Augen-Freigaben (dev-Proxy: /api -> :8000).
 * Die Entscheid-/Zurückzieh-Endpunkte sind session-auth-pflichtig; der
 * auth.interceptor hängt withCredentials + X-CSRFToken an.
 */
@Injectable({ providedIn: 'root' })
export class FreigabenService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/security';

  /** Freigabeanträge, optional nach Status gefiltert. */
  list(status?: ApprovalStatus | null): Observable<Approval[]> {
    let params = new HttpParams();
    if (status) params = params.set('status', status);
    return this.http.get<Approval[]>(`${this.base}/approvals`, { params });
  }

  /** Genehmigt einen Antrag (Recht security/FREIGEBEN; 422 bei Selbstgenehmigung). */
  approve(id: string): Observable<Approval> {
    return this.http.post<Approval>(`${this.base}/approvals/${id}/approve`, {});
  }

  /** Lehnt einen Antrag ab — Begründung ist Pflicht (422 ohne). */
  reject(id: string, note: string): Observable<Approval> {
    return this.http.post<Approval>(`${this.base}/approvals/${id}/reject`, { note });
  }

  /** Zieht den EIGENEN Antrag zurück (Recht security/ANLEGEN). */
  withdraw(id: string): Observable<Approval> {
    return this.http.post<Approval>(`${this.base}/approvals/${id}/withdraw`, {});
  }
}
