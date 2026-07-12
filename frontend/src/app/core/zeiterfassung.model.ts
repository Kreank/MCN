/**
 * Zeiterfassung — Typen der API (`/api/zeiterfassung`, `/api/hr/zeitkategorien`).
 *
 * Ein Zeitstrahl, zwei Auswertungen: `workflow.time_entry` ist die einzige
 * Quelle der Wahrheit. Ob eine Buchung Arbeitszeit im Sinne von ArbZG/MiLoG ist,
 * entscheidet allein `is_work_time` der Kategorie — nicht ihr Name.
 *
 * Dauern kommen als **Sekunden** (Ganzzahl), Stundenwerte als **Decimal-String**
 * (verlustfrei, Repo-Konvention). Beides wird erst zur Anzeige formatiert.
 */

export type Zustand = 'GESTOPPT' | 'LAEUFT' | 'PAUSE';
export type TagStatus = 'ENTWURF' | 'EINGEREICHT' | 'BESTAETIGT' | 'ABGELEHNT';
export type PausenModus = 'KEINE' | 'GESETZLICH' | 'FESTE_ZEITEN';

export interface Zeitkategorie {
  id: string;
  code: string | null;
  name: string;
  description: string | null;
  is_work_time: boolean;
  is_system: boolean;
  status: 'AKTIV' | 'ARCHIVIERT';
  sort_order: number;
}

export interface KategorieCreate {
  name: string;
  is_work_time: boolean;
  description?: string | null;
  sort_order?: number;
}

export type KategorieUpdate = Partial<KategorieCreate>;

export interface Pausenfenster {
  von: string; // HH:MM
  bis: string; // HH:MM
}

export interface Pausenregel {
  mode: PausenModus;
  fixed_breaks: Pausenfenster[];
}

export interface Feiertag {
  day: string;
  name: string;
  region: string | null;
}

export interface Zeiteintrag {
  id: string;
  work_day_id: string;
  user_id: string;
  user: string | null;
  category_id: string;
  kategorie: string;
  is_work_time: boolean;
  started_at: string;
  /** null = läuft gerade. */
  ended_at: string | null;
  dauer_sekunden: number | null;
  /** Vom System eingesetzte Pflichtpause — im UI gekennzeichnet. */
  auto_generated: boolean;
  service_job_id: string | null;
  einsatz: string | null;
  note: string | null;
}

export interface EintragCreate {
  category_id: string;
  started_at: string;
  ended_at: string;
  user_id?: string | null;
  service_job_id?: string | null;
  note?: string | null;
  correction_reason?: string | null;
}

export interface EintragUpdate {
  category_id?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  note?: string | null;
  correction_reason?: string | null;
}

export interface Arbeitstag {
  id: string;
  user_id: string;
  user: string | null;
  day: string;
  status: TagStatus;
  submitted_at: string | null;
  decided_at: string | null;
  decided_by: string | null;
  decision_note: string | null;
  arbeit_sekunden: number;
  pause_sekunden: number;
  laeuft: boolean;
  eintraege_anzahl: number;
}

export interface ArbeitstagDetail extends Arbeitstag {
  eintraege: Zeiteintrag[];
}

export interface StempelZustand {
  laeuft: boolean;
  zustand: Zustand;
  /** Die laufende Buchung begann vor heute — vergessenes Stoppen. */
  ueberfaellig: boolean;
  eintrag: Zeiteintrag | null;
  /**
   * Bezugstag der Summen (YYYY-MM-DD). Bei vergessenem Stoppen ist das der Tag
   * der laufenden Buchung, also der **Vortag** — nicht heute. Die Summen tragen
   * deshalb `tag_`, nicht `heute_`, und das UI beschriftet sie mit diesem Datum.
   */
  tag: string | null;
  tag_arbeit_sekunden: number;
  tag_pause_sekunden: number;
  work_day_id: string | null;
  tagesstatus: TagStatus | null;
}

