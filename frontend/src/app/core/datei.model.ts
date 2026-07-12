// Vertrag zu /api/content (content.file / content.file_link in der DB).
// Eine Datei haengt an GENAU EINEM Zielobjekt (DB-CHECK num_nonnulls(...) = 1).
// Groessen kommen als Ganzzahl (Bytes), Zeitstempel als ISO-String.

/**
 * Zielfilter fuer die Datei-Ablage: GENAU EINES dieser Felder ist gesetzt. Der
 * Server flacht das Schema (django-ninja `Query`/`Form`) auf einzelne Form- bzw.
 * Query-Parameter ab — der Client schickt also `project_id=…`, nicht ein
 * verschachteltes JSON-Objekt.
 */
export interface ZielFilter {
  project_id?: string;
  property_id?: string;
  unit_id?: string;
  asset_id?: string;
  party_id?: string;
  service_case_id?: string;
  work_order_id?: string;
  service_job_id?: string;
  quote_id?: string;
  invoice_id?: string;
  article_id?: string;
  site_report_id?: string;
  /**
   * Attest (Arbeitsunfaehigkeitsbescheinigung) an einer Abwesenheit.
   * Gesundheitsdatum — besondere Kategorie nach DSGVO Art. 9. Der Server prueft
   * dieses Ziel mit einem EIGENEN Guard (nicht nur mit dem content-Recht): nur
   * der Betroffene selbst und die Personalverwaltung kommen heran, alles andere
   * ist 404. Nicht in `app-dateien` verwenden — dafuer gibt es `app-attest`.
   */
  absence_id?: string;
}

/** Fachliche Einordnung einer Verknuepfung (Freitext in der DB, gepflegte Liste). */
export type LinkKategorie =
  | 'DOKUMENT'
  | 'FOTO_VORHER'
  | 'FOTO_NACHHER'
  | 'VIDEO_BEGEHUNG'
  | 'SCAN'
  | 'PLAN'
  | 'VERTRAG'
  | 'SONSTIGES'
  | 'BELEG_PDF'
  // Vergibt der Server selbst am Ziel `absence_id` — nie von Hand waehlbar.
  | 'ATTEST';

/** Eine Datei samt ihrer Verknuepfung an einem Zielobjekt (DateiOut). */
export interface Datei {
  file_id: string;
  link_id: string;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  link_category: string | null;
  uploaded_at: string;
  uploaded_by: string | null;
}

export interface DateiListe {
  items: Datei[];
  total: number;
}

/** Ein heruntergeladener Dateiinhalt samt (aus der Antwort gelesenem) Namen. */
export interface DateiInhalt {
  blob: Blob;
  filename: string;
}
