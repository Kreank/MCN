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
