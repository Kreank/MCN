// Vertrag zu /api/planung/einsaetze (workflow.service_job in der DB).
import { PropertyRef, StatusChangeEntry } from './projekt.model';
import { WorkOrderStatus, workOrderStatusLabel } from './auftrag.model';
import { PropertyType } from './property.model';

export type ServiceJobStatus =
  | 'UNGEPLANT'
  | 'GEPLANT'
  | 'BESTAETIGT'
  | 'UNTERWEGS'
  | 'VOR_ORT'
  | 'PAUSIERT'
  | 'ABGESCHLOSSEN'
  | 'NACHARBEIT'
  | 'AUSGEFALLEN';

export interface WorkOrderRef {
  id: string;
  order_number: string;
  title: string;
  status: WorkOrderStatus;
}

/** Farb-Codeliste der Terminkategorie (Token, kein Hex). Das UI mappt jeden
 * Token WCAG-sicher; die Farbe ist stets nur Ergänzung zum Namen (Text). */
export type CategoryColorToken =
  | 'NAVY'
  | 'ORANGE'
  | 'SAGE'
  | 'AMBER'
  | 'TEAL'
  | 'PLUM'
  | 'ROSE'
  | 'SLATE';

export interface CategoryRef {
  id: string;
  name: string;
  color_token: CategoryColorToken;
}

export interface ResourceRef {
  id: string;
  resource_number: string;
  name: string;
  resource_type: ResourceType;
}

export interface ServiceJob {
  id: string;
  job_number: string;
  status: ServiceJobStatus;
  /** Anzeigetitel — der Server löst ihn auf (eigener Titel, sonst Auftragstitel). */
  title: string;
  /** Freier Termin (Begehung/Besichtigung/Beratung ohne Auftrag): work_order ist null. */
  is_free: boolean;
  scheduled_start: string | null;
  scheduled_end: string | null;
  actual_start: string | null;
  actual_end: string | null;
  work_order: WorkOrderRef | null;
  property: PropertyRef | null;
  category: CategoryRef | null;
  assignee_count: number;
}

export interface ServiceJobPage {
  items: ServiceJob[];
  total: number;
  page: number;
  page_size: number;
}

export interface ServiceJobQuery {
  page: number;
  page_size: number;
  q?: string;
  status?: ServiceJobStatus | null;
  work_order_id?: string | null;
  /**
   * Alle Einsätze an einer Liegenschaft (Befund C3) — über BEIDE Wege: der
   * freie Termin hängt direkt am Objekt, der auftragsgebundene über seinen
   * Auftrag. Der Server löst das mit einer Oder-Bedingung auf.
   */
  property_id?: string | null;
  /** Zeitraumfilter der API (ISO-Datetime) — war bisher nicht verdrahtet. */
  scheduled_from?: string | null;
  scheduled_to?: string | null;
}

export interface JobAssignment {
  assignee_id: string;
  display_name: string;
  role: string;
}

/**
 * Antwort auf eine neue Zuweisung: die Zuweisung plus NICHT-blockierende
 * Doppelbelegungs-Hinweise. Die Doppelbelegung ist eine bewusst weiche
 * Invariante (die DB verhindert sie nicht) — das UI muss die Warnung ZEIGEN,
 * die Aktion ist dennoch durchgelaufen.
 */
export interface JobAssignmentResult extends JobAssignment {
  warnings: string[];
}

/** Antwort auf das Umplanen (POST .../schedule) — mit ebensolchen Warnungen. */
export interface ScheduleResult extends ServiceJob {
  warnings: string[];
}

/** Schlanke Zuweisungs-Auswahlliste (GET /api/planung/users): nur id + Name. */
export interface AssignableUser {
  id: string;
  display_name: string;
}

/** Rollen einer Einsatz-Zuweisung (workflow.job_assignment). */
export const ASSIGNMENT_ROLES: { wert: string; label: string }[] = [
  { wert: 'TECHNICIAN', label: 'Techniker' },
  { wert: 'LEAD', label: 'Einsatzleitung' },
];

