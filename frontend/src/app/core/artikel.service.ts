import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  ArticleDetail,
  ArticleKalkulation,
  ArticlePage,
  AssemblyDetail,
  AssemblyPage,
  StammQuery,
} from './artikel.model';

/** Typisierter Zugriff auf die Artikel-/Leistungs-API (dev-Proxy: /api -> :8000). */
@Injectable({ providedIn: 'root' })
export class ArtikelService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/pricing';

  listArticles(query: StammQuery): Observable<ArticlePage> {
    let params = new HttpParams()
      .set('page', query.page)
      .set('page_size', query.page_size);
    const q = query.q?.trim();
    if (q) params = params.set('q', q);
    if (query.line_type) params = params.set('line_type', query.line_type);
    if (query.status) params = params.set('status', query.status);
    return this.http.get<ArticlePage>(`${this.base}/articles`, { params });
  }

  getArticle(id: string): Observable<ArticleDetail> {
    return this.http.get<ArticleDetail>(`${this.base}/articles/${id}`);
  }

  getKalkulation(id: string): Observable<ArticleKalkulation> {
    return this.http.get<ArticleKalkulation>(
      `${this.base}/articles/${id}/kalkulation`,
    );
  }

  listAssemblies(query: StammQuery): Observable<AssemblyPage> {
    let params = new HttpParams()
      .set('page', query.page)
      .set('page_size', query.page_size);
    const q = query.q?.trim();
    if (q) params = params.set('q', q);
    if (query.status) params = params.set('status', query.status);
    return this.http.get<AssemblyPage>(`${this.base}/assemblies`, { params });
  }

  getAssembly(id: string): Observable<AssemblyDetail> {
    return this.http.get<AssemblyDetail>(`${this.base}/assemblies/${id}`);
  }
}
