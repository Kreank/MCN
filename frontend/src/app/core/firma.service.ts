import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  Branch,
  BranchInput,
  BranchPatch,
  CompanyProfile,
  CompanyProfileInput,
  DunningLevel,
  DunningLevelPatch,
  Trade,
  TradeInput,
  TradePatch,
} from './firma.model';

/**
 * Firmeneinstellungen-API (Modul `company`) plus Mahnstufen-Pflege
 * (`/buchhaltung/dunning-levels`). Lesen für alle Rollen, Schreiben nur mit
 * Recht — der Server setzt das durch, das UI blendet nur aus.
 */
@Injectable({ providedIn: 'root' })
export class FirmaService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/company';
  private readonly buchhaltung = '/api/buchhaltung';

  // --- Firmenprofil --------------------------------------------------------

  getProfile(): Observable<CompanyProfile> {
    return this.http.get<CompanyProfile>(`${this.base}/profile`);
  }

  updateProfile(payload: CompanyProfileInput): Observable<CompanyProfile> {
    return this.http.put<CompanyProfile>(`${this.base}/profile`, payload);
  }

  // --- Firmenlogo ----------------------------------------------------------

  /**
   * Lädt das Firmenlogo hoch/ersetzt es (multipart). Recht: company/AENDERN.
   * Nur PNG/JPEG, <= 2 MB (der Server prüft und antwortet bei Verstoß mit 422).
   * Gibt das aktualisierte Profil zurück (u. a. `has_logo`).
   */
  uploadLogo(datei: File): Observable<CompanyProfile> {
    const form = new FormData();
    form.append('datei', datei, datei.name);
    return this.http.post<CompanyProfile>(`${this.base}/profile/logo`, form);
  }

  /** Entfernt das Firmenlogo (logo_file_id → NULL). Recht: company/AENDERN. */
  deleteLogo(): Observable<CompanyProfile> {
    return this.http.delete<CompanyProfile>(`${this.base}/profile/logo`);
  }

  /**
   * Holt die Logo-Bytes als Blob (durch die Anwendung, nicht per Direkt-URL) —
   * so gehen Session-Cookie und Rechteprüfung durch. Recht: company/LESEN.
   */
  getLogo(): Observable<Blob> {
    return this.http.get(`${this.base}/profile/logo`, { responseType: 'blob' });
  }

  // --- Niederlassungen -----------------------------------------------------

  listBranches(includeInactive = true): Observable<Branch[]> {
    const params = new HttpParams().set('include_inactive', includeInactive);
    return this.http.get<Branch[]>(`${this.base}/branches`, { params });
  }

  createBranch(payload: BranchInput): Observable<Branch> {
    return this.http.post<Branch>(`${this.base}/branches`, payload);
  }

  updateBranch(id: string, payload: BranchPatch): Observable<Branch> {
    return this.http.put<Branch>(`${this.base}/branches/${id}`, payload);
  }

  // --- Gewerke -------------------------------------------------------------

  listTrades(includeInactive = true): Observable<Trade[]> {
    const params = new HttpParams().set('include_inactive', includeInactive);
    return this.http.get<Trade[]>(`${this.base}/trades`, { params });
  }

  createTrade(payload: TradeInput): Observable<Trade> {
    return this.http.post<Trade>(`${this.base}/trades`, payload);
  }

  updateTrade(id: string, payload: TradePatch): Observable<Trade> {
    return this.http.put<Trade>(`${this.base}/trades/${id}`, payload);
  }

  // --- Mahnstufen ----------------------------------------------------------

  listDunningLevels(): Observable<DunningLevel[]> {
    return this.http.get<DunningLevel[]>(`${this.buchhaltung}/dunning-levels`);
  }

  updateDunningLevel(level: number, payload: DunningLevelPatch): Observable<DunningLevel> {
    return this.http.put<DunningLevel>(`${this.buchhaltung}/dunning-levels/${level}`, payload);
  }
}
