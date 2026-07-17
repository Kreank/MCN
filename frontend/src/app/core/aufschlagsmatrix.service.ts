import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  MarkupRule,
  MarkupRuleIn,
  MarkupRuleUpdateIn,
  MarkupTierIn,
  MassenpflegeErgebnis,
  MassenpflegeIn,
  MatrixStatus,
  VkVorschlag,
  Warengruppe,
} from './aufschlagsmatrix.model';

/**
 * Zugriff auf die EK→VK-Aufschlagsmatrix.
 *
 * Gerechnet wird ausschließlich auf dem Server (eine Rechenstelle:
 * `db_core/services/aufschlagsmatrix.py`). Das Frontend rechnet keinen
 * Verkaufspreis selbst — es zeigt, was der Server sagt, und macht nachvollziehbar,
 * welche Regel dahintersteht.
 */
@Injectable({ providedIn: 'root' })
export class AufschlagsmatrixService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/pricing';

  /** Aufschlagsregeln. `articleId` grenzt auf die Regeln EINES Artikels ein
   *  (Scope ARTIKEL) — so holt das Artikel-Detail seine eigene Regel gezielt. */
  listRules(status?: MatrixStatus, articleId?: string): Observable<MarkupRule[]> {
    let params = new HttpParams();
    if (status) params = params.set('status', status);
    if (articleId) params = params.set('article_id', articleId);
    return this.http.get<MarkupRule[]>(`${this.base}/markup-rules`, { params });
  }

  warengruppen(): Observable<Warengruppe[]> {
    return this.http.get<Warengruppe[]>(`${this.base}/markup-rules/warengruppen`);
  }

  createRule(payload: MarkupRuleIn): Observable<MarkupRule> {
    return this.http.post<MarkupRule>(`${this.base}/markup-rules`, payload);
  }

  updateRule(id: string, payload: MarkupRuleUpdateIn): Observable<MarkupRule> {
    return this.http.patch<MarkupRule>(`${this.base}/markup-rules/${id}`, payload);
  }

  setStatus(id: string, status: MatrixStatus): Observable<MarkupRule> {
    return this.http.post<MarkupRule>(`${this.base}/markup-rules/${id}/status`, {
      status,
    });
  }

  /** Setzt die ganze Rabattstaffel; nicht mehr genannte Stufen werden deaktiviert. */
  setTiers(id: string, tiers: MarkupTierIn[]): Observable<MarkupRule> {
    return this.http.put<MarkupRule>(`${this.base}/markup-rules/${id}/tiers`, { tiers });
  }

  /** VK-Vorschlag inkl. Rechenweg. `menge` steuert die Rabattstaffel. */
  vkVorschlag(articleId: string, menge?: string): Observable<VkVorschlag> {
    let params = new HttpParams();
    if (menge) params = params.set('menge', menge);
    return this.http.get<VkVorschlag>(
      `${this.base}/articles/${articleId}/vk-vorschlag`,
      { params },
    );
  }

  /**
   * Massenpflege. `dry_run: true` = Vorschau (schreibt nichts), `false` = anwenden.
   * Beides läuft serverseitig durch denselben Code — die Vorschau kann nicht vom
   * Ergebnis abweichen.
   */
  massenpflege(payload: MassenpflegeIn): Observable<MassenpflegeErgebnis> {
    return this.http.post<MassenpflegeErgebnis>(
      `${this.base}/markup-rules/massenpflege`,
      payload,
    );
  }
}
