import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  Entscheidung,
  EntscheidungErgebnis,
  OeffentlichesAngebot,
} from './oeffentliches-angebot.model';

/**
 * Der anmeldefreie Kundenweg. Kein Sitzungs-Cookie, kein Profil — die
 * Autorisierung ist ausschließlich das Token in der URL.
 *
 * Der `authInterceptor` bleibt trotzdem aktiv und ist hier genau richtig: Er
 * setzt `withCredentials` (damit das csrftoken-Cookie mitläuft) und schickt den
 * `X-CSRFToken`-Header beim POST. Seine 401-Umleitung nach `/login` kann nicht
 * greifen, weil diese Endpunkte nie 401 antworten — ungültige Token sind 404,
 * Drosselung ist 429.
 */
@Injectable({ providedIn: 'root' })
export class OeffentlichesAngebotService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/oeffentlich/angebot';

  /**
   * Lädt das Angebot hinter dem Token. Der Aufruf ist nebenwirkungsfrei: Er
   * verbraucht den Link nicht — und setzt zugleich das csrftoken-Cookie, das
   * die Entscheidung braucht.
   */
  laden(token: string): Observable<OeffentlichesAngebot> {
    return this.http.get<OeffentlichesAngebot>(`${this.base}/${encodeURIComponent(token)}`);
  }

  /** Annehmen oder ablehnen. Unumkehrbar — der Link ist danach verbraucht. */
  entscheiden(token: string, entscheidung: Entscheidung): Observable<EntscheidungErgebnis> {
    return this.http.post<EntscheidungErgebnis>(
      `${this.base}/${encodeURIComponent(token)}/entscheidung`,
      { entscheidung },
    );
  }
}