/** Nutzlast von `POST /stempel/start`. */
export interface StempelStart {
  category_id?: string | null;
  service_job_id?: string | null;
  note?: string | null;
  /** Pflicht, wenn der Arbeitstag bereits bestätigt ist (er fällt dann zurück). */
  correction_reason?: string | null;
}

/** Filterliste der Verwaltungssicht: Personalsatz + zugehöriges Login-Konto. */
export interface ZeitMitarbeiter {
  user_id: string;
  employee_id: string;
  name: string;
  employee_number: string;
}

export interface Stundenkonto {
  employee_id: string;
  von: string;
  bis: string;
  soll: string;
  ist: string;
  pause: string;
  abwesend: string;
  saldo: string;
  tage_gesamt: number;
  tage_offen: number;
  tage_eingereicht: number;
  tage_bestaetigt: number;
}

export type Zeitraum = 'heute' | 'woche' | 'monat' | 'jahr';

// ---------------------------------------------------------------------------
// Anzeige
// ---------------------------------------------------------------------------

/** Sekunden → „7:30 h". Ehrlich gerundet auf volle Minuten. */
export function dauerText(sekunden: number | null | undefined): string {
  if (sekunden === null || sekunden === undefined) return '—';
  const minuten = Math.round(sekunden / 60);
  const h = Math.floor(minuten / 60);
  const m = minuten % 60;
  return `${h}:${String(m).padStart(2, '0')} h`;
}

const uhrFmt = new Intl.DateTimeFormat('de-DE', { hour: '2-digit', minute: '2-digit' });
const datumFmt = new Intl.DateTimeFormat('de-DE', {
  weekday: 'short',
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
});

export function uhrzeit(iso: string | null): string {
  return iso ? uhrFmt.format(new Date(iso)) : '—';
}

export function tagText(iso: string): string {
  // Datums-Strings (YYYY-MM-DD) ohne Zeitzone interpretieren, sonst rutscht der
  // Tag je nach Offset.
  const [y, m, d] = iso.split('-').map(Number);
  return datumFmt.format(new Date(y, m - 1, d));
}

/**
 * Status nie NUR über Farbe (WCAG 2.2 AA) — jeder Status trägt Text und
 * Symbol; die Klasse ist reine Zugabe.
 */
export const TAG_STATUS_LABEL: Record<TagStatus, string> = {
  ENTWURF: 'Entwurf',
  EINGEREICHT: 'Eingereicht',
  BESTAETIGT: 'Bestätigt',
  ABGELEHNT: 'Abgelehnt',
};

export const TAG_STATUS_ZEICHEN: Record<TagStatus, string> = {
  ENTWURF: '○',
  EINGEREICHT: '↑',
  BESTAETIGT: '✓',
  ABGELEHNT: '✕',
};

/** Auf die vorhandenen Stempel-Varianten aus `styles.scss` abgebildet. */
export function tagStatusClass(status: TagStatus): string {
  switch (status) {
    case 'BESTAETIGT':
      return 'stamp stamp--positive';
    case 'EINGEREICHT':
      return 'stamp stamp--type';
    case 'ABGELEHNT':
      return 'stamp stamp--negativ';
    default:
      return 'stamp';
  }
}

export const ZUSTAND_LABEL: Record<Zustand, string> = {
  GESTOPPT: 'Nicht gestartet',
  LAEUFT: 'Läuft',
  PAUSE: 'Pause',
};

export const PAUSEN_MODUS_LABEL: Record<PausenModus, string> = {
  KEINE: 'Keine automatische Pause',
  GESETZLICH: 'Gesetzlich (ArbZG § 4)',
  FESTE_ZEITEN: 'Feste Pausenzeiten',
};

/** ISO-String für `datetime-local`-Eingaben (lokale Zeit, ohne Sekunden). */
export function toLocalInput(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`;
}

/** `datetime-local` → ISO mit Zeitzonen-Offset (der Server rechnet in UTC). */
export function fromLocalInput(wert: string): string {
  return new Date(wert).toISOString();
}
