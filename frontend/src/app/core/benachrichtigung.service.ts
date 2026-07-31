import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject, signal } from '@angular/core';
import { Observable, tap } from 'rxjs';
import {
  BenachrichtigungSeite,
  BenachrichtigungZaehler,
} from './benachrichtigung.model';

/**
 * Zugriff auf das persönliche Postfach — und der Zähler, den die Glocke zeigt.
 *
 * Der Zähler liegt als Signal IM Service, nicht in der Komponente: Er wird von
 * mehreren Stellen gesetzt (Hintergrundabfrage, Öffnen des Panels, Lesen einer
 * Zeile), und jede Antwort des Servers bringt ihn frisch mit. Zwei Kopien
 * desselben Werts liefen sonst auseinander, und die Glocke zeigte eine Zahl,
 * die nichts mehr bedeutet.
 */
@Injectable({ providedIn: 'root' })
export class BenachrichtigungService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/benachrichtigungen';

  /** Ungelesene Meldungen des angemeldeten Kontos. */
  readonly ungelesen = signal(0);

  liste(page = 1, page_size = 20, nur_ungelesen = false): Observable<BenachrichtigungSeite> {
    const params = new HttpParams()
      .set('page', page)
      .set('page_size', page_size)
      .set('nur_ungelesen', nur_ungelesen);
    return this.http
      .get<BenachrichtigungSeite>(this.base, { params })
      .pipe(tap((s) => this.ungelesen.set(s.ungelesen)));
  }

  /** Nur der Zähler — die Hintergrundabfrage der Glocke. */
  zaehler(): Observable<BenachrichtigungZaehler> {
    return this.http
      .get<BenachrichtigungZaehler>(`${this.base}/zaehler`)
      .pipe(tap((z) => this.ungelesen.set(z.ungelesen)));
  }

  gelesen(id: string): Observable<BenachrichtigungZaehler> {
    return this.http
      .post<BenachrichtigungZaehler>(`${this.base}/${id}/gelesen`, {})
      .pipe(tap((z) => this.ungelesen.set(z.ungelesen)));
  }

  alleGelesen(): Observable<BenachrichtigungZaehler> {
    return this.http
      .post<BenachrichtigungZaehler>(`${this.base}/alle-gelesen`, {})
      .pipe(tap((z) => this.ungelesen.set(z.ungelesen)));
  }
}
