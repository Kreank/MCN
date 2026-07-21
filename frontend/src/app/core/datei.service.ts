import {
  HttpClient,
  HttpEvent,
  HttpParams,
  HttpResponse,
} from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';
import {
  Datei,
  Dateikategorie,
  DateiInhalt,
  DateiListe,
  ZielFilter,
} from './datei.model';

/**
 * Typisierter Zugriff auf die Datei-Ablage (/api/content, dev-Proxy: /api -> :8000).
 *
 * Der Download laeuft bewusst NICHT ueber `window.open`, sondern per HttpClient
 * mit `responseType:'blob'`: nur so gehen Session-Cookie und CSRF durch den
 * Interceptor, und die Rechtepruefung des Servers greift. Der Blob wird zu einer
 * Object-URL, ein unsichtbarer `<a download>` startet den Download, danach wird
 * die URL sofort wieder freigegeben.
 */
@Injectable({ providedIn: 'root' })
export class DateiService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/content';

  /** Das (einzige) gesetzte Zielfeld als flache Parameter — so erwartet es der Server. */
  private zielParams(ziel: ZielFilter): HttpParams {
    let params = new HttpParams();
    for (const [key, wert] of Object.entries(ziel)) {
      if (wert) params = params.set(key, wert);
    }
    return params;
  }

  /**
   * Die gepflegte Kategorienliste (Migration 0127).
   *
   * `ohne_system` für Auswahlfelder: ARTIKELBILD, ATTEST, BELEG_PDF und
   * E_RECHNUNG vergibt ausschließlich der Server.
   */
  kategorien(opt?: { nurAktive?: boolean; ohneSystem?: boolean }): Observable<Dateikategorie[]> {
    let params = new HttpParams();
    if (opt?.nurAktive === false) params = params.set('nur_aktive', 'false');
    if (opt?.ohneSystem) params = params.set('ohne_system', 'true');
    return this.http.get<Dateikategorie[]>(`${this.base}/file-categories`, { params });
  }

  kategorieAnlegen(payload: {
    label: string;
    code?: string;
    sort_order?: number;
  }): Observable<Dateikategorie> {
    return this.http.post<Dateikategorie>(`${this.base}/file-categories`, payload);
  }

  /** Nur Bezeichnung und Reihenfolge — der Code bleibt (siehe Service). */
  kategorieAendern(
    id: string,
    payload: { label?: string; sort_order?: number },
  ): Observable<Dateikategorie> {
    return this.http.patch<Dateikategorie>(`${this.base}/file-categories/${id}`, payload);
  }

  /** Deaktivieren statt löschen — alte Dateien tragen ihre Kategorie noch. */
  kategorieDeaktivieren(id: string): Observable<Dateikategorie> {
    return this.http.post<Dateikategorie>(
      `${this.base}/file-categories/${id}/deaktivieren`,
      {},
    );
  }

  kategorieAktivieren(id: string): Observable<Dateikategorie> {
    return this.http.post<Dateikategorie>(
      `${this.base}/file-categories/${id}/aktivieren`,
      {},
    );
  }

  /** Alle Dateien an einem Zielobjekt (neueste zuerst). Recht: content/LESEN. */
  liste(ziel: ZielFilter): Observable<DateiListe> {
    return this.http.get<DateiListe>(`${this.base}/files`, {
      params: this.zielParams(ziel),
    });
  }

  /**
   * Laedt eine Datei hoch (multipart). Recht: content/ANLEGEN. Gibt den
   * Event-Strom zurueck (`reportProgress`), damit der Fortschritt grosser
   * Dateien angezeigt werden kann. Das Zielschema wird als einzelne Formfelder
   * gesendet (nicht als JSON), passend zum django-ninja-`Form`-Schema.
   */
  hochladen(
    ziel: ZielFilter,
    datei: File,
    linkKategorie: string,
  ): Observable<HttpEvent<Datei>> {
    const form = new FormData();
    form.append('datei', datei, datei.name);
    for (const [key, wert] of Object.entries(ziel)) {
      if (wert) form.append(key, wert);
    }
    form.append('link_category', linkKategorie);
    return this.http.post<Datei>(`${this.base}/files`, form, {
      reportProgress: true,
      observe: 'events',
    });
  }

  /**
   * Holt den Dateiinhalt als Blob (durch die Anwendung, nicht per Direkt-URL).
   * Der Dateiname wird aus dem `Content-Disposition`-Header der Antwort gelesen;
   * `fallbackName` greift, falls der Header fehlt.
   */
  herunterladen(fileId: string, fallbackName: string): Observable<DateiInhalt> {
    return this.http
      .get(`${this.base}/files/${fileId}/download`, {
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

  /** Loest nur die Verknuepfung; die Datei selbst bleibt. Recht: content/AENDERN. */
  verknuepfungLoesen(linkId: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/links/${linkId}`);
  }
}

/**
 * Liest den Dateinamen aus `Content-Disposition: attachment; filename="…"`.
 * Beruecksichtigt sowohl `filename*=` (RFC 5987) als auch das schlichte
 * `filename="…"`. Gibt `null` zurueck, wenn kein Name enthalten ist.
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
