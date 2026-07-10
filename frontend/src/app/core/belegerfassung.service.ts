import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  CostCenter,
  CostCenterInput,
  CostCenterPatch,
  LedgerAccount,
  LedgerAccountInput,
  LedgerAccountPatch,
  ReceiptCreate,
  ReceiptDetail,
  ReceiptPage,
  ReceiptQuery,
  ReceiptUpdate,
  StatusInput,
} from './belegerfassung.model';

/** Typisierter Zugriff auf die Belegerfassungs-API (dev-Proxy: /api -> :8000). */
@Injectable({ providedIn: 'root' })
export class BelegerfassungService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/accounting';

  // --- Eingangsbelege ------------------------------------------------------

  listReceipts(query: ReceiptQuery): Observable<ReceiptPage> {
    let params = new HttpParams()
      .set('page', query.page)
      .set('page_size', query.page_size);
    const q = query.q?.trim();
    if (q) params = params.set('q', q);
    if (query.status) params = params.set('status', query.status);
    return this.http.get<ReceiptPage>(`${this.base}/receipts`, { params });
  }

  getReceipt(id: string): Observable<ReceiptDetail> {
    return this.http.get<ReceiptDetail>(`${this.base}/receipts/${id}`);
  }

  // --- Schreibend (Session-Auth Pflicht) -----------------------------------

  /** Neuen Eingangsbeleg (Status ERFASST) mit Positionen anlegen. */
  createReceipt(payload: ReceiptCreate): Observable<ReceiptDetail> {
    return this.http.post<ReceiptDetail>(`${this.base}/receipts`, payload);
  }

  /** Beleg bearbeiten (nur ERFASST/GEPRUEFT; ab FREIGEGEBEN sperrt die DB). */
  updateReceipt(id: string, payload: ReceiptUpdate): Observable<ReceiptDetail> {
    return this.http.put<ReceiptDetail>(`${this.base}/receipts/${id}`, payload);
  }

  /** Statuswechsel (ERFASST→GEPRUEFT→FREIGEGEBEN→GEBUCHT, +ABGELEHNT). */
  advanceStatus(id: string, payload: StatusInput): Observable<ReceiptDetail> {
    return this.http.post<ReceiptDetail>(`${this.base}/receipts/${id}/status`, payload);
  }

  // --- Stammdaten: Buchungskonten ------------------------------------------

  listLedgerAccounts(includeInactive = true): Observable<LedgerAccount[]> {
    const params = new HttpParams().set('include_inactive', includeInactive);
    return this.http.get<LedgerAccount[]>(`${this.base}/ledger-accounts`, { params });
  }

  createLedgerAccount(payload: LedgerAccountInput): Observable<LedgerAccount> {
    return this.http.post<LedgerAccount>(`${this.base}/ledger-accounts`, payload);
  }

  updateLedgerAccount(id: string, patch: LedgerAccountPatch): Observable<LedgerAccount> {
    return this.http.put<LedgerAccount>(`${this.base}/ledger-accounts/${id}`, patch);
  }

  // --- Stammdaten: Kostenstellen -------------------------------------------

  listCostCenters(includeInactive = true): Observable<CostCenter[]> {
    const params = new HttpParams().set('include_inactive', includeInactive);
    return this.http.get<CostCenter[]>(`${this.base}/cost-centers`, { params });
  }

  createCostCenter(payload: CostCenterInput): Observable<CostCenter> {
    return this.http.post<CostCenter>(`${this.base}/cost-centers`, payload);
  }

  updateCostCenter(id: string, patch: CostCenterPatch): Observable<CostCenter> {
    return this.http.put<CostCenter>(`${this.base}/cost-centers/${id}`, patch);
  }
}
