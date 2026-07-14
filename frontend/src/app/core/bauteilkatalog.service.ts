import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, forkJoin } from 'rxjs';
import { Bauteil, BauteilGattung, BauteilIn, BauteilPatch } from './bauteilkatalog.model';

/** Die beiden Listen, wie der Raum-Editor sie braucht (je Gattung getrennt). */
export interface BauteilVorlagen {
  flaechen: Bauteil[];
  oeffnungen: Bauteil[];
}

/**
 * Bauteilkatalog (`/api/property/component-templates`).
 *
 * **Kein Löschen** — die Datenbank verbietet es. Stillgelegt wird über
 * `PATCH { status: 'INAKTIV' }`; der Katalog bleibt damit als Nachweis bestehen,
 * und Aufmaße, die eine Vorlage benutzt haben, verlieren nichts (der U-Wert ist
 * ohnehin kopiert, nicht verlinkt).
 */
@Injectable({ providedIn: 'root' })
export class BauteilkatalogService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/property';

  /** Vorlagen einer Gattung (Recht property/LESEN). `nurAktive=false` zeigt auch Stillgelegte. */
  list(kind?: BauteilGattung, nurAktive = true): Observable<Bauteil[]> {
    let params = new HttpParams().set('nur_aktive', nurAktive ? 'true' : 'false');
    if (kind) params = params.set('kind', kind);
    return this.http.get<Bauteil[]>(`${this.base}/component-templates`, { params });
  }

  /**
   * Beide Gattungen auf einmal — für den Raum-Editor, der Hüllflächen UND
   * Öffnungen anbietet. Zwei Anfragen statt einer ungefilterten: der Vertrag
   * filtert über `kind`, und beide Listen werden getrennt gebraucht.
   */
  vorlagen(nurAktive = true): Observable<BauteilVorlagen> {
    return forkJoin({
      flaechen: this.list('FLAECHE', nurAktive),
      oeffnungen: this.list('OEFFNUNG', nurAktive),
    });
  }

  /** Vorlage anlegen (Recht property/ANLEGEN). */
  create(payload: BauteilIn): Observable<Bauteil> {
    return this.http.post<Bauteil>(`${this.base}/component-templates`, payload);
  }

  /** Vorlage ändern — U-Wert nachtragen, umbenennen, stilllegen (Recht property/AENDERN). */
  update(id: string, patch: BauteilPatch): Observable<Bauteil> {
    return this.http.patch<Bauteil>(`${this.base}/component-templates/${id}`, patch);
  }
}
