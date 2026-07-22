import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  EigentuemerIn,
  EigentuemerPatch,
  EigentuemerRef,
  EigentumIn,
  EigentumPatch,
  Eigentumsstand,
  EinheitEigentum,
} from './eigentum.model';

/**
 * Eigentum an den Einheiten einer Liegenschaft (dev-Proxy: /api → :8000).
 *
 * Rechtemodul **`tenure`** — dasselbe wie die Belegung, und aus demselben
 * Grund: Wer Räume und Anlagen pflegen darf, darf damit noch lange keine
 * **Eigentumsverhältnisse** ändern.
 *
 * **Es gibt kein Löschen**, und anders als beim Mieter auch kein „Beteiligten
 * beenden": Eine Beteiligung trägt keinen eigenen Zeitraum, der hängt am Stand.
 * Ein Eigentümerwechsel ist deshalb immer *Stand beenden → neuen Stand
 * anlegen*. Wer sich vertippt hat, korrigiert vorwärts; die alte Aussage bleibt
 * in der Historie sichtbar.
 */
@Injectable({ providedIn: 'root' })
export class EigentumService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/tenure';

  /**
   * Das Eigentum je Einheit. `historie = true` liefert zusätzlich die beendeten
   * Stände — wer wem wann verkauft hat, ist der eigentliche Nachweis.
   */
  list(propertyId: string, historie = false): Observable<EinheitEigentum[]> {
    const options = historie
      ? { params: new HttpParams().set('historie', 'true') }
      : undefined;
    return this.http.get<EinheitEigentum[]>(
      `${this.base}/properties/${propertyId}/eigentum`,
      options,
    );
  }

  /**
   * Die Eigentümer der Liegenschaft, dublettenfrei — die Antwort auf „welche
   * Rechnungsadressen kommen hier in Frage?".
   */
  eigentuemer(propertyId: string): Observable<EigentuemerRef[]> {
    return this.http.get<EigentuemerRef[]>(
      `${this.base}/properties/${propertyId}/eigentuemer`,
    );
  }

  /** Stand samt Beteiligten anlegen — in einer Transaktion. */
  create(propertyId: string, payload: EigentumIn): Observable<Eigentumsstand> {
    return this.http.post<Eigentumsstand>(
      `${this.base}/properties/${propertyId}/eigentum`,
      payload,
    );
  }

  update(periodId: string, payload: EigentumPatch): Observable<Eigentumsstand> {
    return this.http.patch<Eigentumsstand>(`${this.base}/eigentum/${periodId}`, payload);
  }

  /** Stand beenden — der erste Schritt des Eigentümerwechsels. */
  beenden(periodId: string, validUntil: string): Observable<Eigentumsstand> {
    return this.http.post<Eigentumsstand>(
      `${this.base}/eigentum/${periodId}/beenden`,
      {},
      { params: new HttpParams().set('valid_until', validUntil) },
    );
  }

  addEigentuemer(periodId: string, payload: EigentuemerIn): Observable<Eigentumsstand> {
    return this.http.post<Eigentumsstand>(
      `${this.base}/eigentum/${periodId}/eigentuemer`,
      payload,
    );
  }

  updateEigentuemer(
    interestId: string,
    payload: EigentuemerPatch,
  ): Observable<Eigentumsstand> {
    return this.http.patch<Eigentumsstand>(
      `${this.base}/eigentuemer/${interestId}`,
      payload,
    );
  }

  /** Den Stand als geprüft bestätigen (Zeitpunkt + Person). */
  bestaetigen(periodId: string): Observable<Eigentumsstand> {
    return this.http.post<Eigentumsstand>(
      `${this.base}/eigentum/${periodId}/bestaetigen`,
      {},
    );
  }
}
