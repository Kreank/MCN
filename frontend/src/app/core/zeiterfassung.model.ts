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

import { vonLokalerEingabe } from '../shared/datum';

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
  /** Σ der wirksamen Ausgleichsbuchungen im Zeitraum (vorzeichenbehaftet). */
  ausgleich: string;
  saldo: string;
  tage_gesamt: number;
  tage_offen: number;
  tage_eingereicht: number;
  tage_bestaetigt: number;
}

// ---------------------------------------------------------------------------
// Stundenausgleich (hr.time_adjustment)
// ---------------------------------------------------------------------------

/**
 * Der Saldo bleibt **abgeleitet**: `Saldo = Ist − Soll + Σ Ausgleich`. Eine
 * Ausgleichsbuchung überschreibt keinen Saldo (es gibt keinen gespeicherten),
 * sie ist die dritte Größe der Formel.
 *
 * `minutes` ist **vorzeichenbehaftet und ganzzahlig**: positiv = Gutschrift aufs
 * Konto, negativ = Belastung. Minuten statt Stunden, weil 20 Minuten in einer
 * Dezimalstunde nicht verlustfrei darstellbar sind.
 */
export type Ausgleichsart = 'EINBEHALT' | 'AUSZAHLUNG' | 'FREIZEITAUSGLEICH' | 'KORREKTUR';

export interface Ausgleich {
  id: string;
  employee_id: string;
  mitarbeiter: string;
  adjustment_type: Ausgleichsart;
  effective_on: string;
  minutes: number;
  stunden: string;
  reason: string;
  status: 'GEBUCHT' | 'STORNIERT';
  reversal_of_id: string | null;
  ist_storno: boolean;
  gebucht_von: string | null;
  created_at: string;
}

export interface AusgleichCreate {
  employee_id: string;
  adjustment_type: Ausgleichsart;
  effective_on: string;
  minutes: number;
  reason: string;
}

export const AUSGLEICHSARTEN: { wert: Ausgleichsart; label: string; hinweis: string }[] = [
  {
    wert: 'EINBEHALT',
    label: 'Einbehalten',
    hinweis: 'Minusstunden werden einbehalten — das Konto steigt (positives Vorzeichen).',
  },
  {
    wert: 'AUSZAHLUNG',
    label: 'Auszahlung',
    hinweis: 'Mehrstunden werden ausgezahlt — das Konto sinkt (negatives Vorzeichen).',
  },
  {
    wert: 'FREIZEITAUSGLEICH',
    label: 'Freizeitausgleich',
    hinweis: 'Mehrstunden werden in Freizeit abgegolten — das Konto sinkt.',
  },
  {
    wert: 'KORREKTUR',
    label: 'Korrektur',
    hinweis: 'Begründete Berichtigung des Kontos (beide Vorzeichen möglich).',
  },
];

export const AUSGLEICHSART_LABEL: Record<Ausgleichsart, string> = {
  EINBEHALT: 'Einbehalten',
  AUSZAHLUNG: 'Auszahlung',
  FREIZEITAUSGLEICH: 'Freizeitausgleich',
  KORREKTUR: 'Korrektur',
};

