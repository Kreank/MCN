import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  Belegung,
  BelegungIn,
  BelegungPatch,
  EinheitBelegung,
  MieterAddIn,
} from './belegung.model';

/**
 * Belegung einer Liegenschaft (dev-Proxy: /api -> :8000).
 *
 * Rechtemodul **`tenure`** — nicht `property`. Wer Räume und Anlagen pflegen
 * darf, darf damit noch lange keine **Mietverhältnisse** ändern; die Matrix
 * trennt das seit 0026, und seit Migration 0103 benutzt es jemand.
 *
 * Unter `row_scope EIGENE` sieht der Monteur die Mieter **seiner** Objekte (mit
 * Telefonnummer — dafür gibt es den Slice) und **ändert nichts**. Ein fremdes
 * Objekt antwortet mit 404. Das entscheidet der Server, nicht dieser Service.
 *
 * **Es gibt kein Löschen.** Eine Belegung wird beendet, ein Mieter zieht aus
 * (`valid_until`) — der Baustellenbericht von damals zeigt auf die Wohnung, in
 * der damals Musili wohnte.
 */
@Injectable({ providedIn: 'root' })
export class BelegungService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/tenure';

  /**
   * Die Belegung je Einheit. `historie = true` liefert zusätzlich die beendeten
   * Belegungen („wer wohnte hier, als der Schaden entstand?").
   */
  list(propertyId: string, historie = false): Observable<EinheitBelegung[]> {
    const options = historie
      ? { params: new HttpParams().set('historie', 'true') }
      : undefined;
    return this.http.get<EinheitBelegung[]>(
      `${this.base}/properties/${propertyId}/belegung`,
      options,
    );
  }

  /** Belegung erfassen — ohne `mieter` ist es Leerstand (Recht tenure.ANLEGEN). */
  create(propertyId: string, payload: BelegungIn): Observable<Belegung> {
    return this.http.post<Belegung>(
      `${this.base}/properties/${propertyId}/belegung`,
      payload,
    );
  }

  /**
   * Belegung ändern — **und beenden** (`valid_until`). Beim Beenden ziehen die
   * offenen Mietverhältnisse **mit** (der Server erledigt das in derselben
   * Transaktion; ein offener Mieter passt sonst nicht mehr in eine geschlossene
   * Belegung).
   */
  update(occupancyId: string, payload: BelegungPatch): Observable<Belegung> {
    return this.http.patch<Belegung>(`${this.base}/belegung/${occupancyId}`, payload);
  }

  /** Einen weiteren Mieter/Nutzer setzen (Ehepaar, Mitbewohner — der Normalfall). */
  addMieter(occupancyId: string, payload: MieterAddIn): Observable<Belegung> {
    return this.http.post<Belegung>(
      `${this.base}/belegung/${occupancyId}/mieter`,
      payload,
    );
  }

  /** Ein Mieter zieht aus (`valid_until`). Kein Löschen — die Historie bleibt. */
  endMieter(occupancyPartyId: string, validUntil: string): Observable<Belegung> {
    return this.http.post<Belegung>(`${this.base}/mieter/${occupancyPartyId}/beenden`, {
      valid_until: validUntil,
    });
  }
}
