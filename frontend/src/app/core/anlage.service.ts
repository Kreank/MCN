import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { Anlage, AnlageDetail, AnlageIn, AnlagePatch, AnlageStatus } from './anlage.model';

/**
 * Typisierter Zugriff auf die technischen Anlagen (dev-Proxy: /api -> :8000).
 *
 * Rechtemodul `property` (die Anlage ist Objektstammdatum). Unter `row_scope
 * EIGENE` sieht und pflegt der Monteur die Anlagen **seiner** Objekte; ein
 * fremdes Objekt antwortet mit 404 — das entscheidet der Server, nicht dieser
 * Service.
 *
 * **Es gibt kein Löschen.** Eine Anlage wird stillgelegt (`status = 'INAKTIV'`):
 * Aufträge, Prüfungen und Berichte zeigen auf sie.
 */
@Injectable({ providedIn: 'root' })
export class AnlageService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/property';

  /**
   * Anlagen einer Liegenschaft — **standardmäßig nur die aktiven**. Stillgelegte
   * liefert der Server nur auf Nachfrage (kein Client-Filter: was sichtbar ist,
   * entscheidet er).
   */
  list(propertyId: string, mitInaktiven = false): Observable<Anlage[]> {
    const options = mitInaktiven
      ? { params: new HttpParams().set('mit_inaktiven', 'true') }
      : undefined;
    return this.http.get<Anlage[]>(`${this.base}/properties/${propertyId}/assets`, options);
  }

  /** Anlagen-Detail inkl. Wartung, Prüfungen, Aufträgen und Fälligkeiten. */
  get(assetId: string): Observable<AnlageDetail> {
    return this.http.get<AnlageDetail>(`${this.base}/assets/${assetId}`);
  }

  /** Anlage erfassen (Recht property.ANLEGEN). */
  create(propertyId: string, payload: AnlageIn): Observable<AnlageDetail> {
    return this.http.post<AnlageDetail>(
      `${this.base}/properties/${propertyId}/assets`,
      payload,
    );
  }

  /** Anlagenfelder ändern — nur gesetzte Felder (Recht property.AENDERN). */
  update(assetId: string, payload: AnlagePatch): Observable<AnlageDetail> {
    return this.http.patch<AnlageDetail>(`${this.base}/assets/${assetId}`, payload);
  }

  /**
   * Anlage stilllegen bzw. reaktivieren (Recht property.AENDERN).
   *
   * **Gelöscht wird nie** — eine ausgebaute Therme bleibt der Beleg dafür, was in
   * den Aufträgen von damals verbaut wurde (GoBD/Audit).
   */
  setStatus(assetId: string, status: AnlageStatus): Observable<AnlageDetail> {
    return this.update(assetId, { status });
  }
}
