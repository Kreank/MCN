import { HttpClient, HttpEvent, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  CredentialIn,
  CredentialStatus,
  DatanormImportErgebnis,
  PunchoutSession,
  PunchoutSessionIn,
  PunchoutSessionStart,
  SupplierConnection,
  SupplierConnectionIn,
  SupplierConnectionPatch,
} from './anbindung.model';

/** Typisierter Zugriff auf die Lieferanten-Anbindungs-API (/api/pricing/supplier-connections). */
@Injectable({ providedIn: 'root' })
export class AnbindungService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/pricing/supplier-connections';

  list(includeInactive = true): Observable<SupplierConnection[]> {
    const params = new HttpParams().set('include_inactive', includeInactive);
    return this.http.get<SupplierConnection[]>(this.base, { params });
  }

  create(payload: SupplierConnectionIn): Observable<SupplierConnection> {
    return this.http.post<SupplierConnection>(this.base, payload);
  }

  update(id: string, payload: SupplierConnectionPatch): Observable<SupplierConnection> {
    return this.http.patch<SupplierConnection>(`${this.base}/${id}`, payload);
  }

  // --- IDS-Connect: Zugangsdaten (Passwort write-only) ---------------------
  credentials(id: string): Observable<CredentialStatus> {
    return this.http.get<CredentialStatus>(`${this.base}/${id}/credentials`);
  }

  setCredentials(id: string, payload: CredentialIn): Observable<CredentialStatus> {
    return this.http.put<CredentialStatus>(`${this.base}/${id}/credentials`, payload);
  }

  // --- IDS-Connect: Warenkorb-Roundtrip ------------------------------------
  /** Startet eine Punchout-Session; liefert das an den Shop zu submittende Formular. */
  startPunchoutSession(id: string, payload: PunchoutSessionIn): Observable<PunchoutSessionStart> {
    return this.http.post<PunchoutSessionStart>(`${this.base}/${id}/punchout-session`, payload);
  }

  /** Status + (falls eingelöst) aufgelöste Positionen einer Punchout-Session. */
  punchoutSession(sessionId: string): Observable<PunchoutSession> {
    return this.http.get<PunchoutSession>(
      `/api/pricing/punchout-sessions/${sessionId}`,
    );
  }

  // --- DATANORM-Import (Datei-Upload) --------------------------------------
  /** Importiert eine DATANORM-Datei (Stamm + optional Preise). `dryRun`=Vorschau.
   * Liefert HTTP-Events (Upload-Fortschritt + Antwort), damit die Oberfläche einen
   * Fortschritt zeigen und den Request abbrechen kann. */
  datanormImport(
    id: string,
    stamm: File,
    preise: File | null,
    dryRun: boolean,
  ): Observable<HttpEvent<DatanormImportErgebnis>> {
    const fd = new FormData();
    fd.append('stamm', stamm);
    if (preise) fd.append('preise', preise);
    fd.append('dry_run', dryRun ? 'true' : 'false');
    return this.http.post<DatanormImportErgebnis>(
      `${this.base}/${id}/imports/datanorm`,
      fd,
      { reportProgress: true, observe: 'events' },
    );
  }
}
