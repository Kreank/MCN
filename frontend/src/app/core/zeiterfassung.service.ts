import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  Arbeitstag,
  ArbeitstagDetail,
  Ausgleich,
  AusgleichCreate,
  EintragCreate,
  EintragUpdate,
  Feiertag,
  KategorieCreate,
  KategorieUpdate,
  Pausenregel,
  StempelStart,
  StempelZustand,
  Stundenkonto,
  TagStatus,
  Zeiteintrag,
  ZeitMitarbeiter,
  Zeitkategorie,
  Zeitraum,
} from './zeiterfassung.model';

/**
 * Zeiterfassung-API.
 *
 * Die Stempeluhr nimmt bewusst **keine** Mitarbeiter-ID entgegen: sie wirkt
 * immer auf den angemeldeten Akteur. Ein Monteur kann sich nicht als jemand
 * anderes stempeln — das ist im Server so gebaut, nicht hier im Client.
 */
@Injectable({ providedIn: 'root' })
export class ZeiterfassungService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/zeiterfassung';
  private readonly hr = '/api/hr';

  // --- Stempeluhr ---------------------------------------------------------

  aktuell(): Observable<StempelZustand> {
    return this.http.get<StempelZustand>(`${this.base}/aktuell`);
  }

  start(payload: StempelStart): Observable<StempelZustand> {
    return this.http.post<StempelZustand>(`${this.base}/stempel/start`, payload);
  }

  /**
   * Pause/Weiter/Stopp nehmen die Begründung als Query-Parameter (kein Body —
   * Repo-Muster wie `pausen-anwenden` und `eintragLoeschen`). Sie ist nur nötig,
   * wenn der Arbeitstag schon bestätigt ist; er fällt dann auf Entwurf zurück.
   */
  pause(correctionReason?: string): Observable<StempelZustand> {
    return this.stempel('pause', correctionReason);
  }

  weiter(correctionReason?: string): Observable<StempelZustand> {
    return this.stempel('weiter', correctionReason);
  }

  stopp(correctionReason?: string): Observable<StempelZustand> {
    return this.stempel('stopp', correctionReason);
  }

  private stempel(aktion: string, correctionReason?: string): Observable<StempelZustand> {
    let params = new HttpParams();
    if (correctionReason) params = params.set('correction_reason', correctionReason);
    return this.http.post<StempelZustand>(
      `${this.base}/stempel/${aktion}`,
      {},
      { params },
    );
  }

  // --- Meine Tage ---------------------------------------------------------

  meineTage(von?: string, bis?: string): Observable<Arbeitstag[]> {
    let params = new HttpParams();
    if (von) params = params.set('von', von);
    if (bis) params = params.set('bis', bis);
    return this.http.get<Arbeitstag[]>(`${this.base}/meine-tage`, { params });
  }

  tag(id: string): Observable<ArbeitstagDetail> {
    return this.http.get<ArbeitstagDetail>(`${this.base}/tage/${id}`);
  }

  einreichen(id: string): Observable<ArbeitstagDetail> {
    return this.http.post<ArbeitstagDetail>(`${this.base}/tage/${id}/einreichen`, {});
  }

  // --- Verwaltung ---------------------------------------------------------

  liste(query: {
    zeitraum?: Zeitraum;
    von?: string;
    bis?: string;
    user_id?: string;
    status?: TagStatus;
  }): Observable<Arbeitstag[]> {
    let params = new HttpParams();
    if (query.zeitraum) params = params.set('zeitraum', query.zeitraum);
    if (query.von) params = params.set('von', query.von);
    if (query.bis) params = params.set('bis', query.bis);
    if (query.user_id) params = params.set('user_id', query.user_id);
    if (query.status) params = params.set('status', query.status);
    return this.http.get<Arbeitstag[]>(this.base, { params });
  }

  /** Filterliste der Verwaltungssicht (nur mit hr/LESEN + row_scope ALLE). */
  mitarbeitende(): Observable<ZeitMitarbeiter[]> {
    return this.http.get<ZeitMitarbeiter[]>(`${this.base}/mitarbeitende`);
  }

  bestaetigen(id: string): Observable<ArbeitstagDetail> {
    return this.http.post<ArbeitstagDetail>(`${this.base}/tage/${id}/bestaetigen`, {});
  }

  ablehnen(id: string, note: string): Observable<ArbeitstagDetail> {
    return this.http.post<ArbeitstagDetail>(`${this.base}/tage/${id}/ablehnen`, { note });
  }

  pausenAnwenden(id: string, correctionReason?: string): Observable<ArbeitstagDetail> {
    let params = new HttpParams();
    if (correctionReason) params = params.set('correction_reason', correctionReason);
    return this.http.post<ArbeitstagDetail>(
      `${this.base}/tage/${id}/pausen-anwenden`,
      {},
      { params },
    );
  }

  // --- Zeiteinträge -------------------------------------------------------

  eintragAnlegen(payload: EintragCreate): Observable<Zeiteintrag> {
    return this.http.post<Zeiteintrag>(`${this.base}/eintraege`, payload);
  }

  eintragAendern(id: string, payload: EintragUpdate): Observable<Zeiteintrag> {
    return this.http.patch<Zeiteintrag>(`${this.base}/eintraege/${id}`, payload);
  }

  eintragLoeschen(id: string, correctionReason?: string): Observable<{ detail: string }> {
    let params = new HttpParams();
    if (correctionReason) params = params.set('correction_reason', correctionReason);
    return this.http.delete<{ detail: string }>(`${this.base}/eintraege/${id}`, { params });
  }

  /** Zeiten an einem Einsatz — Grundlage der Buchung im Baustellenbericht. */
  eintraegeAmEinsatz(jobId: string): Observable<Zeiteintrag[]> {
    return this.http.get<Zeiteintrag[]>(`${this.base}/einsaetze/${jobId}/eintraege`);
  }

  // --- Stundenkonto + Export ---------------------------------------------

  /** Ohne Angabe: das eigene Konto. `userId` = app_user (Sicht der Zeiterfassung). */
  stundenkonto(userId?: string, von?: string, bis?: string): Observable<Stundenkonto> {
    let params = new HttpParams();
    if (userId) params = params.set('user_id', userId);
    if (von) params = params.set('von', von);
    if (bis) params = params.set('bis', bis);
    return this.http.get<Stundenkonto>(`${this.base}/stundenkonto`, { params });
  }

  /**
   * Stundenliste als CSV — die Vorlagefähigkeit nach § 17 MiLoG.
   * Download über Blob (nicht `window.open`): der Auth-Cookie und der
   * CSRF-Header müssen mit; Repo-Muster aus `core/datei.service.ts`.
   */
  stundenlisteCsv(von?: string, bis?: string, userId?: string): Observable<Blob> {
    let params = new HttpParams();
    if (von) params = params.set('von', von);
    if (bis) params = params.set('bis', bis);
    if (userId) params = params.set('user_id', userId);
    return this.http.get(`${this.base}/stundenliste.csv`, {
      params,
      responseType: 'blob',
    });
  }

  // --- Stundenausgleich ---------------------------------------------------

  /**
   * Die Ausgleichsbuchungen. Ohne `employeeId` alle (Verwaltung); der
   * Beschäftigte selbst sieht immer nur sein eigenes Konto (der Server filtert).
   */
  ausgleiche(employeeId?: string, von?: string, bis?: string): Observable<Ausgleich[]> {
    let params = new HttpParams();
    if (employeeId) params = params.set('employee_id', employeeId);
    if (von) params = params.set('von', von);
    if (bis) params = params.set('bis', bis);
    return this.http.get<Ausgleich[]>(`${this.base}/ausgleich`, { params });
  }

  ausgleichBuchen(payload: AusgleichCreate): Observable<Ausgleich> {
    return this.http.post<Ausgleich>(`${this.base}/ausgleich`, payload);
  }

  /** Storno statt Löschen (GoBD): es entsteht eine Gegenbuchung. */
  ausgleichStornieren(id: string, reason: string): Observable<Ausgleich> {
    return this.http.post<Ausgleich>(`${this.base}/ausgleich/${id}/stornieren`, {
      reason,
    });
  }

  // --- Stammdaten ---------------------------------------------------------

  kategorien(includeArchived = false): Observable<Zeitkategorie[]> {
    const params = new HttpParams().set('include_archived', includeArchived);
    return this.http.get<Zeitkategorie[]>(`${this.hr}/zeitkategorien`, { params });
  }

  kategorieAnlegen(payload: KategorieCreate): Observable<Zeitkategorie> {
    return this.http.post<Zeitkategorie>(`${this.hr}/zeitkategorien`, payload);
  }

  kategorieAendern(id: string, payload: KategorieUpdate): Observable<Zeitkategorie> {
    return this.http.patch<Zeitkategorie>(`${this.hr}/zeitkategorien/${id}`, payload);
  }

  kategorieArchivieren(id: string): Observable<Zeitkategorie> {
    return this.http.post<Zeitkategorie>(`${this.hr}/zeitkategorien/${id}/archivieren`, {});
  }

  pausenregel(): Observable<Pausenregel> {
    return this.http.get<Pausenregel>(`${this.hr}/pausenregel`);
  }

  pausenregelSetzen(regel: Pausenregel): Observable<Pausenregel> {
    return this.http.put<Pausenregel>(`${this.hr}/pausenregel`, regel);
  }

  feiertage(jahr?: number): Observable<Feiertag[]> {
    let params = new HttpParams();
    if (jahr) params = params.set('jahr', jahr);
    return this.http.get<Feiertag[]>(`${this.hr}/feiertage`, { params });
  }
}
