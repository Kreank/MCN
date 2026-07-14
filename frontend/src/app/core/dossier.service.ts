import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  AuftragDossier,
  KontaktDossier,
  LiegenschaftDossier,
  ProjektDossier,
} from './dossier.model';

/**
 * Typisierter Zugriff auf die Dossier-API (`/api/dossier/…`).
 *
 * Rein lesend — ein Dossier schreibt nie. Der Kern jeder Antwort ist hart getort
 * (403 ohne Modulrecht); einzelne Bausteine kommen bei fehlendem Recht als
 * `null` samt `<baustein>_sichtbar: false` zurück. Der Service reicht das
 * unverändert durch: **Er füllt nichts auf und ersetzt kein `null` durch 0** —
 * die Ansicht muss die Lücke benennen können.
 */
@Injectable({ providedIn: 'root' })
export class DossierService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/dossier';

  /** Kern: `identity/LESEN`. */
  kontakt(id: string): Observable<KontaktDossier> {
    return this.http.get<KontaktDossier>(`${this.base}/kontakt/${id}`);
  }

  /** Kern: `property/LESEN`. */
  liegenschaft(id: string): Observable<LiegenschaftDossier> {
    return this.http.get<LiegenschaftDossier>(`${this.base}/liegenschaft/${id}`);
  }

  /** Kern: `workflow/LESEN`. */
  projekt(id: string): Observable<ProjektDossier> {
    return this.http.get<ProjektDossier>(`${this.base}/projekt/${id}`);
  }

  /** Kern: `workflow/LESEN`. */
  auftrag(id: string): Observable<AuftragDossier> {
    return this.http.get<AuftragDossier>(`${this.base}/auftrag/${id}`);
  }
}
