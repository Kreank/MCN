import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { Gebaeudeansicht } from './gebaeudeansicht.model';

/**
 * Die Liegenschaft als Gebäudeschnitt (dev-Proxy: /api -> :8000).
 *
 * **Ein Aufruf, ein Bild.** Struktur, Belegung und Technik in einer Antwort;
 * als drei Aufrufe wären es drei Rundreisen und drei Fehlerzustände für eine
 * Ansicht, die auf einen Blick verstanden werden soll.
 */
@Injectable({ providedIn: 'root' })
export class GebaeudeansichtService {
  private readonly http = inject(HttpClient);

  get(propertyId: string): Observable<Gebaeudeansicht> {
    return this.http.get<Gebaeudeansicht>(
      `/api/property/properties/${propertyId}/gebaeudeansicht`,
    );
  }
}
