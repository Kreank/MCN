import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  SiteReport,
  SiteReportCreate,
  SiteReportDetail,
  SiteReportLineIn,
  SiteReportLines,
  SiteReportListe,
  SiteReportSign,
  SiteReportUpdate,
  SollIst,
  VorbelegbaresAngebot,
} from './site-report.model';

/** Typisierter Zugriff auf die Baustellenbericht-API (dev-Proxy: /api -> :8000). */
@Injectable({ providedIn: 'root' })
export class SiteReportService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/workflow/site_reports';

  /** Berichte eines Auftrags (neueste zuerst; Recht workflow.LESEN).
   * Enthält auch die Berichte der Einsätze dieses Auftrags. Für Rollen mit
   * row_scope EIGENE (Monteur) ist diese Sicht gesperrt (403) — sie nutzen
   * `listAmEinsatz`. */
  list(workOrderId: string): Observable<SiteReportListe> {
    const params = new HttpParams().set('work_order_id', workOrderId);
    return this.http.get<SiteReportListe>(this.base, { params });
  }

  /** Berichte eines Einsatzes — auch beim freien Termin (ohne Auftrag). */
  listAmEinsatz(serviceJobId: string): Observable<SiteReportListe> {
    const params = new HttpParams().set('service_job_id', serviceJobId);
    return this.http.get<SiteReportListe>(this.base, { params });
  }

  /** Ein Bericht im Detail — **mit seinen Positionen** (`lines`). */
  get(id: string): Observable<SiteReportDetail> {
    return this.http.get<SiteReportDetail>(`${this.base}/${id}`);
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

  /**
   * Bericht ohne Unterschrift für fertig erklären (ENTWURF → ABGESCHLOSSEN).
   *
   * Der Normalfall: Rund 80 % der Berichte unterschreibt niemand — abrechenbar
   * sind sie ab hier trotzdem (Migration 0144).
   */
  abschliessen(id: string): Observable<SiteReport> {
    return this.http.post<SiteReport>(`${this.base}/${id}/abschliessen`, {});
  }

  /**
   * Einen Berichts**entwurf** löschen. Ab ABGESCHLOSSEN antwortet der Server
   * mit 422 — dann ist der Bericht Abrechnungsgrundlage und bleibt.
   */
  loeschen(id: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/${id}`);
  }

  // --- Positionen ----------------------------------------------------------

  /**
   * Die Positionen eines Berichts **vollständig ersetzen** (nur im ENTWURF).
   * Der Editor schickt immer den ganzen Satz — ein Teil-Update wäre bei
   * umsortierten Positionsnummern nicht eindeutig. **Ohne Preise.**
   */
  setLines(id: string, lines: SiteReportLineIn[]): Observable<SiteReportLines> {
    return this.http.put<SiteReportLines>(`${this.base}/${id}/positionen`, { lines });
  }

  /** Angebote, aus denen dieser Bericht vorbelegt werden darf (Recht AENDERN). */
  vorbelegbareAngebote(id: string): Observable<VorbelegbaresAngebot[]> {
    return this.http.get<VorbelegbaresAngebot[]>(`${this.base}/${id}/vorbelegen-angebote`);
  }

  /** Angebotspositionen als Soll übernehmen — nur in einen LEEREN Entwurf. */
  vorbelegen(id: string, quoteId: string): Observable<SiteReportLines> {
    return this.http.post<SiteReportLines>(`${this.base}/${id}/vorbelegen`, {
      quote_id: quoteId,
    });
  }

  /**
   * Soll-Ist-Abgleich eines Auftrags (Angebots-Soll gegen Berichts-Ist).
   * Dispositionssicht über die ganze Baustelle: Rollen mit row_scope EIGENE
   * bekommen 403 — der Abschnitt wird dann gar nicht erst angezeigt.
   */
  sollIst(workOrderId: string): Observable<SollIst> {
    return this.http.get<SollIst>(`/api/workflow/work_orders/${workOrderId}/soll-ist`);
  }
}
