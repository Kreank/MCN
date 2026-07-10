import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { MailAccount, MailAccountInput, TestMailResult } from './mail.model';

/**
 * Mailversand-Einstellungen (Modul `company`). Lesen für alle Rollen, Ändern nur
 * mit `company/AENDERN` — der Server setzt das durch, das UI blendet nur aus.
 * Das SMTP-Passwort ist write-only und wird nie zurückgeliefert.
 */
@Injectable({ providedIn: 'root' })
export class MailService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/company/mail-account';

  getAccount(): Observable<MailAccount> {
    return this.http.get<MailAccount>(this.base);
  }

  saveAccount(payload: MailAccountInput): Observable<MailAccount> {
    return this.http.put<MailAccount>(this.base, payload);
  }

  sendTest(toAddress: string): Observable<TestMailResult> {
    return this.http.post<TestMailResult>(`${this.base}/test`, {
      to_address: toAddress,
    });
  }
}
