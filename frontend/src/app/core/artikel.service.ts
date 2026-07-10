import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  ArticleCopyIn,
  ArticleDetail,
  ArticleIn,
  ArticleKalkulation,
  ArticlePage,
  ArticleSalePrice,
  ArticleSalePriceIn,
  ArticleStatusIn,
  ArticleUpdateIn,
  AssemblyComponentsInput,
  AssemblyDetail,
  AssemblyIn,
  AssemblyPage,
  HistorieEintrag,
  LieferantIn,
  SalePriceGroup,
  StammdatenUebernahmeIn,
  StammQuery,
  StammStatus,
  VerkaufspreiseIn,
  VerkaufspreiseUebersicht,
  WageGroup,
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

  /** Artikel anlegen. Erfordert Recht pricing.ANLEGEN. */
  createArticle(payload: ArticleIn): Observable<ArticleDetail> {
    return this.http.post<ArticleDetail>(`${this.base}/articles`, payload);
  }

  /** Leistung/Baugruppe anlegen. Erfordert Recht pricing.ANLEGEN. */
  createAssembly(payload: AssemblyIn): Observable<AssemblyDetail> {
    return this.http.post<AssemblyDetail>(`${this.base}/assemblies`, payload);
  }

  /** Artikel unter neuer Nummer duplizieren (Hero „Kopieren"). Kopiert
   *  Stammdaten, VK-Gruppen und Lieferantenbezug — GTIN bewusst nicht. Recht
   *  pricing.ANLEGEN. Leere/vergebene Nummer → 422. */
  copyArticle(id: string, payload: ArticleCopyIn): Observable<ArticleDetail> {
    return this.http.post<ArticleDetail>(`${this.base}/articles/${id}/copy`, payload);
  }

  /** Artikelstammdaten ändern. Erfordert Recht pricing.AENDERN. Nur gesetzte
   *  Felder wirken (Server: exclude_unset). */
  updateArticle(id: string, payload: ArticleUpdateIn): Observable<ArticleDetail> {
    return this.http.put<ArticleDetail>(`${this.base}/articles/${id}`, payload);
  }

  /** Artikel aktivieren/deaktivieren (kein Löschen). Recht pricing.AENDERN. */
  setArticleStatus(id: string, status: StammStatus): Observable<ArticleDetail> {
    const payload: ArticleStatusIn = { status };
    return this.http.post<ArticleDetail>(`${this.base}/articles/${id}/status`, payload);
  }

  /** Änderungsverlauf eines Artikels (neueste zuerst). Recht pricing.LESEN. */
  articleHistorie(id: string, limit = 50): Observable<HistorieEintrag[]> {
    const params = new HttpParams().set('limit', limit);
    return this.http.get<HistorieEintrag[]>(
      `${this.base}/articles/${id}/historie`,
      { params },
    );
  }

  /** Positionswerte in den Artikelstamm übernehmen — eigener, ausdrücklicher
   *  Vorgang (nicht Teil des Beleg-Speicherns). Recht pricing.AENDERN. Der
   *  Einkaufspreis wird bewusst nicht übernommen. */
  stammdatenUebernehmen(
    id: string,
    payload: StammdatenUebernahmeIn,
  ): Observable<ArticleDetail> {
    return this.http.post<ArticleDetail>(
      `${this.base}/articles/${id}/stammdaten-uebernehmen`,
      payload,
    );
  }

  /** VK-Variante eines Artikels setzen. Erfordert Recht pricing.AENDERN. */
  setSalePrice(
    articleId: string,
    payload: ArticleSalePriceIn,
  ): Observable<ArticleSalePrice> {
    return this.http.put<ArticleSalePrice>(
      `${this.base}/articles/${articleId}/sale_price`,
      payload,
    );
  }

  /** Alle aktiven VK-Gruppen mit errechnetem/überschriebenem VK je Stück
   *  (Hero-Reiter „Kalkulation", rechte Tabelle). Recht pricing/LESEN. */
  getVerkaufspreise(id: string): Observable<VerkaufspreiseUebersicht> {
    return this.http.get<VerkaufspreiseUebersicht>(
      `${this.base}/articles/${id}/verkaufspreise`,
    );
  }

  /** Setzt die GANZE VK-Gruppen-Tabelle (genau eine Standard-Gruppe). Recht
   *  pricing/AENDERN. `fixed_price=null` je Eintrag ⇒ Formelwert gilt. */
  setVerkaufspreise(
    id: string,
    payload: VerkaufspreiseIn,
  ): Observable<VerkaufspreiseUebersicht> {
    return this.http.put<VerkaufspreiseUebersicht>(
      `${this.base}/articles/${id}/verkaufspreise`,
      payload,
    );
  }

  /** Primären Lieferantenbezug setzen (Lieferant, Lieferanten-Nr., EK). Recht
   *  pricing/AENDERN. */
  setLieferant(id: string, payload: LieferantIn): Observable<ArticleDetail> {
    return this.http.put<ArticleDetail>(
      `${this.base}/articles/${id}/lieferant`,
      payload,
    );
  }

  /** Lohn-/Maschinengruppen als Auswahlliste (Standard: nur AKTIV). */
  listWageGroups(status?: string | null): Observable<WageGroup[]> {
    let params = new HttpParams();
    if (status) params = params.set('status', status);
    return this.http.get<WageGroup[]>(`${this.base}/wage_groups`, { params });
  }

  /** VK-Kalkulationsgruppen als Auswahlliste (Standard: nur AKTIV). */
  listSalePriceGroups(status?: string | null): Observable<SalePriceGroup[]> {
    let params = new HttpParams();
    if (status) params = params.set('status', status);
    return this.http.get<SalePriceGroup[]>(`${this.base}/sale_price_groups`, { params });
  }

  /** Stückliste einer Leistung erweitern. Erfordert Recht pricing.AENDERN. */
  addAssemblyComponents(
    assemblyId: string,
    payload: AssemblyComponentsInput,
  ): Observable<AssemblyDetail> {
    return this.http.post<AssemblyDetail>(
      `${this.base}/assemblies/${assemblyId}/components`,
      payload,
    );
  }
}