/** Minuten → „+4:00 h" / „−1:30 h" (Vorzeichen sichtbar, nie nur über Farbe). */
export function ausgleichText(minuten: number): string {
  const vz = minuten < 0 ? '−' : '+';
  const abs = Math.abs(minuten);
  return `${vz}${Math.floor(abs / 60)}:${String(abs % 60).padStart(2, '0')} h`;
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
 * Die ISO-8601-Kalenderwoche eines Tages (`YYYY-MM-DD`) samt Beschriftung.
 *
 * Nach ISO 8601 beginnt die Woche am Montag, und KW 1 ist die Woche, die den
 * 4. Januar enthält. Der naive Weg (Tage seit Jahresanfang durch 7) liegt am
 * Jahreswechsel regelmäßig daneben — genau dort, wo Arbeitszeiten
 * zusammengerechnet werden.
 *
 * Der `id` trägt das ISO-Jahr, nicht das Kalenderjahr: Der 31.12.2025 gehört zu
 * KW 1 von 2026. Ohne diese Unterscheidung fielen zwei verschiedene Wochen in
 * einen Topf.
 */
export function kalenderwoche(iso: string): { id: string; label: string } {
  const [y, m, d] = iso.split('-').map(Number);
  // UTC, damit Sommerzeit-Sprünge die Tagesdifferenz nicht verfälschen.
  const tag = new Date(Date.UTC(y, m - 1, d));
  // Auf den Donnerstag derselben Woche schieben — er liegt immer im ISO-Jahr.
  const wochentag = (tag.getUTCDay() + 6) % 7; // Mo=0 … So=6
  tag.setUTCDate(tag.getUTCDate() - wochentag + 3);
  const isoJahr = tag.getUTCFullYear();
  const ersterDonnerstag = new Date(Date.UTC(isoJahr, 0, 4));
  const versatz = (ersterDonnerstag.getUTCDay() + 6) % 7;
  ersterDonnerstag.setUTCDate(ersterDonnerstag.getUTCDate() - versatz + 3);
  const tage = (tag.getTime() - ersterDonnerstag.getTime()) / 86_400_000;
  const kw = 1 + Math.round(tage / 7);
  return { id: `${isoJahr}-${String(kw).padStart(2, '0')}`, label: `KW ${kw}` };
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

/**
 * `datetime-local` → ISO mit Zeitzonen-Offset (der Server rechnet in UTC).
 *
 * Duenne Huelle um {@link vonLokalerEingabe} — es gibt bewusst nur EINE
 * Umrechnung im Frontend. Der Unterschied ist nur der Vertrag: Hier ist der
 * Zeitpunkt PFLICHT (Zeitbuchungen haben immer Start und Ende), waehrend ein
 * Termin ohne Zeit legitim im Rueckstand liegt.
 *
 * Die Aufrufer setzen den Wert aus zwei Controls zusammen (`${datum}T${von}`).
 * Ist eines davon leer, entsteht ein unparsbarer String wie `"2026-07-21T"`.
 * Frueher lief das in ein nacktes `RangeError: Invalid time value` aus
 * `toISOString()`. Der Fall gehoert durch die Formularvalidierung abgefangen —
 * kommt er trotzdem an, ist das ein Programmierfehler, der laut scheitern soll
 * statt still `null` an den Server zu schicken (der antwortet sonst mit einem
 * 422, das im Formular niemand zuordnen kann).
 */
export function fromLocalInput(wert: string): string {
  const iso = vonLokalerEingabe(wert);
  if (!iso) throw new Error(`Unvollstaendige Zeitangabe: "${wert}"`);
  return iso;
}

/**
 * Der Zeitraum des Stundenkontos: **abgeschlossene** Tage des laufenden Monats,
 * also bis einschließlich gestern.
 *
 * Warum nicht bis heute: Das Sollstunden-Raster zählt den laufenden Tag von
 * 00:00 an voll. Wer morgens um neun nachsieht, hat eine von acht Stunden
 * gebucht und läge damit scheinbar sieben Stunden zurück — jeden Vormittag
 * aufs Neue. Eine Zahl, die regelmäßig falsch aussieht, wird ignoriert.
 *
 * Und erst recht nicht ohne Zeitraum: Der Server nähme dann den Monat bis zum
 * **Monatsletzten** und summierte das Soll aller Zukunftstage gegen ein Ist,
 * das nur bis heute reichen kann — am Monatsersten stünden dort die vollen
 * Minusstunden eines ganzen Monats.
 *
 * Sonderfall Monatserster: „bis gestern" läge vor dem Monatsanfang. Dann ist
 * der Vormonat der richtige Zeitraum; am 1. interessiert ohnehin, wie der
 * abgeschlossene Monat ausgegangen ist.
 */
export function kontoZeitraum(): [string, string] {
  const pad = (n: number) => String(n).padStart(2, '0');
  const iso = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const jetzt = new Date();
  const gestern = new Date(jetzt.getFullYear(), jetzt.getMonth(), jetzt.getDate() - 1);
  const monatsAnfang = new Date(jetzt.getFullYear(), jetzt.getMonth(), 1);
  if (gestern < monatsAnfang) {
    const vormonatAnfang = new Date(jetzt.getFullYear(), jetzt.getMonth() - 1, 1);
    return [iso(vormonatAnfang), iso(gestern)];
  }
  return [iso(monatsAnfang), iso(gestern)];
}

/**
 * Der Saldo mit ausdrücklichem Vorzeichen. „+7,5 h" und „−7,5 h" sind zwei
 * gegenteilige Aussagen; ohne das Pluszeichen liest sich Mehrarbeit wie eine
 * bloße Zahl. Das Minus ist ein echtes Minuszeichen (U+2212).
 */
export function saldoText(wert: string): string {
  const n = Number(wert);
  if (!Number.isFinite(n)) return wert;
  const betrag = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1 }).format(
    Math.abs(n),
  );
  if (n === 0) return '±0 h';
  return `${n > 0 ? '+' : '−'}${betrag} h`;
}

/** Trägt der Saldo Mehrarbeit, Minusstunden oder nichts? */
export function saldoArt(wert: string): 'plus' | 'minus' | 'null' {
  const n = Number(wert);
  if (!Number.isFinite(n) || n === 0) return 'null';
  return n > 0 ? 'plus' : 'minus';
}