export interface TimeEntry {
  time_type: string;
  started_at: string;
  // Eine laufende Stempelung hat noch kein Ende (seit der Zeiterfassung).
  ended_at: string | null;
  note: string | null;
  user: string | null;
}

export interface MaterialEntry {
  description: string;
  quantity: string;
  unit: string;
  note: string | null;
}

// --- Schreib-Payloads ------------------------------------------------------
// POST /api/planung/einsaetze
// Ohne work_order_id entsteht ein FREIER TERMIN; dann ist `title` Pflicht.
// Mit work_order_id ist `title` optional (Fallback: Auftragstitel) und
// `property_id` muss die Liegenschaft des Auftrags sein (der Server prüft).
export interface ServiceJobCreate {
  work_order_id?: string | null;
  title?: string | null;
  property_id?: string | null;
  /** Präziser Zielort innerhalb der Liegenschaft (freier Termin). Regeln wie bei
   * `TerminCreate`: `unit_id` setzt `building_id` voraus, `building_id` setzt
   * `property_id` voraus (der Server prüft, 422). */
  building_id?: string | null;
  unit_id?: string | null;
  scheduled_start?: string | null;
  scheduled_end?: string | null;
  on_site_contact_party_id?: string | null;
  access_instructions?: string | null;
  appointment_category_id?: string | null;
}

// PATCH /api/planung/einsaetze/{id} — Teil-Update: nur gesetzte Felder werden
// geändert, ein ausdrückliches null löscht das Feld (Kontakt entfernen). Der
// Auftragsbezug ist bewusst nicht änderbar (in der DB unveränderlich).
export interface ServiceJobUpdate {
  on_site_contact_party_id?: string | null;
  title?: string | null;
  property_id?: string | null;
  access_instructions?: string | null;
}

// POST /api/planung/einsaetze/{id}/schedule
export interface ScheduleInput {
  scheduled_start: string;
  scheduled_end?: string | null;
}

// POST /api/planung/einsaetze/{id}/status
export interface JobStatusInput {
  to_status: ServiceJobStatus;
  reason?: string | null;
}

// POST /api/planung/einsaetze/{id}/assignments
export interface JobAssignmentInput {
  assignee_user_id: string;
  role: string;
}

// POST /api/planung/einsaetze/{id}/times — Zeiten als ISO-Datetime.
export interface TimeLogInput {
  time_type: string;
  started_at: string;
  ended_at: string;
  user_id?: string | null;
  note?: string | null;
}

// POST /api/planung/einsaetze/{id}/materials — Menge als Dezimal-String.
export interface MaterialLogInput {
  description: string;
  quantity: string;
  unit: string;
  note?: string | null;
}

// --- Plantafel-Board -------------------------------------------------------

/**
 * Konfliktart an einer Kachel. Doppelbelegung ist eine bewusst WEICHE Invariante
 * (die DB verhindert sie nicht) — das Board macht sie sichtbar, blockiert aber
 * nichts. `text` ist immer gesetzt: Der Konflikt wird als Text + Symbol gezeigt,
 * nie nur über Farbe (WCAG 1.4.1).
 */
export type KonfliktArt =
  | 'DOPPELBELEGUNG'
  | 'ABWESENHEIT'
  | 'FEIERTAG'
  | 'OFFENES_ENDE'
  /**
   * Fehlender oder abgelaufener Qualifikationsnachweis (Migration 0078).
   *
   * Wie die Doppelbelegung eine **weiche** Invariante: Die Plantafel macht sie
   * sichtbar, die DB verbietet sie nicht. Der Notdienst am Sonntag darf nicht an
   * einem gesperrten Board scheitern — der Disponent entscheidet.
   */
  | 'QUALIFIKATION';

