import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { Mandat, MandatIn, MandatPatch, ZustaendigkeitIn } from './verwaltung.model';

/**
 * Verwaltungsmandate einer Liegenschaft (dev-Proxy: /api -> :8000).
 *
 * Rechtemodul **`management`**. Unter `row_scope EIGENE` sieht der Monteur, wer
 * **sein** Objekt verwaltet und wen er anruft, wenn niemand aufmacht — und
 * ändert nichts. Fremdes Objekt → 404 (der Server entscheidet).
 *
 * **Kein Löschen**: Ein Mandat wird beendet (`status='ENDED'` + Enddatum). Der
 * **Umfang ist unveränderlich** (A-11) — es gibt bewusst keinen Weg, Einheiten
 * nachträglich hinzuzufügen oder zu entfernen; das wäre ein Nachfolgemandat.
 */
@Injectable({ providedIn: 'root' })
export class VerwaltungService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/management';

  /** Standardmäßig nur die **geltenden** Mandate; `historie` liefert die beendeten dazu. */
  list(propertyId: string, historie = false): Observable<Mandat[]> {
    const options = historie
      ? { params: new HttpParams().set('historie', 'true') }
      : undefined;
    return this.http.get<Mandat[]>(
      `${this.base}/properties/${propertyId}/mandate`,
      options,
    );
  }

  /** Mandat anlegen (Recht management.ANLEGEN). Standardkontakt ist Pflicht. */
  create(propertyId: string, payload: MandatIn): Observable<Mandat> {
    return this.http.post<Mandat>(
      `${this.base}/properties/${propertyId}/mandate`,
      payload,
    );
  }

  /** Nur Standardkontakt und Vertragsreferenz sind korrigierbar. */
  update(mandateId: string, payload: MandatPatch): Observable<Mandat> {
    return this.http.patch<Mandat>(`${this.base}/mandate/${mandateId}`, payload);
  }

  /** Mandat beenden — unumkehrbar. Das UI fragt vorher. */
  beenden(mandateId: string, validUntil: string): Observable<Mandat> {
    return this.http.post<Mandat>(`${this.base}/mandate/${mandateId}/beenden`, {
      valid_until: validUntil,
    });
  }

  addZustaendigkeit(mandateId: string, payload: ZustaendigkeitIn): Observable<Mandat> {
    return this.http.post<Mandat>(
      `${this.base}/mandate/${mandateId}/zustaendigkeiten`,
      payload,
    );
  }

  endZustaendigkeit(responsibilityId: string, validUntil: string): Observable<Mandat> {
    return this.http.post<Mandat>(
      `${this.base}/zustaendigkeiten/${responsibilityId}/beenden`,
      { valid_until: validUntil },
    );
  }
}
