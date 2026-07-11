import { HttpClient, HttpParams, HttpResponse } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';
import {
  ArtikelDashboard,
  AuswertungQuery,
  Dashboard,
  Kunden,
  MitarbeitendeDashboard,
  ProjekteDashboard,
  UmsatzProjekt,
} from './auswertungen.model';

/** Blob + Dateiname eines CSV-Exports (Name aus Content-Disposition). */
export interface CsvExport {
  blob: Blob;
  filename: string;
}

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

  private zeitraumParams(query: AuswertungQuery): HttpParams {
    let params = new HttpParams();
    if (query.date_from) params = params.set('date_from', query.date_from);
    if (query.date_to) params = params.set('date_to', query.date_to);
    return params;
  }

  /**
   * Laedt den CSV-Export eines Dashboards als Blob (durch die Anwendung, nicht
   * per Direkt-URL): nur so gehen Session-Cookie und CSRF durch den Interceptor
   * und die Rechtepruefung des Servers greift. Der Dateiname kommt aus dem
   * `Content-Disposition`-Header, mit `fallbackName` als Rueckfall.
   */
  private exportCsv(
    pfad: string,
    params: HttpParams,
    fallbackName: string,
  ): Observable<CsvExport> {
    return this.http
      .get(`${this.base}/${pfad}/export.csv`, {
        params,
        responseType: 'blob',
        observe: 'response',
      })
      .pipe(
        map((antwort: HttpResponse<Blob>) => ({
          blob: antwort.body ?? new Blob(),
          filename: dateinameAusAntwort(antwort) ?? fallbackName,
        })),
      );
  }

  umsatzExport(query: AuswertungQuery = {}): Observable<CsvExport> {
    return this.exportCsv(
      'umsatz-projektuebersicht',
      this.zeitraumParams(query),
      'Umsatz-Projektuebersicht.csv',
    );
  }

  kundenExport(query: AuswertungQuery = {}): Observable<CsvExport> {
    return this.exportCsv('kunden', this.zeitraumParams(query), 'Kunden.csv');
  }

  projekteExport(query: AuswertungQuery = {}): Observable<CsvExport> {
    return this.exportCsv('projekte', this.zeitraumParams(query), 'Projekte.csv');
  }

  artikelExport(query: AuswertungQuery = {}): Observable<CsvExport> {
    return this.exportCsv('artikel', this.zeitraumParams(query), 'Artikel-Leistungen.csv');
  }

  mitarbeitendeExport(year?: number): Observable<CsvExport> {
    let params = new HttpParams();
    if (year) params = params.set('year', year);
    return this.exportCsv('mitarbeitende', params, `Mitarbeitende_${year ?? ''}.csv`);
  }
}

/**
 * Loest einen Datei-Download aus einem Blob aus: Object-URL + unsichtbarer
 * `<a download>`. Die URL wird erst NACH dem aktuellen Task freigegeben (ein
 * synchrones revoke bricht den Download in manchen Browsern ab). Bewusst KEIN
 * `window.open`. Spiegel des Musters aus shared/dateien.
 */
export function csvDownloadAusloesen(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.rel = 'noopener';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  } catch (e) {
    URL.revokeObjectURL(url);
    throw e;
  }
}

/**
 * Liest den Dateinamen aus `Content-Disposition: attachment; filename="…"`
 * (sowohl `filename*=` (RFC 5987) als auch schlichtes `filename="…"`). `null`,
 * wenn kein Name enthalten ist.
 */
function dateinameAusAntwort(antwort: HttpResponse<Blob>): string | null {
  const header = antwort.headers.get('Content-Disposition');
  if (!header) return null;
  const stern = /filename\*=(?:UTF-8'')?([^;]+)/i.exec(header);
  if (stern?.[1]) {
    try {
      return decodeURIComponent(stern[1].trim().replace(/^"|"$/g, ''));
    } catch {
      /* faellt unten auf das schlichte filename zurueck */
    }
  }
  const schlicht = /filename="?([^";]+)"?/i.exec(header);
  return schlicht?.[1]?.trim() ?? null;
}