export interface Konflikt {
  kind: KonfliktArt;
  text: string;
}

export type LaneKind = 'USER' | 'RESOURCE';

/**
 * Eine Schwimmbahn: Mitarbeiter oder Betriebsmittel. Der Server liefert ALLE
 * aktiven Bahnen — auch die leeren; sonst könnte man nichts auf eine freie
 * Person ziehen.
 *
 * `target_hours === null` heißt **unbekannt** (kein gültiger Arbeitsvertrag),
 * NICHT „null Stunden Soll". Die Auslastung wird dann als „—" gezeigt, nie als
 * 100 %.
 */
export interface BoardLane {
  kind: LaneKind;
  id: string;
  display_name: string;
  sub: string | null;
  plan_hours: string | null;
  target_hours: string | null;
}

export interface BoardJob {
  id: string;
  job_number: string;
  title: string;
  status: ServiceJobStatus;
  /** Freier Termin ohne Auftrag — die Kachel kennzeichnet ihn als Text. */
  is_free: boolean;
  scheduled_start: string;
  scheduled_end: string | null;
  property_name: string | null;
  /** Kompakte Zieladresse (Straße, Stadt) — „wo ist der Einsatz" auf der Kachel. */
  property_address: string | null;
  category: CategoryRef | null;
  assignee_ids: string[];
  resource_ids: string[];
  conflicts: Konflikt[];
  /**
   * Herkunftsklammer einer Serie (null = Einzeltermin). Jedes Vorkommen ist ein
   * eigenständiger Einsatz — die Klammer dient nur der Anzeige und dem Auffinden
   * der ganzen Reihe.
   */
  series_id: string | null;
}

/** Ein UNGEPLANTER Einsatz aus dem Rückstand — das, was man ins Raster zieht. */
export interface BacklogJob {
  id: string;
  job_number: string;
  title: string;
  status: ServiceJobStatus;
  is_free: boolean;
  property_name: string | null;
  property_address: string | null;
  category: CategoryRef | null;
  order_number: string | null;
}

/**
 * Genehmigte Abwesenheit — Sperrfläche in der Mitarbeiter-Bahn.
 *
 * **Ohne Abwesenheitsart, mit Absicht.** Urlaub von Krankheit zu unterscheiden
 * ist ein Gesundheitsdatum (DSGVO Art. 9); es hängt am `hr`-Recht, die Plantafel
 * an `workflow`. Der Server liefert die Art hier gar nicht erst — das Board zeigt
 * „abwesend, von–bis", mehr braucht die Disposition nicht.
 */
export interface BoardAbsence {
  id: string;
  app_user_id: string;
  start_date: string;
  end_date: string;
}

export interface BoardHoliday {
  holiday_date: string;
  name: string;
}

export interface Plantafel {
  date_from: string;
  date_to: string;
  lanes: BoardLane[];
  jobs: BoardJob[];
  backlog: BacklogJob[];
  backlog_total: number;
  absences: BoardAbsence[];
  holidays: BoardHoliday[];
  unassigned_count: number;
}

/** Abfrage des Boards (Zeitraum + Filter). */
export interface PlantafelQuery {
  date_from: string;
  date_to: string;
  q?: string | null;
  category_id?: string | null;
  backlog_q?: string | null;
}

// POST /api/planung/termine — Termin mit allem in EINEM Vorgang.
export interface TerminCreate {
  work_order_id?: string | null;
  title?: string | null;
  property_id?: string | null;
  /** Präziser Zielort innerhalb der Liegenschaft (freier Termin). Der Server
   * prüft: `unit_id` setzt `building_id` voraus, `building_id` setzt
   * `property_id` voraus; Gebäude gehört zur Liegenschaft, Einheit zum Gebäude. */
  building_id?: string | null;
  unit_id?: string | null;
  scheduled_start?: string | null;
  scheduled_end?: string | null;
  on_site_contact_party_id?: string | null;
  access_instructions?: string | null;
  appointment_category_id?: string | null;
  assignee_ids?: string[];
  resource_ids?: string[];
}

