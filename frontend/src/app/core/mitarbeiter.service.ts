import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  Absence,
  AbsenceCreate,
  AbsenceDecision,
  Contract,
  ContractCreate,
  ContractTerminate,
  Employee,
  EmployeeCreate,
  EmployeeDetail,
  EmployeePage,
  EmployeeQuery,
  EmployeeStatusChange,
  VacationAccount,
  VacationBudget,
} from './mitarbeiter.model';

/** Typisierter Zugriff auf die Personal-API (dev-Proxy: /api -> :8000). */
@Injectable({ providedIn: 'root' })
export class MitarbeiterService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/hr/employees';

  list(query: EmployeeQuery): Observable<EmployeePage> {
    let params = new HttpParams()
      .set('page', query.page)
      .set('page_size', query.page_size);
    const q = query.q?.trim();
    if (q) params = params.set('q', q);
    if (query.status) params = params.set('status', query.status);
    return this.http.get<EmployeePage>(this.base, { params });
  }

  get(id: string): Observable<EmployeeDetail> {
    return this.http.get<EmployeeDetail>(`${this.base}/${id}`);
  }

  /** Selbstauskunft: die EIGENE Personalakte des angemeldeten Kontos
   * (Resturlaub, Verträge, eigene Abwesenheiten). Recht hr/LESEN; liefert
   * ausschließlich die eigene Zeile. 404, wenn kein Mitarbeiterdatensatz. */
  getSelf(): Observable<EmployeeDetail> {
    return this.http.get<EmployeeDetail>('/api/hr/self');
  }

  // --- Schreibend (Session-Auth Pflicht) -----------------------------------

  /** Personalsatz anlegen (Status AKTIV). */
  createEmployee(payload: EmployeeCreate): Observable<Employee> {
    return this.http.post<Employee>(this.base, payload);
  }

  /** Statuswechsel; AUSGETRETEN verlangt ein Austrittsdatum und ist final. */
  setStatus(employeeId: string, payload: EmployeeStatusChange): Observable<Employee> {
    return this.http.post<Employee>(`${this.base}/${employeeId}/status`, payload);
  }

  /** Arbeitsvertrag anlegen (ein laufender Vorgänger wird am Vortag beendet). */
  createContract(employeeId: string, payload: ContractCreate): Observable<Contract> {
    return this.http.post<Contract>(`${this.base}/${employeeId}/contracts`, payload);
  }

  /** Vertrag kündigen (begründungspflichtig). */
  terminateContract(contractId: string, payload: ContractTerminate): Observable<Contract> {
    return this.http.post<Contract>(
      `/api/hr/contracts/${contractId}/terminate`,
      payload,
    );
  }

  /** Abwesenheitsantrag anlegen (Status ENTWURF). days_count rechnet der Server. */
  createAbsence(employeeId: string, payload: AbsenceCreate): Observable<Absence> {
    return this.http.post<Absence>(`${this.base}/${employeeId}/absences`, payload);
  }

  submitAbsence(absenceId: string): Observable<Absence> {
    return this.http.post<Absence>(`/api/hr/absences/${absenceId}/submit`, {});
  }

  /** Genehmigen (Recht FREIGEBEN). */
  approveAbsence(absenceId: string, payload: AbsenceDecision): Observable<Absence> {
    return this.http.post<Absence>(`/api/hr/absences/${absenceId}/approve`, payload);
  }

  /** Ablehnen (Recht FREIGEBEN, begründungspflichtig). */
  rejectAbsence(absenceId: string, payload: AbsenceDecision): Observable<Absence> {
    return this.http.post<Absence>(`/api/hr/absences/${absenceId}/reject`, payload);
  }

  withdrawAbsence(absenceId: string): Observable<Absence> {
    return this.http.post<Absence>(`/api/hr/absences/${absenceId}/withdraw`, {});
  }

  /** Urlaubskonto eines Jahres setzen (idempotent, Anpassung begründungspflichtig). */
  setVacationBudget(
    employeeId: string,
    payload: VacationBudget,
  ): Observable<VacationAccount> {
    return this.http.put<VacationAccount>(
      `${this.base}/${employeeId}/vacation-budget`,
      payload,
    );
  }
}
