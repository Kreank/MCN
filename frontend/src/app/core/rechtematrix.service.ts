import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  AppUserRow,
  PermissionCell,
  PermissionMatrix,
  PermissionSetInput,
  Role,
  UserRole,
  UserRoleAssignInput,
  UserRoleEndInput,
} from './rechtematrix.model';

/**
 * Rechtematrix-API (Modul `security`). Lesen mit `security/LESEN`, Schreiben
 * (Matrix-Zelle setzen, Rolle zuweisen/beenden) mit `security/AENDERN` — der
 * Server setzt das durch, das UI blendet nur aus. Die Schreib-Endpunkte
 * antworten mit 422 (deutscher Klartext) auf die Härtungen: Selbst-Erweiterung
 * der eigenen Rolle, Selbstzuweisung, letzte aktive ADMINISTRATION.
 */
@Injectable({ providedIn: 'root' })
export class RechtematrixService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/security';

  // --- Rollen & Matrix -----------------------------------------------------

  listRoles(): Observable<Role[]> {
    return this.http.get<Role[]>(`${this.base}/roles`);
  }

  getPermissions(): Observable<PermissionMatrix> {
    return this.http.get<PermissionMatrix>(`${this.base}/permissions`);
  }

  setPermission(payload: PermissionSetInput): Observable<PermissionCell> {
    return this.http.put<PermissionCell>(`${this.base}/permissions`, payload);
  }

  // --- Benutzer & Rollenzuordnungen ----------------------------------------

  listUsers(): Observable<AppUserRow[]> {
    return this.http.get<AppUserRow[]>(`${this.base}/users`);
  }

  listUserRoles(activeOnly = false): Observable<UserRole[]> {
    const params = new HttpParams().set('active_only', activeOnly);
    return this.http.get<UserRole[]>(`${this.base}/user-roles`, { params });
  }

  assignRole(payload: UserRoleAssignInput): Observable<UserRole> {
    return this.http.post<UserRole>(`${this.base}/user-roles`, payload);
  }

  endUserRole(id: string, payload: UserRoleEndInput = {}): Observable<UserRole> {
    return this.http.post<UserRole>(`${this.base}/user-roles/${id}/end`, payload);
  }
}