// PATCH /api/planung/termine/{id} — Teil-Update; assignee_ids/resource_ids sind
// eine VOLLERSETZUNG (was fehlt, wird gelöst). Der Auftragsbezug ist bewusst
// nicht änderbar (in der DB unveränderlich).
export interface TerminUpdate {
  title?: string | null;
  property_id?: string | null;
  /** Zielort innerhalb der Liegenschaft ändern/entfernen (freier Termin). Ein
   * ausdrückliches `null` löscht Gebäude bzw. Einheit. Es gelten dieselben
   * Voraussetzungsregeln wie beim Anlegen (`TerminCreate`). */
  building_id?: string | null;
  unit_id?: string | null;
  /** Ausdrückliches `null` legt den Termin ZURÜCK IN DEN RÜCKSTAND (Zeitraum weg,
   * Status GEPLANT → UNGEPLANT). Dieser Wechsel ist begründungspflichtig — dann
   * ist `reason` Pflicht. */
  scheduled_start?: string | null;
  scheduled_end?: string | null;
  on_site_contact_party_id?: string | null;
  access_instructions?: string | null;
  appointment_category_id?: string | null;
  assignee_ids?: string[];
  resource_ids?: string[];
  /** Begründung für den Statuswechsel GEPLANT → UNGEPLANT. */
  reason?: string | null;
}

/** Antwort auf Anlegen/Ändern eines Termins — mit weichen Belegungshinweisen. */
export interface TerminResult extends ServiceJob {
  warnings: string[];
}

// --- Anruf-Durchstich: POST /api/planung/anruf -----------------------------
// Kunde + Ort + Auftrag + Termin aus einem Telefonat, in EINER Transaktion.
// Anders als TerminCreate setzt das keinen bestehenden Auftrag voraus — der
// Auftrag entsteht mit und wird direkt freigegeben (das Telefonat ist der
// Beauftragungsnachweis), damit der Monteur am Termintag losfahren darf.
//
// Zweiter Ausgang: `auftrag.vorlegen`. Freigeben setzt voraus, dass die
// Disposition die Beauftragung fachlich entscheiden KANN — beim Rohrbruch ja,
// bei „Bad komplett sanieren" nicht. Dann endet der Durchstich in
// FREIGABE_AUSSTEHEND statt FREIGEGEBEN, und der Anruf verpufft trotzdem nicht.

/** Der Anrufer. Mit `existing_party_id` wird ein bestehender Kontakt
 * referenziert und NICHT neu angelegt — dann bleiben die Namensfelder leer. */
export interface AnrufPerson {
  existing_party_id?: string | null;
  salutation?: string | null;
  first_name?: string | null;
  last_name?: string | null;
  phone?: string | null;
  email?: string | null;
}

/** Der Ort. Mit `existing_property_id` wird eine bestehende Liegenschaft
 * referenziert (kein Duplikat, keine zweite Adresse). */
export interface AnrufProperty {
  existing_property_id?: string | null;
  /** Bei Neuanlage Pflicht — der Server hat bewusst keinen Default, weil
   * ausgerechnet EINFAMILIENHAUS den Verantwortungsbereich ableitet. */
  property_type?: PropertyType | null;
  name?: string | null;
  street?: string | null;
  house_number?: string | null;
  postal_code?: string | null;
  city?: string | null;
}

