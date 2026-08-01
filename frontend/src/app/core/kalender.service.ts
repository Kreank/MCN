import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

/**
 * ICS-Export (iCalendar, RFC 5545) — Termine für Outlook/Google/Apple.
 *
 * Der Download laeuft als **Blob durch den HttpClient**, nie als `window.open`
 * oder Direktlink: Die API ist anmeldepflichtig, und ein neues Fenster traegt
 * weder den CSRF-Header noch verlaesslich das Session-Cookie (Hausmuster, siehe
 * `core/datei.service.ts`). Der Blob wird ueber `shared/datei-download.ts`
 * lokal ausgeloest.
 *
 * Bei 422 (kein Termin, Zeitraum zu gross) ist der Fehlerkoerper wegen
 * `responseType:'blob'` ebenfalls ein Blob — der Aufrufer liest ihn als Text
 * (`icsFehlertext`).
 */
@Injectable({ providedIn: 'root' })
export class KalenderService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/kalender';

  /** Ein einzelner Termin als .ics. */
  einsatz(jobId: string): Observable<Blob> {
    return this.http.get(`${this.base}/einsatz/${jobId}.ics`, {
      responseType: 'blob',
    });
  }

  /**
   * Alle Termine eines Zeitraums als .ics. `von`/`bis` sind ISO-Kalendertage
   * (einschliesslich) und werden serverseitig in Betriebszeit ausgewertet.
   * Wer nur eigene Zeilen sehen darf, bekommt ohnehin nur seine — `assigneeId`
   * ist dann wirkungslos.
   */
  zeitraum(von: string, bis: string, assigneeId?: string): Observable<Blob> {
    let params = new HttpParams().set('von', von).set('bis', bis);
    if (assigneeId) params = params.set('assignee_id', assigneeId);
    return this.http.get(`${this.base}/einsaetze.ics`, {
      params,
      responseType: 'blob',
    });
  }
}

/**
 * Servermeldung aus einem Blob-Fehlerkoerper ziehen (422/404 bei
 * `responseType:'blob'`). Gibt `null` zurueck, wenn nichts Lesbares drinsteht.
 */
export async function icsFehlertext(err: unknown): Promise<string | null> {
  const koerper = (err as { error?: unknown })?.error;
  if (!(koerper instanceof Blob)) return null;
  try {
    const detail = JSON.parse(await koerper.text())?.detail;
    return typeof detail === 'string' ? detail : null;
  } catch {
    return null;
  }
}
