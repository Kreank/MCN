import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  Aufmass,
  AufbauIn,
  Auslegung,
  AuslegungIn,
  GrundrissIn,
  Room,
  RoomIn,
  RoomPatch,
  RoomStatus,
} from './raum.model';

/**
 * Typisierter Zugriff auf das Raumaufmaß (dev-Proxy: /api -> :8000).
 *
 * Der **Aufbau** (Hüllflächen + Öffnungen) wird als GANZES geschrieben
 * (`PUT /rooms/{id}/aufbau`): die Öffnungen referenzieren ihre Wand über
 * `surface_ref`, und diese Refs vergibt der Client. Ein Teil-Update wäre nicht
 * eindeutig — eine Öffnung könnte auf eine Wand zeigen, die es (noch) nicht gibt.
 *
 * Die **Heizlast rechnet ausschließlich der Server**; dieser Service liefert sie
 * nur aus. Ist sie null, ist sie unbekannt (mit Grund) — nie 0.
 */
@Injectable({ providedIn: 'root' })
export class RaumService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/property';

  /**
   * Räume einer Liegenschaft (Recht property.LESEN) — **standardmäßig nur die
   * aktiven**. Stillgelegte Räume (`status = 'INAKTIV'`) liefert der Server nur
   * auf ausdrückliche Nachfrage; er hält sie auch aus den Summen heraus.
   */
  list(propertyId: string, mitInaktiven = false): Observable<Room[]> {
    const options = mitInaktiven
      ? { params: new HttpParams().set('mit_inaktiven', 'true') }
      : undefined;
    return this.http.get<Room[]>(`${this.base}/properties/${propertyId}/rooms`, options);
  }

  /** Raum anlegen (Recht property.ANLEGEN). */
  create(propertyId: string, payload: RoomIn): Observable<Room> {
    return this.http.post<Room>(`${this.base}/properties/${propertyId}/rooms`, payload);
  }

  get(roomId: string): Observable<Room> {
    return this.http.get<Room>(`${this.base}/rooms/${roomId}`);
  }

  /** Raumfelder ändern — nur gesetzte Felder (Recht property.AENDERN). */
  update(roomId: string, payload: RoomPatch): Observable<Room> {
    return this.http.patch<Room>(`${this.base}/rooms/${roomId}`, payload);
  }

  /**
   * Raum stilllegen bzw. wieder aktivieren (Recht property.AENDERN).
   *
   * **Gelöscht wird nie** — ein Aufmaß ist ein Nachweis über den Bestand. Ein
   * weggefallener Raum (Mieter legt zwei Zimmer zusammen) wird stillgelegt: er
   * bleibt lesbar, zählt aber nicht mehr in die Summen.
   */
  setStatus(roomId: string, status: RoomStatus): Observable<Room> {
    return this.update(roomId, { status });
  }

  /** Hüllflächen und Öffnungen **vollständig ersetzen** (Recht property.AENDERN). */
  setAufbau(roomId: string, payload: AufbauIn): Observable<Room> {
    return this.http.put<Room>(`${this.base}/rooms/${roomId}/aufbau`, payload);
  }

  /**
   * Den **Umriss** des Raumes setzen (Recht property.AENDERN) — Punkte in ganzen
   * Millimetern, Reihenfolge = Umlauf.
   *
   * **Wer zeichnet, misst nicht doppelt:** Der Server rechnet danach
   * `floor_area_m2` (Trapezformel, Betrag) und `perimeter_m` aus dem Polygon und
   * schreibt sie. Die Antwort ist der komplette Raum — sie, nicht die Vorschau
   * des Editors, ist die Wahrheit.
   *
   * Ein **leeres Array entfernt den Umriss**: Fläche und Umfang sind danach
   * wieder Handeingabe, die `edge_index` der Wände fallen auf null.
   */
  setGrundriss(roomId: string, vertices: GrundrissIn['vertices']): Observable<Room> {
    const payload: GrundrissIn = { vertices };
    return this.http.put<Room>(`${this.base}/rooms/${roomId}/grundriss`, payload);
  }

  /**
   * Aufmaß-Summe der Liegenschaft — **ohne Rechenparameter**: die
   * Auslegungs-Außentemperatur und der Gebäudekennwert stehen am Objekt
   * (`property.design_outdoor_temp_c` / `heat_load_w_per_m2`, Migration 0089),
   * der Server holt sie sich selbst. Die Antwort liefert sie mit, damit das UI
   * sie anzeigen und pflegen kann.
   */
  aufmass(propertyId: string): Observable<Aufmass> {
    return this.http.get<Aufmass>(`${this.base}/properties/${propertyId}/aufmass`);
  }

  /**
   * Auslegungsdaten des Objekts pflegen (Recht property.AENDERN). Nicht
   * gesendete Felder bleiben unverändert; ein explizites `null` setzt zurück.
   *
   * Sie gelten für ALLE Räume der Liegenschaft — ohne Außentemperatur bleibt die
   * raumweise Heizlast unbekannt.
   */
  setAuslegung(propertyId: string, payload: AuslegungIn): Observable<Auslegung> {
    return this.http.patch<Auslegung>(`${this.base}/properties/${propertyId}/auslegung`, payload);
  }
}