export interface AnrufAuftrag {
  title: string;
  description?: string | null;
  priority?: string | null;
  is_emergency?: boolean;
  /** Beim Einfamilienhaus ableitbar, sonst für die Freigabe Pflicht. Im Notfall
   * (`is_emergency`) entbehrlich — dann lässt die DB die Freigabe ohne zu.
   * Beim Vorlegen ebenfalls entbehrlich: Die Tore feuern erst ab FREIGEGEBEN,
   * und wer die Beauftragung nicht beurteilen kann, kann meist auch Sonder- und
   * Gemeinschaftseigentum nicht auseinanderhalten. Der Entscheider ergänzt es. */
  responsibility_scope?: string | null;
  order_evidence_reference?: string | null;
  /** Gewerk. Optional — am Telefon oft noch offen. Der Einsatz erbt es. */
  trade_id?: string | null;
  /** Vorlegen statt freigeben → der Auftrag entsteht in FREIGABE_AUSSTEHEND. */
  vorlegen?: boolean;
  /** Die Frage an den Entscheider. Bei `vorlegen` Pflicht (der Server weist es
   * sonst mit 422 ab); sie wird Begründung des Statuswechsels und ist damit im
   * Statusverlauf der Auftragsmappe nachlesbar. */
  vorlage_frage?: string | null;
}

/** Ohne `scheduled_start` landet der Termin im Rückstand — gewollt. */
export interface AnrufTermin {
  scheduled_start?: string | null;
  scheduled_end?: string | null;
  building_id?: string | null;
  unit_id?: string | null;
  assignee_ids?: string[];
  resource_ids?: string[];
  access_instructions?: string | null;
  appointment_category_id?: string | null;
}

export interface AnrufIn {
  person: AnrufPerson;
  property: AnrufProperty;
  auftrag: AnrufAuftrag;
  termin?: AnrufTermin | null;
}

export interface AnrufResult {
  party_id: string;
  property_id: string;
  work_order_id: string;
  order_number: string;
  order_status: string;
  service_job_id: string;
  job_number: string;
  job_status: string;
  scheduled_start: string | null;
  /** Explizit, damit die Oberfläche keine Statuscodes kennen muss. */
  im_rueckstand: boolean;
}

const KONFLIKT_LABELS: Record<KonfliktArt, string> = {
  DOPPELBELEGUNG: 'Doppelbelegung',
  ABWESENHEIT: 'Abwesenheit',
  FEIERTAG: 'Feiertag',
  OFFENES_ENDE: 'Kein Ende',
  QUALIFIKATION: 'Qualifikation',
};

export function konfliktLabel(k: KonfliktArt): string {
  return KONFLIKT_LABELS[k] ?? k;
}

/** Symbol je Konfliktart (immer ZUSÄTZLICH zum Text, nie statt seiner). */
const KONFLIKT_SYMBOLE: Record<KonfliktArt, string> = {
  DOPPELBELEGUNG: '⚠',
  ABWESENHEIT: '⛱',
  FEIERTAG: '★',
  OFFENES_ENDE: '⧖',
  QUALIFIKATION: '🎓',
};

export function konfliktSymbol(k: KonfliktArt): string {
  return KONFLIKT_SYMBOLE[k] ?? '⚠';
}

export interface ServiceJobDetail extends ServiceJob {
  access_instructions: string | null;
  completion_notes: string | null;
  /** Anzeigename des Vor-Ort-Kontakts. */
  on_site_contact: string | null;
  /** Seine ID — was ein Bearbeiten-Formular braucht, um ihn zu ERHALTEN. */
  on_site_contact_party_id: string | null;
  /** Der EIGENE Titel (darf null sein). `title` ist der aufgelöste Anzeigetitel:
   * beim Auftragstermin der Auftragstitel. Wer den zurückschriebe, brennte ihn in
   * den Einsatz ein — er folgte einer späteren Auftragsumbenennung nicht mehr. */
  own_title: string | null;
  /** Die ROHEN eigenen Ortsangaben — zum Vorbefüllen der Bearbeiten-Dropdowns.
   * IMMER diese IDs nehmen, nie die aufgelösten Labels aus `property.building`/
   * `.unit`: die können beim auftragsgebundenen Termin vom Auftrag GEERBT sein. */
  own_property_id: string | null;
  own_building_id: string | null;
  own_unit_id: string | null;
  created_at: string;
  assignments: JobAssignment[];
  resources: ResourceRef[];
  history: StatusChangeEntry[];
  time_entries: TimeEntry[];
  material_entries: MaterialEntry[];
}

