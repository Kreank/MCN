// Vertrag zu /api/workflow/site_reports (workflow.site_report in der DB).
// Ein Baustellenbericht hängt an einem ANKER: am Auftrag (work_order), am Einsatz
// (service_job) oder an beidem — nie im Leeren. Beim **freien Termin** (Einsatz
// ohne Auftrag) ist `work_order_id` null: das ist das Begehungsprotokoll. Fotos
// hängen über die Datei-Ablage (site_report_id) daran. Die Kundenunterschrift
// besiegelt den Bericht (ENTWURF → UNTERZEICHNET); danach ist er unveränderlich.

export type SiteReportStatus = 'ENTWURF' | 'UNTERZEICHNET';

export interface SiteReport {
  id: string;
  work_order_id: string | null;
  service_job_id: string | null;
  report_date: string;
  author_id: string | null;
  author_name: string | null;
  weather: string | null;
  activity_text: string;
  hours_worked: string | null;
  materials_note: string | null;
  remarks: string | null;
  status: SiteReportStatus;
  signed_by_name: string | null;
  signed_at: string | null;
  signature_file_id: string | null;
  version: number;
  created_at: string;
}

export interface SiteReportListe {
  items: SiteReport[];
  total: number;
}

// POST /api/workflow/site_reports — mindestens eines von work_order_id und
// service_job_id ist Pflicht (Anker). Beim freien Termin nur service_job_id; der
// Server leitet den Auftrag aus dem Einsatz ab.
export interface SiteReportCreate {
  report_date: string;
  activity_text: string;
  work_order_id?: string | null;
  service_job_id?: string | null;
  weather?: string | null;
  hours_worked?: string | null;
  materials_note?: string | null;
  remarks?: string | null;
}

// PUT /api/workflow/site_reports/{id} — nur gesetzte Felder werden geändert.
export interface SiteReportUpdate {
  report_date?: string | null;
  service_job_id?: string | null;
  weather?: string | null;
  activity_text?: string | null;
  hours_worked?: string | null;
  materials_note?: string | null;
  remarks?: string | null;
}

// POST /api/workflow/site_reports/{id}/sign
export interface SiteReportSign {
  signed_by_name: string;
  signature_png_base64: string;
}

const STATUS_LABELS: Record<SiteReportStatus, string> = {
  ENTWURF: 'Entwurf',
  UNTERZEICHNET: 'Unterzeichnet',
};

export function siteReportStatusLabel(s: SiteReportStatus): string {
  return STATUS_LABELS[s] ?? s;
}

export function siteReportStatusClass(s: SiteReportStatus): string {
  return s === 'UNTERZEICHNET' ? 'stamp--positive' : '';
}