// --- Darstellung (eine Quelle für Liste und Einsatz-Mappe) -----------------
const SERVICE_JOB_STATUS_LABELS: Record<ServiceJobStatus, string> = {
  UNGEPLANT: 'Ungeplant',
  GEPLANT: 'Geplant',
  BESTAETIGT: 'Bestätigt',
  UNTERWEGS: 'Unterwegs',
  VOR_ORT: 'Vor Ort',
  PAUSIERT: 'Pausiert',
  ABGESCHLOSSEN: 'Abgeschlossen',
  NACHARBEIT: 'Nacharbeit',
  AUSGEFALLEN: 'Ausgefallen',
};

export function serviceJobStatusLabel(s: ServiceJobStatus): string {
  return SERVICE_JOB_STATUS_LABELS[s] ?? s;
}

export function serviceJobStatusClass(s: ServiceJobStatus): string {
  if (s === 'ABGESCHLOSSEN') return 'stamp--positive';
  if (s === 'AUSGEFALLEN') return 'stamp--warn';
  return '';
}

// Auch für Verlaufseinträge (String-Status).
export function serviceJobStatusLabelStr(s: string | null): string {
  if (s === null) return 'Anlage';
  return serviceJobStatusLabel(s as ServiceJobStatus);
}

export { workOrderStatusLabel };

const TIME_TYPE_LABELS: Record<string, string> = {
  ARBEITSZEIT: 'Arbeitszeit',
  FAHRTZEIT: 'Fahrtzeit',
  PAUSE: 'Pause',
  BEREITSCHAFT: 'Bereitschaft',
  NACHARBEIT: 'Nacharbeit',
  INTERNE_ZEIT: 'Interne Zeit',
};

export function timeTypeLabel(t: string): string {
  return TIME_TYPE_LABELS[t] ?? t;
}

const ASSIGNMENT_ROLE_LABELS: Record<string, string> = {
  TECHNICIAN: 'Techniker',
  LEAD: 'Einsatzleitung',
};

export function assignmentRoleLabel(r: string): string {
  return ASSIGNMENT_ROLE_LABELS[r] ?? r;
}

// ===========================================================================
// Planungs-Stammdaten: Terminkategorien + Ressourcen
// ===========================================================================

export type CategoryStatus = 'AKTIV' | 'ARCHIVIERT';

export interface AppointmentCategory {
  id: string;
  name: string;
  description: string | null;
  color_token: CategoryColorToken;
  status: CategoryStatus;
  sort_order: number;
  /**
   * Übliche Dauer dieses Termintyps in Minuten (null = keine).
   *
   * **Nur ein Vorschlag:** Der Termin-Dialog belegt daraus das Ende vor. Der
   * Server leitet daraus nie etwas ab, und eine geänderte Kategoriedauer
   * verschiebt keinen bestehenden Termin.
   */
  default_duration_minutes: number | null;
}

export interface CategoryCreate {
  name: string;
  color_token: CategoryColorToken;
  description?: string | null;
  sort_order?: number;
  default_duration_minutes?: number | null;
}

export interface CategoryUpdate {
  name?: string | null;
  color_token?: CategoryColorToken | null;
  description?: string | null;
  sort_order?: number | null;
  /** Weglassen = nicht ändern; ausdrückliches null = keine übliche Dauer mehr. */
  default_duration_minutes?: number | null;
}

/** Wiederholungstakt einer Terminserie (Migration 0077). */
export type SerienIntervall =
  | 'TAEGLICH'
  | 'WOECHENTLICH'
  | 'ZWEIWOECHENTLICH'
  | 'MONATLICH';

export interface SerieCreate {
  intervall: SerienIntervall;
  /** Zahl der ZUSÄTZLICHEN Termine — der Ausgangstermin bleibt der erste. */
  anzahl: number;
  /** Sonntage/Feiertage auf den nächsten Werktag schieben (Takt bleibt gewahrt). */
  werktags?: boolean;
}

/**
 * Ein Vorkommen einer Serie. **Eigener Typ statt `BoardJob`**, weil
 * `scheduled_start` hier null sein darf: Jedes Vorkommen ist ein eigenständiger
 * Einsatz und kann einzeln in den Rückstand zurückgelegt werden — es bleibt
 * trotzdem Teil der Reihe.
 */
export interface SerienTermin {
  id: string;
  job_number: string;
  title: string;
  status: ServiceJobStatus;
  is_free: boolean;
  scheduled_start: string | null;
  scheduled_end: string | null;
  property_name: string | null;
  category: CategoryRef | null;
  series_id: string | null;
}

export interface SerieResult {
  series_id: string;
  erzeugt: SerienTermin[];
  anzahl: number;
  /** Nicht-blockierende Belegungshinweise der NEU angelegten Termine. */
  warnungen: string[];
}

export type ResourceType = 'FAHRZEUG' | 'GERAET' | 'RAUM' | 'SONSTIGE';
export type ResourceStatus = 'AKTIV' | 'INAKTIV' | 'ARCHIVIERT';

export interface Resource {
  id: string;
  resource_number: string;
  name: string;
  resource_type: ResourceType;
  status: ResourceStatus;
  notes: string | null;
}

export interface ResourceCreate {
  name: string;
  resource_type: ResourceType;
  notes?: string | null;
}

export interface ResourceUpdate {
  name?: string | null;
  resource_type?: ResourceType | null;
  notes?: string | null;
}

export interface ResourceAssignResult {
  resource: ResourceRef;
  warnings: string[];
}

// --- Farb-Codeliste (Token -> Anzeige) -------------------------------------
// Jede Kategorie zeigt IMMER ihren Namen als Text; der Farbpunkt ist nur
// dekorative Ergaenzung (WCAG: Status nie nur ueber Farbe). Die CSS-Klasse
// `kat-<token>` faerbt Punkt/Tint (siehe styles.scss / plantafel.scss).
export const CATEGORY_COLORS: { token: CategoryColorToken; label: string }[] = [
  { token: 'NAVY', label: 'Marineblau' },
  { token: 'ORANGE', label: 'Orange' },
  { token: 'SAGE', label: 'Salbeigrün' },
  { token: 'AMBER', label: 'Amber' },
  { token: 'TEAL', label: 'Petrol' },
  { token: 'PLUM', label: 'Pflaume' },
  { token: 'ROSE', label: 'Rosé' },
  { token: 'SLATE', label: 'Schiefer' },
];

export function categoryColorLabel(token: CategoryColorToken): string {
  return CATEGORY_COLORS.find((c) => c.token === token)?.label ?? token;
}

export function categoryColorClass(token: CategoryColorToken): string {
  return `kat-${token.toLowerCase()}`;
}

// --- Ressourcen-Typen/-Status ----------------------------------------------
export const RESOURCE_TYPES: { wert: ResourceType; label: string }[] = [
  { wert: 'FAHRZEUG', label: 'Fahrzeug' },
  { wert: 'GERAET', label: 'Gerät' },
  { wert: 'RAUM', label: 'Raum' },
  { wert: 'SONSTIGE', label: 'Sonstige' },
];

const RESOURCE_TYPE_LABELS: Record<ResourceType, string> = {
  FAHRZEUG: 'Fahrzeug',
  GERAET: 'Gerät',
  RAUM: 'Raum',
  SONSTIGE: 'Sonstige',
};

export function resourceTypeLabel(t: ResourceType): string {
  return RESOURCE_TYPE_LABELS[t] ?? t;
}

const RESOURCE_STATUS_LABELS: Record<ResourceStatus, string> = {
  AKTIV: 'Aktiv',
  INAKTIV: 'Inaktiv',
  ARCHIVIERT: 'Archiviert',
};

export function resourceStatusLabel(s: ResourceStatus): string {
  return RESOURCE_STATUS_LABELS[s] ?? s;
}

/**
 * „Wer ist gerade nicht da" — die Abwesenheitsuebersicht der Disposition.
 *
 * **Ohne Abwesenheitsart, mit voller Absicht** (wie `BoardAbsence`). Die Art
 * unterscheidet Urlaub von Krankheit und ist ein Gesundheitsdatum, besondere
 * Kategorie nach DSGVO Art. 9. Diese Ansicht haengt an `workflow/LESEN` — dem
 * Recht der Disposition, die kein `hr` hat. Sie beantwortet: **wer fehlt, von
 * wann bis wann**. Nicht: warum.
 *
 * Der Server liefert die Art hier gar nicht erst — es gibt also nichts, was ein
 * Client versehentlich anzeigen koennte.
 */
export interface Abwesend {
  id: string;
  app_user_id: string;
  name: string;
  start_date: string;
  end_date: string;
  half_day_start: boolean;
  half_day_end: boolean;
}

// --- Qualifikationen und Zuweisungs-Vorlagen (Migration 0078) ---------------

/**
 * Eine Qualifikation aus dem **frei pflegbaren** Katalog.
 *
 * `kind` ist bewusst ein **freier String**, kein Union-Typ: Der Betrieb legt
 * seine Arten selbst an (GEWERK, ZERTIFIKAT, HERSTELLERSCHULUNG, SICHERHEIT …).
 * Ein Enum im Code verlangte für jede neue Schulungsart einen Deploy — der User
 * hat ausdrücklich um Flexibilität gebeten.
 */
export interface Qualifikation {
  id: string;
  code: string;
  label: string;
  kind: string | null;
  description: string | null;
  /** Verlangt die Zuordnung ein Gültig-bis? (Gasschein ja, Gesellenbrief nein.) */
  expires: boolean;
  active: boolean;
  sort_order: number;
}

export interface QualifikationCreate {
  code: string;
  label: string;
  kind?: string | null;
  description?: string | null;
  expires?: boolean;
  sort_order?: number;
}

export interface QualifikationUpdate {
  label?: string | null;
  kind?: string | null;
  description?: string | null;
  expires?: boolean | null;
  active?: boolean | null;
  sort_order?: number | null;
}

/** Ein Nachweis am Mitarbeiter (Personalakte — `hr`-Recht). */
export interface MitarbeiterQualifikation {
  qualification: Qualifikation;
  valid_from: string | null;
  valid_until: string | null;
  evidence_note: string | null;
  /** Gilt der Nachweis HEUTE? (Der Terminabgleich nutzt den Terminbeginn.) */
  gueltig_heute: boolean;
}

export interface MitarbeiterQualifikationInput {
  qualification_id: string;
  valid_from?: string | null;
  valid_until?: string | null;
  evidence_note?: string | null;
}

/**
 * Zuweisungs-Vorlage: eine benannte Personengruppe als **Vorschlag**.
 *
 * Kein Team-Modell (User-Entscheidung „lose Gruppen, wechselnd") — die Vorlage
 * füllt den Termin-Dialog vor und bindet danach nichts.
 */
export interface Zuweisungsvorlage {
  id: string;
  name: string;
  description: string | null;
  active: boolean;
  sort_order: number;
  members: { app_user_id: string; display_name: string; role: string }[];
}

export interface ZuweisungsvorlageInput {
  name?: string;
  description?: string | null;
  active?: boolean;
  sort_order?: number;
  /** Weglassen = Mitglieder nicht anfassen; [] = alle entfernen. */
  members?: { app_user_id: string; role?: string }[];
}
