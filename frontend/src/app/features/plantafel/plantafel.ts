import { Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { map } from 'rxjs';
import { EinsatzService } from '../../core/einsatz.service';
import { PlanungStammdatenService } from '../../core/planung-stammdaten.service';
import {
  AppointmentCategory,
  BacklogJob,
  BoardAbsence,
  BoardJob,
  BoardLane,
  Konflikt,
  Plantafel as PlantafelData,
  Resource,
  SerienIntervall,
  ServiceJobStatus,
  Zuweisungsvorlage,
  categoryColorClass,
  konfliktLabel,
  konfliktSymbol,
  resourceTypeLabel,
  serviceJobStatusLabel,
} from '../../core/einsatz.model';
import { AuftragService } from '../../core/auftrag.service';
import { PropertyService } from '../../core/property.service';
import { PartyService } from '../../core/party.service';
import { AuthService } from '../../core/auth.service';
import { PlanungNav } from '../planung-nav/planung-nav';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { ReferenzWahl, RefSuche } from '../../shared/formular/referenz-wahl';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: PlantafelData }
  | VerbotenState
  | { kind: 'error' };

/** Zeitraster des Boards. Hero bietet Tag/3 Tage/7 Tage/KW/14 Tage/4 Wochen/Monat;
 * wir bieten dieselben Größen ohne die redundanten Zwischenstufen. */
type Ansicht = 'tag' | 'woche' | 'zwei_wochen' | 'monat';

const ANSICHT_TAGE: Record<Ansicht, number> = {
  tag: 1,
  woche: 7,
  zwei_wochen: 14,
  monat: 28,
};

export const ANSICHTEN: { wert: Ansicht; label: string }[] = [
  { wert: 'tag', label: 'Tag' },
  { wert: 'woche', label: 'Woche' },
  { wert: 'zwei_wochen', label: '2 Wochen' },
  { wert: 'monat', label: '4 Wochen' },
];

/**
 * Das **Zeitband** eines Tages: die Stunden, die das Raster zeigt.
 *
 * 06–20 Uhr ist die Grundeinstellung — sie deckt jede Handwerkerschicht ab. Das
 * Band **wächst aber mit den Daten** (`zeitBand`): Liegt ein Notdiensttermin um
 * 22:30, reicht das Band an diesem Board bis 23 Uhr. Ein Termin wird also NIE an
 * den Rand geklemmt und nie verschluckt — er verschiebt die Skala.
 */
const TAG_VON = 6;
const TAG_BIS = 20;

/**
 * Mindestbreite eines Balkens, als Anteil des Zeitbands.
 *
 * Ein 15-Minuten-Termin wäre in einer Wochenansicht sonst ein Haarstrich und
 * praktisch weder lesbar noch greifbar (Drag&Drop, Tap-Ziel ≥ 24 px, WCAG 2.5.8).
 * Er wird deshalb auf diese Breite **aufgerundet — nach RECHTS**, damit sein
 * Beginn an der richtigen Stelle der Skala steht. Die Uhrzeit steht ohnehin als
 * Text auf der Kachel: Die Skala ist eine Orientierung, keine Messlatte.
 */
const MIN_BALKEN_ANTEIL = 0.08;

/**
 * Dasselbe für die Tagesansicht, wo eine Spalte EINE STUNDE ist: 25 % = 15 min.
 *
 * Bewusst klein. Die 24-px-Zielgröße (WCAG 2.5.8) garantiert ohnehin die
 * CSS-`min-width` der Kachel — verzerrungsfrei und ohne den Beginn zu
 * verschieben. Ein großzügiger Anteil hier bläht dagegen einen 15-Minuten-Termin
 * auf eine Dreiviertelstunde auf und lügt über die Dauer (Review-Fund).
 */
const MIN_ANTEIL_TAG = 0.25;

/**
 * Mindestbreite einer Stunde in der Tages-Spalte (Woche/2W/4W).
 *
 * Der Wert ist ein Kompromiss, und zwar ein bewusster: Bei 1,1 rem ist eine
 * 14-Stunden-Spalte gut 15 rem breit — eine **ganze Woche passt damit auf einen
 * üblichen Bildschirm**, und ein Zwei-Stunden-Termin ist immer noch ~35 px breit
 * (bei 3 rem/Stunde wäre er lesbar, aber man sähe nur noch drei Tage).
 *
 * Es ist eine MINDEST-breite: Ist mehr Platz da, dehnen sich die Spalten
 * (`minmax(…, 1fr)`). Reicht er nicht, scrollt das Board waagerecht — das ist
 * ehrlicher, als die Zeit zu stauchen.
 */
const STUNDE_REM = 1.1;

/** Eine Spalte des Rasters: ein Tag (Wochen-/Monatsansicht) oder eine Stunde
 * (Tagesansicht). Beides verhält sich beim Ziehen, Ablegen und Navigieren gleich. */
type Slot = {
  /** Beginn der Spalte in lokaler Zeit. */
  start: Date;
  /** ISO-Tag der Spalte (für Abwesenheits-/Feiertagsabgleich). */
  dayIso: string;
  kopf: string;
  sub: string;
  isToday: boolean;
  isWeekend: boolean;
  feiertag: string | null;
};

/** Eine Kachel im Raster: der Einsatz plus seine Position (Spalte + Spannweite)
 * und die Zeile innerhalb der Bahn (mehrere gleichzeitige Termine stapeln sich). */
type Balken = {
  job: BoardJob;
  von: number;
  span: number;
  reihe: number;
  /** Ragt der Einsatz über den linken/rechten Rand des Fensters hinaus? */
  offenLinks: boolean;
  offenRechts: boolean;
  /**
   * Position INNERHALB der belegten Spalten, in Prozent ihrer Gesamtbreite.
   *
   * Ohne das füllte ein Termin von 7–9 Uhr die ganze Tagesspalte und sähe aus wie
   * ein Ganztagestermin — und zwei Termine am selben Tag stapelten sich
   * übereinander, statt nebeneinander auf ihrer Uhrzeit zu liegen. Prozentuale
   * Ränder eines Grid-Items lösen sich gegen seine **Grid-Area** auf; die Balken
   * bleiben damit an die Spalten gebunden (Drag&Drop, Tastatur, Sperrflächen
   * rechnen unverändert in Spalten) und liegen trotzdem zeitgenau.
   */
  insetLinks: number;
  insetRechts: number;
  /** Absolute Zeitgrenzen (ms) — für die zeitgenaue Reihen-Packung. */
  startMs: number;
  endeMs: number;
};

/** Abwesenheitsband in einer Mitarbeiter-Bahn (Sperrfläche, nicht anklickbar). */
type Sperre = { absence: BoardAbsence; von: number; span: number };

/** Aufgenommene Kachel — aus dem Raster ODER aus dem Rückstand. */
type Aufnahme =
  | { art: 'board'; job: BoardJob; lane: BoardLane }
  | { art: 'backlog'; job: BacklogJob };

/** Tastatur-Cursor über dem Board. */
type Zielzelle = { laneIdx: number; slotIdx: number };

/** Terminart im Dialog: an einem Auftrag oder frei (ohne Auftrag). */
type TerminArt = 'auftrag' | 'frei';

const STATUS_MOD: Record<ServiceJobStatus, string> = {
  UNGEPLANT: 'neutral',
  GEPLANT: 'plan',
  BESTAETIGT: 'plan',
  UNTERWEGS: 'aktiv',
  VOR_ORT: 'aktiv',
  PAUSIERT: 'pause',
  ABGESCHLOSSEN: 'fertig',
  NACHARBEIT: 'nach',
  AUSGEFALLEN: 'aus',
};

/**
 * Vier-Zeichen-Kurzform des Status für die Kachelmarke.
 *
 * Sie bleibt in JEDER Kachelbreite stehen — auch dort, wo die Container-Query
 * den vollen Statustext ausblendet. Ohne sie bliebe der Status an einer schmalen
 * Kachel allein an der Farbe hängen, und die Projektregel sagt: nie nur Farbe.
 */
const STATUS_KURZ: Record<ServiceJobStatus, string> = {
  UNGEPLANT: 'OFFN',
  GEPLANT: 'PLAN',
  BESTAETIGT: 'BEST',
  UNTERWEGS: 'FAHRT',
  VOR_ORT: 'VORT',
  PAUSIERT: 'PAUS',
  ABGESCHLOSSEN: 'FERT',
  NACHARBEIT: 'NACH',
  AUSGEFALLEN: 'AUSF',
};

function isoVon(d: Date): string {
  const m = `${d.getMonth() + 1}`.padStart(2, '0');
  const t = `${d.getDate()}`.padStart(2, '0');
  return `${d.getFullYear()}-${m}-${t}`;
}

function heuteIso(): string {
  return isoVon(new Date());
}

/** Lokale Mitternacht eines ISO-Tages (NICHT `new Date(iso)` — das läse UTC). */
function tagBeginn(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d, 0, 0, 0, 0);
}

/**
 * Mitternacht (Ortszeit) des Tages, auf den ein Zeitpunkt fällt.
 *
 * NICHT `tagBeginn(d.toISOString())`: Das zerlegt einen ISO-String am
 * Bindestrich und bekäme aus „2026-07-14T05:00:00Z“ zwar Jahr und Monat, aber
 * als Tag „14T05:00:00Z“ → NaN. Und es wäre der UTC-Tag, nicht der lokale.
 */
function tagBeginnVon(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate(), 0, 0, 0, 0);
}

function plusTage(iso: string, n: number): string {
  const d = tagBeginn(iso);
  d.setDate(d.getDate() + n);
  return isoVon(d);
}

/** Montag der Woche, in der `iso` liegt (Wochen-/Mehrwochenansicht rastet ein). */
function montagVon(iso: string): string {
  const d = tagBeginn(iso);
  const versatz = (d.getDay() + 6) % 7; // So=0 → 6
  return plusTage(iso, -versatz);
}

@Component({
  selector: 'app-plantafel',
  imports: [
    RouterLink, PlanungNav, KeinZugriff, ReactiveFormsModule, Dialog, Feld, ReferenzWahl,
  ],
  templateUrl: './plantafel.html',
  styleUrl: './plantafel.scss',
})
export class Plantafel {
  private readonly svc = inject(EinsatzService);
  private readonly stammSvc = inject(PlanungStammdatenService);
  private readonly auftragSvc = inject(AuftragService);
  private readonly propertySvc = inject(PropertyService);
  private readonly partySvc = inject(PartyService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  protected readonly ansichten = ANSICHTEN;
  protected readonly ansicht = signal<Ansicht>('woche');
  protected readonly anker = signal(montagVon(heuteIso()));
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  /** Filter: Suche im Raster, Kategorie, Suche im Rückstand. */
  protected readonly suche = signal('');
  protected readonly katFilter = signal('');
  protected readonly backlogSuche = signal('');
  /** Rückstandsleiste ein-/ausklappbar (auf schmalen Bildschirmen Platz). */
  protected readonly poolOffen = signal(true);

  /** Stammdaten für Dialog und Filter. */
  protected readonly kategorien = signal<AppointmentCategory[]>([]);
  protected readonly ressourcen = signal<Resource[]>([]);

  private readonly dowFmt = new Intl.DateTimeFormat('de-DE', { weekday: 'short' });
  private readonly dayFmt = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
  });
  private readonly langFmt = new Intl.DateTimeFormat('de-DE', {
    weekday: 'long', day: '2-digit', month: 'long', year: 'numeric',
  });
  private readonly timeFmt = new Intl.DateTimeFormat('de-DE', {
    hour: '2-digit',
    minute: '2-digit',
  });

  constructor() {
    this.fetch();
    this.stammSvc.listKategorien().subscribe({
      next: (k) => this.kategorien.set(k),
      error: () => this.kategorien.set([]),
    });
    this.stammSvc.listRessourcen().subscribe({
      next: (r) => this.ressourcen.set(r),
      error: () => this.ressourcen.set([]),
    });
    this.stammSvc.listVorlagen().subscribe({
      next: (v) => this.vorlagen.set(v),
      error: () => this.vorlagen.set([]),
    });
  }

  // =========================================================================
  // Zeitraum & Raster
  // =========================================================================
  protected readonly tage = computed(() => ANSICHT_TAGE[this.ansicht()]);

  /** Erster/letzter Tag des Fensters. */
  protected readonly von = computed(() => this.anker());
  protected readonly bis = computed(() => plusTage(this.anker(), this.tage() - 1));

  /** Feiertage als Nachschlagetabelle (leer, solange `hr.holiday` fehlt). */
  private readonly feiertage = computed<Record<string, string>>(() => {
    const s = this.state();
    if (s.kind !== 'ready') return {};
    const map: Record<string, string> = {};
    for (const f of s.data.holidays) map[f.holiday_date] = f.name;
    return map;
  });

  /**
   * Das Zeitband des Boards (Stunden), abgeleitet aus den sichtbaren Terminen.
   *
   * Grundlage ist die Betriebszeit 06–20 Uhr; ein Termin außerhalb **weitet das
   * Band**, statt an den Rand geklemmt zu werden. Ein Notdienst um 22:30 macht
   * die Skala also bis 23 Uhr auf — für ALLE Spalten gleich, sonst wären die Tage
   * nicht mehr vergleichbar.
   */
  protected readonly zeitBand = computed<{ von: number; bis: number }>(() => {
    const s = this.state();
    let von = TAG_VON;
    let bis = TAG_BIS;
    if (s.kind === 'ready') {
      for (const job of s.data.jobs) {
        const a = new Date(job.scheduled_start);
        von = Math.min(von, a.getHours());
        // Ein Termin OHNE Ende bekommt keine erfundene Dauer — nur sein Beginn
        // zählt fürs Band (die Kachel trägt dafür den Konflikt „Kein Ende").
        if (!job.scheduled_end) continue;
        const e = new Date(job.scheduled_end);
        // Ein Ende exakt um MITTERNACHT gehört noch zum Vortag (24:00) — es macht
        // den Termin nicht mehrtägig. Sonst risse ein gewöhnlicher „bis 24 Uhr"-
        // Einsatz das Band unnötig auf volle 24 Stunden auf.
        const endetUmMitternacht = e.getHours() === 0 && e.getMinutes() === 0;
        const endTag = endetUmMitternacht
          ? tagBeginnVon(new Date(e.getTime() - 1))
          : tagBeginnVon(e);

        if (endTag.getTime() !== tagBeginnVon(a).getTime()) {
          // Der Termin läuft ECHT über Mitternacht (Nachtdienst). Das Band wird
          // bis 24 Uhr geöffnet, damit sein Abendteil sichtbar ist — aber der
          // Anfang bleibt bei 6 Uhr. Ihn ebenfalls auf 0 zu öffnen, machte JEDE
          // Tagesspalte 24 Stunden breit; ein einziger Notdienst halbierte damit
          // die Zahl der Tage, die auf den Schirm passen. Der Preis für den
          // Regelfall wäre zu hoch. Der Nachtteil des Folgetags (00–06 Uhr) liegt
          // dann vor dem Band; der Balken endet sichtbar an der Mitternachtskante,
          // und die volle Spanne steht im Tooltip.
          bis = 24;
          continue;
        }
        const endeStunde = endetUmMitternacht
          ? 24
          : e.getHours() + (e.getMinutes() > 0 ? 1 : 0);
        bis = Math.max(bis, endeStunde);
      }
    }
    return { von: Math.max(0, von), bis: Math.min(24, bis) };
  });

  /**
   * Mindestbreite eines Balkens als Anteil SEINER GRID-AREA.
   *
   * Sie muss zur Spaltenlänge passen: In der Tagesansicht ist eine Spalte eine
   * Stunde (ein Viertelstundentermin wäre 12 px — unter der 24-px-Zielgröße nach
   * WCAG 2.5.8), in den Tages-Spalten das ganze Zeitband.
   */
  private minAnteil(): number {
    return this.ansicht() === 'tag' ? MIN_ANTEIL_TAG : MIN_BALKEN_ANTEIL;
  }

  /** Die Mindestbreite in ECHTER Zeit — die Reihen-Packung muss dieselbe
   *  Geometrie annehmen wie das Rendering, sonst überlappen die Kacheln wieder. */
  private minDauerMs(): number {
    return this.ansicht() === 'tag'
      ? MIN_ANTEIL_TAG * 3_600_000
      : MIN_BALKEN_ANTEIL * this.bandStunden() * 3_600_000;
  }

  /** Stunden im Band — die Zahl der Teilstriche je Tagesspalte. */
  protected readonly bandStunden = computed(() => this.zeitBand().bis - this.zeitBand().von);

  /**
   * Mindestbreite einer Spalte.
   *
   * In den Tages-Spalten (Woche/2W/4W) trägt eine Spalte das ganze Zeitband —
   * sie braucht **Platz je Stunde**, sonst wäre ein Zwei-Stunden-Termin ein
   * Strich und die Skala Dekoration. Passt der Zeitraum damit nicht mehr auf den
   * Schirm, scrollt das Board waagerecht; das ist ehrlicher, als die Zeit zu
   * stauchen. In der Tagesansicht IST eine Spalte eine Stunde — dort genügt die
   * bisherige Mindestbreite.
   */
  protected readonly spaltenbreite = computed(() =>
    this.ansicht() === 'tag'
      ? '3rem'
      : `${(this.bandStunden() * STUNDE_REM).toFixed(1)}rem`,
  );

  /**
   * Die Teilstriche der Zeitskala (Kopfzeile und Zellenraster). Bei vielen
   * Stunden wird ausgedünnt — eine Skala, die man nicht lesen kann, ist Dekoration.
   */
  protected readonly bandTicks = computed<{ stunde: number; label: string; stark: boolean }[]>(
    () => {
      const { von, bis } = this.zeitBand();
      const n = bis - von;
      // In der Tagesansicht IST jede Spalte eine Stunde — dort braucht es keine
      // Unterteilung innerhalb der Spalte.
      if (this.ansicht() === 'tag') return [];
      const schritt = n <= 8 ? 1 : n <= 16 ? 2 : 3;
      const out: { stunde: number; label: string; stark: boolean }[] = [];
      for (let h = von; h < bis; h++) {
        const beschriftet = (h - von) % schritt === 0;
        out.push({
          stunde: h,
          label: beschriftet ? `${h}` : '',
          stark: beschriftet,
        });
      }
      return out;
    },
  );

  /**
   * Die Spalten des Rasters. In der Tagesansicht sind es STUNDEN, sonst TAGE —
   * für alles Weitere (Ziehen, Ablegen, Tastatur) ist eine Spalte eine Spalte.
   */
  protected readonly slots = computed<Slot[]>(() => {
    const heute = heuteIso();
    const feiertage = this.feiertage();
    if (this.ansicht() === 'tag') {
      const iso = this.anker();
      const basis = tagBeginn(iso);
      const { von, bis } = this.zeitBand();
      return Array.from({ length: bis - von }, (_, i) => {
        const start = new Date(basis);
        start.setHours(von + i);
        return {
          start,
          dayIso: iso,
          kopf: `${`${von + i}`.padStart(2, '0')}:00`,
          sub: '',
          isToday: iso === heute,
          isWeekend: [0, 6].includes(basis.getDay()),
          feiertag: feiertage[iso] ?? null,
        };
      });
    }
    return Array.from({ length: this.tage() }, (_, i) => {
      const iso = plusTage(this.anker(), i);
      const d = tagBeginn(iso);
      return {
        start: d,
        dayIso: iso,
        kopf: this.dowFmt.format(d),
        sub: this.dayFmt.format(d),
        isToday: iso === heute,
        isWeekend: [0, 6].includes(d.getDay()),
        feiertag: feiertage[iso] ?? null,
      };
    });
  });

  /** Länge einer Spalte in Millisekunden (1 h bzw. 1 Tag). */
  private slotMs(): number {
    return this.ansicht() === 'tag' ? 3_600_000 : 86_400_000;
  }

  /** Beginn der ersten und Ende der letzten Spalte (lokale Zeit). */
  private fenster(): { start: Date; ende: Date } {
    const s = this.slots();
    const start = s[0].start;
    const ende = new Date(s[s.length - 1].start.getTime() + this.slotMs());
    return { start, ende };
  }

  protected readonly zeitraumLabel = computed(() => {
    if (this.ansicht() === 'tag') return this.langFmt.format(tagBeginn(this.anker()));
    return `${this.dayFmt.format(tagBeginn(this.von()))} – ${this.dayFmt.format(
      tagBeginn(this.bis()),
    )}`;
  });

  ansichtWaehlen(a: string): void {
    const wert = a as Ansicht;
    this.ansicht.set(wert);
    // Wochen- und Mehrwochenraster rasten auf Montag ein; die Tagesansicht bleibt
    // auf dem gewählten Tag stehen.
    this.anker.set(wert === 'tag' ? this.anker() : montagVon(this.anker()));
    this.fetch();
  }

  prev(): void {
    this.anker.set(plusTage(this.anker(), -this.tage()));
    this.fetch();
  }
  next(): void {
    this.anker.set(plusTage(this.anker(), this.tage()));
    this.fetch();
  }
  heute(): void {
    this.anker.set(
      this.ansicht() === 'tag' ? heuteIso() : montagVon(heuteIso()),
    );
    this.fetch();
  }
  datumWaehlen(wert: string): void {
    if (!wert) return;
    this.anker.set(this.ansicht() === 'tag' ? wert : montagVon(wert));
    this.fetch();
  }
  retry(): void {
    this.fetch();
  }

  // =========================================================================
  // Bahnen, Balken, Sperrflächen
  // =========================================================================
  protected readonly lanes = computed<BoardLane[]>(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data.lanes : [];
  });

  protected readonly mitarbeiterBahnen = computed(() =>
    this.lanes().filter((l) => l.kind === 'USER'),
  );
  protected readonly ressourcenBahnen = computed(() =>
    this.lanes().filter((l) => l.kind === 'RESOURCE'),
  );

  protected readonly backlog = computed<BacklogJob[]>(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data.backlog : [];
  });
  protected readonly backlogTotal = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data.backlog_total : 0;
  });

  /** Anzahl Kacheln mit mindestens einem Konflikt (Kopfzeile des Boards). */
  protected readonly konfliktZahl = computed(() => {
    const s = this.state();
    if (s.kind !== 'ready') return 0;
    return s.data.jobs.filter((j) => j.conflicts.length > 0).length;
  });

  /**
   * Spalten-Spanne eines Zeitraums im aktuellen Raster — die eigentliche Antwort
   * auf „mehrtägige Einsätze werden nur am Starttag gerendert".
   *
   * Ein Einsatz, der links oder rechts über das Fenster hinausragt, wird am Rand
   * geklemmt und trägt eine Fortsetzungsmarke (‹ / ›). Ein Einsatz ganz außerhalb
   * liefert null. Ein Einsatz OHNE Ende belegt genau eine Spalte — geraten wird
   * keine Dauer (die Kachel trägt dafür den Konflikt „Kein Ende").
   */
  private spanne(
    startIso: string,
    endIso: string | null,
  ): Omit<Balken, 'job' | 'reihe'> | null {
    const slots = this.slots();
    const { start: fVon, ende: fBis } = this.fenster();
    const ms = this.slotMs();
    const s = new Date(startIso);
    const e = endIso ? new Date(endIso) : null;
    if (s >= fBis) return null;
    if (e && e <= fVon) return null;
    if (!e && s < fVon) return null;

    const roh = Math.floor((s.getTime() - fVon.getTime()) / ms);
    const von = Math.max(0, roh);
    let bis: number; // exklusiver Spaltenindex
    if (!e) {
      bis = von + 1;
    } else {
      // Halb-offenes Intervall: ein Ende exakt auf einer Spaltengrenze belegt die
      // Folgespalte NICHT (sonst wäre jeder Termin bis 24:00 zweitägig).
      const rohBis = Math.ceil((e.getTime() - fVon.getTime()) / ms);
      bis = Math.min(slots.length, Math.max(von + 1, rohBis));
    }
    if (bis <= 0 || von >= slots.length) return null;
    const span = Math.max(1, bis - von);

    const offenLinks = roh < 0;
    const offenRechts = !!e && e.getTime() > fBis.getTime();
    const [insetLinks, insetRechts] = this.insets(
      s, e, von, span, offenLinks, offenRechts,
    );
    return {
      von,
      span,
      offenLinks,
      offenRechts,
      insetLinks,
      insetRechts,
      startMs: s.getTime(),
      // Ohne Ende gibt es keine Dauer — für die Reihen-Packung zählt dann nur der
      // Beginn (ein erfundenes Ende wäre eine Behauptung über die Arbeitszeit).
      endeMs: e ? e.getTime() : s.getTime(),
    };
  }

  /**
   * Die zeitgenaue Position eines Balkens INNERHALB seiner Spalten, in Prozent.
   *
   * In der Tagesansicht ist eine Spalte bereits eine Stunde — dort liegt der
   * Balken exakt auf der Minute. In den Tages-Spalten (Woche/2W/4W) bildet das
   * **Zeitband** (z. B. 06–20 Uhr) die Zellenbreite ab: Ein Termin von 7–9 Uhr
   * beginnt bei einem Vierzehntel und ist zwei Vierzehntel breit — statt die
   * ganze Zelle zu füllen und wie ein Ganztagestermin auszusehen.
   *
   * Ein Termin außerhalb des Bandes kann nicht auftreten: Das Band wächst mit den
   * Daten (`zeitBand`).
   */
  private insets(
    s: Date,
    e: Date | null,
    von: number,
    span: number,
    offenLinks: boolean,
    offenRechts: boolean,
  ): [number, number] {
    const slots = this.slots();
    if (this.ansicht() === 'tag') {
      // Stunden-Spalten: der Anteil bezieht sich auf die Stunde selbst.
      const ms = this.slotMs();
      const spaltenStart = slots[von].start.getTime();
      const links = Math.max(0, (s.getTime() - spaltenStart) / ms);
      const rechtsKante = slots[von + span - 1].start.getTime() + ms;
      const rechts = e ? Math.max(0, (rechtsKante - e.getTime()) / ms) : 0;
      return this.klemmen(links / span, rechts / span, span);
    }

    const { von: bandVon, bis: bandBis } = this.zeitBand();

    /**
     * Anteil des Zeitbands, an dem `d` innerhalb der Spalte `spalte` liegt.
     *
     * **Gegen den Tag DER SPALTE gerechnet, nicht gegen den Tag von `d`.** Sonst
     * (Review-Fund, reproduziert):
     * - stünde ein Termin, der von Sonntag 22:00 in den Montag hineinragt, bei
     *   88 % der MONTAGSSPALTE — also abends statt an der linken Kante;
     * - kollabierte ein Termin, der um Mitternacht endet, auf die Mindestbreite,
     *   weil Mitternacht schon zum FOLGETAG gehört (ein 16-Stunden-Termin als
     *   25-Pixel-Stummel).
     *
     * Bandgrenzen werden als LOKALE Datumswerte gebaut, nicht per
     * Millisekunden-Addition: An den Umstellungstagen hat ein Tag 23 bzw. 25
     * Stunden, und jeder Balken stünde sonst eine Stunde falsch (dieselbe
     * Wanduhr-Invariante wie im Backend).
     */
    const anteilIn = (spalte: number, d: Date): number => {
      const tag = slots[spalte].start;
      const y = tag.getFullYear();
      const m = tag.getMonth();
      const t = tag.getDate();
      const bandStart = new Date(y, m, t, bandVon, 0, 0, 0).getTime();
      // `bandBis === 24` läuft sauber auf 00:00 des Folgetags über.
      const bandEnde = new Date(y, m, t, bandBis, 0, 0, 0).getTime();
      const len = bandEnde - bandStart;
      if (len <= 0) return 0;
      return Math.min(1, Math.max(0, (d.getTime() - bandStart) / len));
    };

    // Geklemmte Ränder liegen AUF der Fensterkante — dort gibt es keinen Anteil
    // zu rechnen (die Fortsetzungsmarke ‹ / › sagt, dass es weitergeht).
    const links = offenLinks ? 0 : anteilIn(von, s);
    let rechts: number;
    if (!e) {
      // Ohne Ende: eine schmale Marke am Beginn, KEINE geratene Dauer.
      rechts = Math.max(0, 1 - links - this.minAnteil());
    } else if (offenRechts) {
      rechts = 0;
    } else {
      rechts = 1 - anteilIn(von + span - 1, e);
    }
    return this.klemmen(links / span, Math.max(0, rechts) / span, span);
  }

  /**
   * Ränder so beschneiden, dass der Balken sichtbar und greifbar bleibt.
   *
   * Ein 15-Minuten-Termin wäre sonst ein Haarstrich — nicht lesbar, nicht
   * anfassbar (WCAG 2.5.8 verlangt 24 px Zielgröße). Er wächst deshalb nach
   * RECHTS über seine Zeit hinaus; sein BEGINN bleibt an der richtigen Stelle,
   * und die Uhrzeit steht als Text auf der Kachel. Die Skala ist eine
   * Orientierung, keine Messlatte — das ist bewusst so.
   */
  private klemmen(links: number, rechts: number, span: number): [number, number] {
    let l = Math.min(Math.max(links, 0), 1);
    let r = Math.min(Math.max(rechts, 0), 1);
    // Die Ränder sind Anteile der GRID-AREA (also `span` Spalten breit), die
    // Mindestbreite meint aber EINE Spalte — sonst bliese ein Balken über zwei
    // Spalten auf die doppelte Mindestbreite auf und schöbe dabei seinen Beginn
    // nach links (Review-Fund: ein 30-Minuten-Termin wurde in der Tagesansicht
    // als 08:30–10:00 gezeichnet). Die Reihen-Packung rechnet mit derselben
    // Dauer (`minDauerMs`), damit beide dieselbe Geometrie annehmen.
    const min = this.minAnteil() / span;
    if (1 - l - r < min) {
      // Zuerst nach rechts wachsen — der BEGINN soll an der richtigen Stelle der
      // Skala bleiben.
      r = Math.max(0, 1 - l - min);
      // Am rechten Bandrand (z. B. 19:45–20:00) ist rechts schon 0; dann muss der
      // Balken nach LINKS wachsen, sonst bliebe er ein 6-Pixel-Strich und wäre
      // weder lesbar noch greifbar (WCAG 2.5.8).
      if (1 - l - r < min) l = Math.max(0, 1 - r - min);
    }
    return [l * 100, r * 100];
  }

  /**
   * Balken und Sperrflächen ALLER Bahnen — EINMAL berechnet, nicht je Bahn.
   *
   * Vorher waren `balken()`/`reihen()` gewöhnliche Methoden, die das Template je
   * Bahn aufrief. Bei 40 Bahnen × 300 Terminen sind das ~24.000 Durchläufe pro
   * Change-Detection-Zyklus — und der feuert bei JEDER Mausbewegung während des
   * Ziehens (`dragUeber` schreibt ein Signal). Als `computed` läuft die Rechnung
   * nur, wenn sich Daten, Fenster oder Ansicht wirklich ändern; das Template
   * schlägt danach bloß noch in einer Map nach.
   */
  private readonly bahnKey = (lane: BoardLane) => `${lane.kind}|${lane.id}`;

  private readonly balkenNachBahn = computed<Map<string, Balken[]>>(() => {
    const map = new Map<string, Balken[]>();
    const s = this.state();
    if (s.kind !== 'ready') return map;

    for (const lane of s.data.lanes) map.set(this.bahnKey(lane), []);
    for (const job of s.data.jobs) {
      const pos = this.spanne(job.scheduled_start, job.scheduled_end);
      if (!pos) continue;
      const bahnen = [
        ...job.assignee_ids.map((id) => `USER|${id}`),
        ...job.resource_ids.map((id) => `RESOURCE|${id}`),
      ];
      for (const key of bahnen) {
        // Eine Bahn, die der Server nicht liefert, gibt es im Board nicht —
        // wir erfinden sie hier nicht (sonst zeigte das Raster eine Kachel ohne
        // Kopfzeile).
        map.get(key)?.push({ job, reihe: 0, ...pos });
      }
    }

    // Greedy-Packung je Bahn: die erste Zeile, in der der Balken mit keinem schon
    // platzierten kollidiert. Ohne das lägen gleichzeitige Termine übereinander
    // und einer wäre schlicht unsichtbar — bei einer Doppelbelegung genau der
    // Fall, den man sehen MUSS.
    //
    // Die Kollision wird über die ZEIT geprüft, nicht über die Spalten: Zwei
    // Termine am selben Tag (8–10 und 13–15 Uhr) belegen dieselbe Tagesspalte,
    // überschneiden sich aber nicht — sie gehören NEBENEINANDER auf ihre Uhrzeit,
    // nicht untereinander. Nur so zeigt eine Bahn, was ein Monteur an einem Tag
    // wirklich zu tun hat.
    // **Die Packung muss dieselbe Geometrie annehmen wie das Rendering.**
    // `klemmen()` zieht kurze Termine auf eine Mindestbreite auf (sonst wären sie
    // Haarstriche). Rechnete die Packung mit der ECHTEN Dauer, hielte sie einen
    // 8:00–8:30-Termin und einen 9:00–10:00-Termin für überschneidungsfrei — der
    // erste würde aber bis 9:07 gezeichnet und läge über dem zweiten. Genau der
    // Fall, den die Packung verhindern soll (Review-Fund).
    const minMs = this.minDauerMs();
    for (const balken of map.values()) {
      balken.sort((a, b) => a.startMs - b.startMs || b.endeMs - a.endeMs);
      const belegt: { von: number; bis: number }[][] = [];
      for (const b of balken) {
        const bis = Math.max(b.endeMs, b.startMs + minMs);
        let reihe = 0;
        while (belegt[reihe]?.some((x) => b.startMs < x.bis && x.von < bis)) reihe++;
        belegt[reihe] ??= [];
        belegt[reihe].push({ von: b.startMs, bis });
        b.reihe = reihe;
      }
    }
    return map;
  });

  private readonly sperrenNachBahn = computed<Map<string, Sperre[]>>(() => {
    const map = new Map<string, Sperre[]>();
    const s = this.state();
    if (s.kind !== 'ready') return map;
    for (const a of s.data.absences) {
      // Abwesenheiten sind TAGE; das Ende ist einschließlich → +1 Tag exklusiv.
      const pos = this.spanne(
        tagBeginn(a.start_date).toISOString(),
        tagBeginn(plusTage(a.end_date, 1)).toISOString(),
      );
      if (!pos) continue;
      const key = `USER|${a.app_user_id}`;
      const liste = map.get(key) ?? [];
      liste.push({ absence: a, von: pos.von, span: pos.span });
      map.set(key, liste);
    }
    return map;
  });

  /** Alle Balken einer Bahn, überschneidungsfrei auf Zeilen verteilt. */
  balken(lane: BoardLane): Balken[] {
    return this.balkenNachBahn().get(this.bahnKey(lane)) ?? [];
  }

  /** Zeilen einer Bahn (mindestens 1, damit die Zelle klickbar bleibt). */
  reihen(lane: BoardLane): number {
    const b = this.balken(lane);
    return Math.max(1, ...b.map((x) => x.reihe + 1));
  }

  /** Genehmigte Abwesenheiten als Sperrfläche der Mitarbeiter-Bahn. */
  sperren(lane: BoardLane): Sperre[] {
    if (lane.kind !== 'USER') return [];
    return this.sperrenNachBahn().get(this.bahnKey(lane)) ?? [];
  }

  // =========================================================================
  // Auslastung (Stufe 1)
  // =========================================================================
  /** Auslastung in Prozent — oder null, wenn kein Sollwert bekannt ist.
   * `null` heißt **unbekannt**, nicht 0: ein Mitarbeiter ohne Arbeitsvertrag
   * darf nicht als maximal überlastet erscheinen. */
  auslastung(lane: BoardLane): number | null {
    // Auf `null` prüfen, NICHT auf Falsy: Der Server liefert Dezimalzahlen als
    // String, „0.00" ist wahrheitswertig — die alte Prüfung funktionierte nur
    // aus Versehen und kippte bei jeder Formatänderung.
    if (lane.kind !== 'USER' || lane.target_hours == null || lane.plan_hours == null) {
      return null;
    }
    const soll = Number(lane.target_hours);
    // Soll 0 (z. B. ganze Woche abwesend) → die Auslastung ist gegenstandslos,
    // nicht „unendlich überlastet".
    if (!soll) return null;
    return Math.round((Number(lane.plan_hours) / soll) * 100);
  }
  auslastungText(lane: BoardLane): string {
    const p = this.auslastung(lane);
    const plan =
      lane.plan_hours == null ? '0,0' : Number(lane.plan_hours).toFixed(1).replace('.', ',');
    if (p === null) {
      const soll = lane.target_hours == null ? null : Number(lane.target_hours);
      if (soll === 0) {
        return `${plan} h geplant · kein Soll im Zeitraum (abwesend oder Feiertage)`;
      }
      return `${plan} h geplant · Sollstunden unbekannt (kein Vertrag)`;
    }
    const soll = Number(lane.target_hours).toFixed(1).replace('.', ',');
    return `${plan} von ${soll} h · ${p} % ausgelastet`;
  }
  /** Modifier der Auslastungsleiste (Text steht immer daneben — nie nur Farbe). */
  auslastungMod(lane: BoardLane): string {
    const p = this.auslastung(lane);
    if (p === null) return 'unbekannt';
    if (p > 100) return 'ueber';
    if (p >= 80) return 'voll';
    return 'ok';
  }

  // =========================================================================
  // Rechte
  // =========================================================================
  protected readonly darfPlanen = computed(
    () => this.auth.darf('workflow', 'ANLEGEN') && this.auth.darf('workflow', 'AENDERN'),
  );
  protected readonly darfUmplanen = computed(() => this.auth.darf('workflow', 'AENDERN'));

  // =========================================================================
  // Verschieben: Maus (HTML5-Drag&Drop) UND Tastatur/Touch — ein Automat
  // =========================================================================
  protected readonly griff = signal<Aufnahme | null>(null);
  protected readonly zieht = signal<Aufnahme | null>(null);
  protected readonly zielzelle = signal<Zielzelle | null>(null);
  protected readonly dropZiel = signal<string | null>(null);
  protected readonly busy = signal(false);
  protected readonly fehler = signal<string | null>(null);
  protected readonly warnungen = signal<string[]>([]);
  protected readonly ansage = signal('');
  private griffAusloeser: HTMLElement | null = null;

  protected readonly quelle = computed<Aufnahme | null>(() => this.griff() ?? this.zieht());

  /**
   * In welche Bahnen darf die aufgenommene Kachel?
   *
   * Aus dem RÜCKSTAND in jede Bahn (der Einsatz hat noch gar keine Zuordnung).
   * Aus dem RASTER nur zwischen Bahnen derselben Art: Ein Mitarbeiter gegen ein
   * Fahrzeug zu tauschen wäre keine Umplanung, sondern Datenverlust — ein Einsatz
   * trägt beides nebeneinander. Solche Änderungen laufen über den Termin-Dialog.
   */
  private bahnErlaubt(q: Aufnahme, lane: BoardLane): boolean {
    if (q.art === 'backlog') return true;
    return q.lane.kind === lane.kind;
  }

  protected readonly zielBahnen = computed<number[]>(() => {
    const q = this.quelle();
    if (!q) return [];
    return this.lanes()
      .map((lane, i) => ({ lane, i }))
      .filter((x) => this.bahnErlaubt(q, x.lane))
      .map((x) => x.i);
  });

  istErlaubtesZiel(laneIdx: number): boolean {
    const q = this.quelle();
    const lane = this.lanes()[laneIdx];
    return !!q && !!lane && this.bahnErlaubt(q, lane);
  }

  istZiel(laneIdx: number, slotIdx: number): boolean {
    const z = this.zielzelle();
    if (z && z.laneIdx === laneIdx && z.slotIdx === slotIdx) return true;
    return this.dropZiel() === `${laneIdx}|${slotIdx}`;
  }

  istInBewegung(jobId: string): boolean {
    return this.quelle()?.job.id === jobId;
  }

  // --- Maus ----------------------------------------------------------------
  dragStart(ev: DragEvent, q: Aufnahme): void {
    if (!this.darfUmplanen() || this.busy()) {
      ev.preventDefault();
      return;
    }
    this.griff.set(null);
    this.zieht.set(q);
    this.hinweiseSchliessen();
    if (ev.dataTransfer) {
      ev.dataTransfer.effectAllowed = 'move';
      ev.dataTransfer.setData('text/plain', q.job.job_number);
    }
  }

  dragEnde(): void {
    this.zieht.set(null);
    this.dropZiel.set(null);
  }

  dragUeber(ev: DragEvent, laneIdx: number, slotIdx: number): void {
    if (!this.zieht() || !this.istErlaubtesZiel(laneIdx)) return;
    ev.preventDefault(); // macht die Zelle erst zum Drop-Ziel
    if (ev.dataTransfer) ev.dataTransfer.dropEffect = 'move';
    this.dropZiel.set(`${laneIdx}|${slotIdx}`);
  }

  dragVerlassen(laneIdx: number, slotIdx: number): void {
    if (this.dropZiel() === `${laneIdx}|${slotIdx}`) this.dropZiel.set(null);
  }

  drop(ev: DragEvent, laneIdx: number, slotIdx: number): void {
    ev.preventDefault();
    const q = this.zieht();
    this.dragEnde();
    if (q) this.ablegen(q, laneIdx, slotIdx);
  }

  // --- Tastatur & Touch ----------------------------------------------------
  aufnehmen(q: Aufnahme, laneIdx: number, slotIdx: number, ev?: Event): void {
    if (!this.darfUmplanen() || this.busy()) return;
    this.griffAusloeser = (ev?.currentTarget as HTMLElement) ?? null;
    this.hinweiseSchliessen();
    this.griff.set(q);
    const bahnen = this.zielBahnen();
    const lane = bahnen.includes(laneIdx) ? laneIdx : (bahnen[0] ?? 0);
    this.zielzelle.set({ laneIdx: lane, slotIdx });
    this.sagen(
      `Einsatz ${q.job.job_number}, ${q.job.title}, aufgenommen. ` +
        'Pfeiltasten bewegen das Ziel, Enter legt ab, Escape bricht ab.',
    );
    this.zielFokussieren();
  }

  /** Aus dem Rückstand aufnehmen: Ziel ist zunächst die erste Bahn, erste Spalte. */
  ausPoolAufnehmen(job: BacklogJob, ev?: Event): void {
    this.aufnehmen({ art: 'backlog', job }, 0, 0, ev);
  }

  abbrechen(): void {
    const q = this.griff();
    const ausloeser = this.griffAusloeser;
    this.griff.set(null);
    this.zielzelle.set(null);
    this.griffAusloeser = null;
    if (!q) return;
    this.sagen(`Verschieben abgebrochen. Einsatz ${q.job.job_number} bleibt, wo er war.`);
    // Fokus zurück auf GENAU den Auslöser (ein Einsatz mit mehreren Zuweisungen
    // erscheint in jeder Bahn — eine DOM-ID wäre mehrdeutig).
    setTimeout(() => {
      if (ausloeser?.isConnected) ausloeser.focus();
    });
  }

  boardTaste(ev: KeyboardEvent): void {
    const q = this.griff();
    const z = this.zielzelle();
    if (!q || !z) return;
    const bahnen = this.zielBahnen();
    const pos = bahnen.indexOf(z.laneIdx);
    const letzte = this.slots().length - 1;
    let neu: Zielzelle | null = null;
    switch (ev.key) {
      case 'ArrowLeft':
        neu = { ...z, slotIdx: Math.max(0, z.slotIdx - 1) };
        break;
      case 'ArrowRight':
        neu = { ...z, slotIdx: Math.min(letzte, z.slotIdx + 1) };
        break;
      case 'ArrowUp':
        if (pos > 0) neu = { ...z, laneIdx: bahnen[pos - 1] };
        break;
      case 'ArrowDown':
        if (pos >= 0 && pos < bahnen.length - 1) neu = { ...z, laneIdx: bahnen[pos + 1] };
        break;
      case 'Home':
        neu = { ...z, slotIdx: 0 };
        break;
      case 'End':
        neu = { ...z, slotIdx: letzte };
        break;
      case 'Enter':
      case ' ':
      case 'Spacebar': {
        ev.preventDefault();
        this.griff.set(null);
        this.zielzelle.set(null);
        this.ablegen(q, z.laneIdx, z.slotIdx);
        return;
      }
      case 'Escape':
        ev.preventDefault();
        this.abbrechen();
        return;
      default:
        return;
    }
    ev.preventDefault();
    if (!neu) return;
    this.zielzelle.set(neu);
    const lane = this.lanes()[neu.laneIdx];
    const slot = this.slots()[neu.slotIdx];
    this.sagen(`Ziel: ${slot.kopf} ${slot.sub}, Bahn ${lane.display_name}.`);
    this.zielFokussieren();
  }

  private zielFokussieren(): void {
    const z = this.zielzelle();
    if (!z) return;
    setTimeout(() => document.getElementById(`drop-${z.laneIdx}-${z.slotIdx}`)?.focus());
  }

  zielAnklicken(laneIdx: number, slotIdx: number): void {
    const q = this.griff();
    if (!q) return;
    this.griff.set(null);
    this.zielzelle.set(null);
    this.ablegen(q, laneIdx, slotIdx);
  }

  // =========================================================================
  // Der eigentliche Umzug — EIN atomarer Serveraufruf
  // =========================================================================
  // Früher waren das bis zu drei Rufe hintereinander (schedule + assign +
  // unassign). Schlug der zweite fehl, war der erste geschrieben — der Nutzer
  // bekam einen Teilzustand. `PATCH /planung/termine/{id}` klammert alles in EINE
  // Transaktion: entweder der Umzug steht ganz, oder gar nicht.

  private neueZeit(job: BoardJob | BacklogJob, slotIdx: number): {
    start: string;
    end: string | null;
  } {
    const slot = this.slots()[slotIdx];
    const start = new Date(slot.start);
    const alt = 'scheduled_start' in job && job.scheduled_start ? new Date(job.scheduled_start) : null;

    if (this.ansicht() === 'tag') {
      // Stundenraster: die Spalte IST die Uhrzeit; die Minuten bleiben erhalten.
      if (alt) start.setMinutes(alt.getMinutes(), 0, 0);
    } else if (alt) {
      // Tagesraster: der Tag wechselt, die Uhrzeit bleibt.
      start.setHours(alt.getHours(), alt.getMinutes(), 0, 0);
    } else {
      // Aus dem Rückstand ohne bisherige Uhrzeit: Arbeitsbeginn.
      start.setHours(TAG_VON + 2, 0, 0, 0);
    }

    let end: string | null = null;
    const altEnd = 'scheduled_end' in job ? job.scheduled_end : null;
    if (alt && altEnd) {
      // Dauer bleibt erhalten.
      end = new Date(start.getTime() + (new Date(altEnd).getTime() - alt.getTime())).toISOString();
    }
    // Kein Ende bekannt → es wird KEINES erfunden. Die Kachel trägt dann den
    // Konflikt „Kein Ende gepflegt" und fordert zur Korrektur auf.
    return { start: start.toISOString(), end };
  }

  private ablegen(q: Aufnahme, laneIdx: number, slotIdx: number): void {
    if (this.busy()) return;
    this.griffAusloeser = null;
    this.hinweiseSchliessen();
    const ziel = this.lanes()[laneIdx];
    const slot = this.slots()[slotIdx];
    if (!ziel || !slot) return;
    if (!this.bahnErlaubt(q, ziel)) {
      this.melden(
        'Ein Einsatz wechselt nur zwischen Bahnen derselben Art. ' +
          'Mitarbeiter und Betriebsmittel änderst du im Termin-Dialog.',
      );
      return;
    }

    const zeit = this.neueZeit(q.job, slotIdx);
    const zielText = `${slot.kopf} ${slot.sub}, Bahn ${ziel.display_name}`;

    // Soll-Zustand der Zuordnungen (Vollersetzung — der Server löst, was fehlt).
    let assignees: string[];
    let ressourcen: string[];
    if (q.art === 'backlog') {
      assignees = ziel.kind === 'USER' ? [ziel.id] : [];
      ressourcen = ziel.kind === 'RESOURCE' ? [ziel.id] : [];
    } else {
      assignees = [...q.job.assignee_ids];
      ressourcen = [...q.job.resource_ids];
      if (ziel.kind === 'USER') {
        assignees = assignees.filter((id) => id !== q.lane.id);
        if (!assignees.includes(ziel.id)) assignees.push(ziel.id);
      } else {
        ressourcen = ressourcen.filter((id) => id !== q.lane.id);
        if (!ressourcen.includes(ziel.id)) ressourcen.push(ziel.id);
      }
      const gleicheZeit =
        q.job.scheduled_start === zeit.start && (q.job.scheduled_end ?? null) === zeit.end;
      const gleicheBahn = q.lane.id === ziel.id;
      if (gleicheZeit && gleicheBahn) {
        this.sagen('Abgelegt, wo der Einsatz schon war — nichts geändert.');
        return;
      }
    }

    this.busy.set(true);
    this.sagen(`Einsatz ${q.job.job_number} wird verschoben …`);
    this.svc
      .updateTermin(q.job.id, {
        scheduled_start: zeit.start,
        scheduled_end: zeit.end,
        assignee_ids: assignees,
        resource_ids: ressourcen,
      })
      .subscribe({
        next: (res) => {
          this.busy.set(false);
          this.warnungen.set(res.warnings ?? []);
          const kern = `Einsatz ${q.job.job_number} abgelegt auf ${zielText}.`;
          this.sagen(
            res.warnings?.length
              ? `${kern} ${res.warnings.length} Hinweis(e): ${res.warnings.join(' ')}`
              : kern,
          );
          this.refresh();
        },
        error: (err) => {
          this.busy.set(false);
          const status = (err as { status?: number })?.status;
          const detail = fehlerDetail(err);
          const kopf =
            status === 403
              ? 'Keine Berechtigung zum Umplanen.'
              : status === 422
                ? 'Der Server hat die Umplanung abgelehnt.'
                : 'Die Umplanung ist fehlgeschlagen.';
          // Der Server schreibt den Umzug in EINER Transaktion — schlägt er fehl,
          // ist NICHTS geschrieben. Diese Aussage ist deshalb belastbar.
          this.melden(
            `${kopf}${detail ? ' ' + detail : ''} Der Einsatz ${q.job.job_number} steht ` +
              'unverändert an seinem alten Platz.',
          );
          this.refresh();
        },
      });
  }

  // =========================================================================
  // Termin anlegen / bearbeiten (ein Dialog, ein Serveraufruf)
  // =========================================================================
  protected readonly dialogOffen = signal(false);
  protected readonly dialogBusy = signal(false);
  /** Der Dialog holt beim Bearbeiten erst die gespeicherten Werte (siehe
   * `dialogOeffnen`). Solange bleibt „Übernehmen" gesperrt — ein Speichern mit
   * halb geladenem Formular würde Felder löschen, die nie angezeigt wurden. */
  protected readonly dialogLaedt = signal(false);
  protected readonly dialogFehler = signal<string | null>(null);
  /** Bearbeiteter Termin (null = neu anlegen). */
  protected readonly bearbeitet = signal<BoardJob | BacklogJob | null>(null);
  protected readonly art = signal<TerminArt>('auftrag');
  /** Mehrfachauswahl im Dialog (Hero: „Mitarbeiter und Ressourcen zuweisen"). */
  protected readonly gewaehlteMitarbeiter = signal<string[]>([]);
  protected readonly gewaehlteRessourcen = signal<string[]>([]);
  /** Beim Bearbeiten: der Auftrag ist unveränderlich (DB-Trigger WF-01). */
  protected readonly auftragGesperrt = signal(false);

  /**
   * Der bereits hinterlegte Vor-Ort-Kontakt, als NAME.
   *
   * `app-referenz-wahl` hält die ID im Control (die wird also korrekt
   * zurückgeschrieben), zeigt aber nur einen Chip für eine Auswahl, die der
   * Nutzer selbst getroffen hat — ein von außen gesetzter Wert bliebe unsichtbar.
   * Das Feld sähe leer aus, obwohl ein Kontakt gesetzt IST: Der Disponent würde
   * glauben, es sei keiner hinterlegt. Deshalb steht der gespeicherte Name hier
   * als Text daneben (und lässt sich gezielt entfernen).
   */
  protected readonly aktuellerKontakt = signal<string | null>(null);
  private geladeneKontaktId: string | null = null;

  /** Nur zeigen, solange der geladene Kontakt auch der gewählte ist — sonst
   * behauptete der Text einen Kontakt, den der Nutzer gerade ersetzt hat. */
  zeigtAktuellenKontakt(): boolean {
    return (
      !!this.aktuellerKontakt() &&
      this.form.controls.on_site_contact_party_id.value === this.geladeneKontaktId
    );
  }

  kontaktEntfernen(): void {
    this.form.controls.on_site_contact_party_id.setValue('');
    this.aktuellerKontakt.set(null);
    this.geladeneKontaktId = null;
  }

  protected readonly form = this.fb.group({
    work_order_id: this.fb.control('', { nonNullable: true }),
    title: this.fb.control('', { nonNullable: true }),
    property_id: this.fb.control('', { nonNullable: true }),
    appointment_category_id: this.fb.control('', { nonNullable: true }),
    on_site_contact_party_id: this.fb.control('', { nonNullable: true }),
    access_instructions: this.fb.control('', { nonNullable: true }),
    start_datum: this.fb.control('', { nonNullable: true }),
    start_zeit: this.fb.control('08:00', { nonNullable: true }),
    end_datum: this.fb.control('', { nonNullable: true }),
    end_zeit: this.fb.control('', { nonNullable: true }),
    /** Nur im Rückstand-Modus: Begründung für GEPLANT → UNGEPLANT. */
    rueckstand_grund: this.fb.control('', { nonNullable: true }),
  });

  protected readonly katOptionen = computed(() => [
    { wert: '', label: 'Ohne Kategorie' },
    ...this.kategorien().map((k) => ({ wert: k.id, label: k.name })),
  ]);

  /**
   * Ende aus der üblichen Dauer der gewählten Kategorie vorbelegen (Migration
   * 0077) — der häufigste Handgriff der Disposition.
   *
   * **Vorschlag, keine Vorschrift.** Zwei Fälle, bewusst verschieden:
   *
   * - **Neuer Termin:** Das Ende ist nur eine Voreinstellung des Dialogs
   *   (Start + 2 h). Die Kategoriedauer darf sie ersetzen — sonst griffe die
   *   Dauer genau dort nie, wo sie gebraucht wird. Hat der Bediener das Ende
   *   selbst angefasst (`dirty` — Angular setzt das nur bei echter Eingabe,
   *   nicht bei `setValue`), bleibt es stehen.
   * - **Bestehender Termin:** Das Ende ist ein ZUGESAGTER Zeitraum. Es wird nur
   *   gefüllt, wenn es leer ist — eine bloße Kategorieänderung darf einen
   *   vereinbarten Termin nicht still verschieben.
   */
  kategorieGewaehlt(): void {
    const dauer = this.gewaehlteKatDauer();
    if (!dauer) return;

    const c = this.form.controls;
    if (!c.start_datum.value || !c.start_zeit.value) return;

    const neu = this.bearbeitet() === null;
    const hatEnde = !!c.end_datum.value || !!c.end_zeit.value;
    const angefasst = c.end_datum.dirty || c.end_zeit.dirty;
    if (angefasst) return;
    if (!neu && hatEnde) return;

    const startIso = this.zuIso(c.start_datum.value, c.start_zeit.value);
    if (!startIso) return;
    const ende = new Date(new Date(startIso).getTime() + dauer * 60_000);
    c.end_datum.setValue(isoVon(ende));
    c.end_zeit.setValue(this.hhmm(ende));
  }

  // ---- Zuweisungs-Vorlagen (lose Gruppen, Migration 0078) -----------------
  /**
   * Eine benannte Personengruppe auf Knopfdruck in den Dialog übernehmen.
   *
   * **Ein Vorschlag, keine Bindung** (User-Entscheidung „lose Gruppen,
   * wechselnd"): Die Mitglieder werden zur Auswahl HINZUGEFÜGT, bestehende
   * bleiben stehen, und danach lässt sich jeder einzeln wieder abwählen. Es
   * entsteht kein Team-Objekt am Termin — nur gewöhnliche Einzelzuweisungen.
   */
  protected readonly vorlagen = signal<Zuweisungsvorlage[]>([]);

  vorlageUebernehmen(v: Zuweisungsvorlage): void {
    const vorhanden = new Set(this.gewaehlteMitarbeiter());
    // Nur Mitglieder übernehmen, die es als Board-Bahn noch gibt: Wurde jemand
    // seit dem Anlegen der Vorlage deaktiviert, hat er keine Checkbox mehr — er
    // ließe sich danach nicht wieder abwählen. Und die Übersprungenen werden
    // BENANNT, nicht verschwiegen.
    const bahnen = new Set(this.mitarbeiterBahnen().map((l) => l.id));
    const kandidaten = v.members.map((m) => m.app_user_id);
    const neu = kandidaten.filter((id) => bahnen.has(id) && !vorhanden.has(id));
    const entfallen = kandidaten.filter((id) => !bahnen.has(id)).length;

    if (!neu.length) {
      this.ansage.set(
        entfallen
          ? `Aus „${v.name}“ ist niemand einplanbar (${entfallen} Mitglied(er) sind ` +
              'nicht mehr aktiv).'
          : `Alle Mitglieder von „${v.name}“ sind bereits gewählt.`,
      );
      return;
    }
    this.gewaehlteMitarbeiter.set([...this.gewaehlteMitarbeiter(), ...neu]);
    this.ansage.set(
      `${neu.length} Mitarbeiter aus „${v.name}“ übernommen.` +
        (entfallen
          ? ` ${entfallen} Mitglied(er) sind nicht mehr aktiv und wurden übergangen.`
          : '') +
        ' Die Auswahl lässt sich weiter ändern — die Vorlage bindet nichts.',
    );
  }

  /** Die übliche Dauer der aktuell gewählten Kategorie (für den Dialoghinweis). */
  gewaehlteKatDauer(): number | null {
    const katId = this.form.controls.appointment_category_id.value;
    return this.kategorien().find((k) => k.id === katId)?.default_duration_minutes ?? null;
  }

  /**
   * Die Zeitspanne eines Termins als Text — für Tooltip und `aria-label`.
   *
   * Sie MUSS dort stehen: Bei schmalen Kacheln blendet die Container-Query die
   * Endzeit (und bei sehr kurzen Terminen auch die Anfangszeit) aus. Was hier
   * fehlte, wäre dann nirgends mehr abrufbar — weder mit der Maus noch mit dem
   * Screenreader.
   */
  spanneText(job: BoardJob): string {
    const von = this.zeit(job.scheduled_start);
    if (!job.scheduled_end) return `ab ${von} (kein Ende gepflegt)`;
    return `${von}–${this.zeit(job.scheduled_end)}`;
  }

  /** Minuten menschenlesbar: 90 → „1 h 30 min". */
  dauerText(minuten: number): string {
    const h = Math.floor(minuten / 60);
    const m = minuten % 60;
    if (h === 0) return `${m} min`;
    return m === 0 ? `${h} h` : `${h} h ${m} min`;
  }

  protected readonly auftragSuche: RefSuche = (q) =>
    this.auftragSvc
      .list({ page: 1, page_size: 20, q })
      .pipe(map((p) => p.items.map((o) => ({ id: o.id, label: o.title, sub: o.order_number }))));

  protected readonly liegenschaftSuche: RefSuche = (q) =>
    this.propertySvc.list({ page: 1, page_size: 20, q }).pipe(
      map((p) =>
        p.items.map((x) => ({ id: x.id, label: x.name, sub: `${x.property_number} · ${x.city}` })),
      ),
    );

  protected readonly kontaktSuche: RefSuche = (q) =>
    this.partySvc
      .list({ page: 1, page_size: 20, q })
      .pipe(map((p) => p.items.map((x) => ({ id: x.id, label: x.display_name }))));

  artWaehlen(art: TerminArt): void {
    if (this.auftragGesperrt() || this.art() === art) return;
    this.art.set(art);
    this.pflichtfelderSetzen();
  }

  private pflichtfelderSetzen(): void {
    const auftrag = this.form.controls.work_order_id;
    const titel = this.form.controls.title;
    if (this.art() === 'auftrag') {
      auftrag.setValidators([Validators.required]);
      titel.clearValidators();
    } else {
      auftrag.clearValidators();
      auftrag.setValue('');
      titel.setValidators([Validators.required]);
    }
    auftrag.updateValueAndValidity();
    titel.updateValueAndValidity();
  }

  /** „+ Neuer Termin" (Kopfleiste) oder Klick in eine leere Zelle. */
  neuerTermin(laneIdx?: number, slotIdx?: number): void {
    if (!this.darfPlanen()) return;
    const slot = slotIdx !== undefined ? this.slots()[slotIdx] : this.slots()[0];
    const lane = laneIdx !== undefined ? this.lanes()[laneIdx] : null;
    const stunde =
      this.ansicht() === 'tag' ? slot.start.getHours() : TAG_VON + 2;
    this.bearbeitet.set(null);
    this.auftragGesperrt.set(false);
    this.art.set('auftrag');
    this.rueckstandModus.set(false);
    this.form.reset({
      work_order_id: '', title: '', property_id: '', appointment_category_id: '',
      on_site_contact_party_id: '', access_instructions: '', rueckstand_grund: '',
      start_datum: slot.dayIso,
      start_zeit: `${`${stunde}`.padStart(2, '0')}:00`,
      end_datum: slot.dayIso,
      end_zeit: `${`${Math.min(stunde + 2, 23)}`.padStart(2, '0')}:00`,
    });
    this.form.controls.rueckstand_grund.clearValidators();
    this.form.controls.rueckstand_grund.updateValueAndValidity();
    this.gewaehlteMitarbeiter.set(lane?.kind === 'USER' ? [lane.id] : []);
    this.gewaehlteRessourcen.set(lane?.kind === 'RESOURCE' ? [lane.id] : []);
    this.aktuellerKontakt.set(null);
    this.geladeneKontaktId = null;
    this.pflichtfelderSetzen();
    this.dialogFehler.set(null);
    // Ein neuer Termin hat nichts nachzuladen.
    this.dialogLaedt.set(false);
    this.dialogOffen.set(true);
  }

  /**
   * Kachel/Rückstands-Eintrag bearbeiten.
   *
   * **Der Dialog lädt den Einsatz nach, bevor er ihn zeigt.** `BoardJob`/
   * `BacklogJob` sind Kacheldaten: Sie tragen weder den Vor-Ort-Kontakt noch die
   * Zutrittshinweise, und ihr `title` ist der AUFGELÖSTE Titel (beim
   * Auftragstermin der Auftragstitel). Würde das Formular sie blind aus der Kachel
   * füllen, schriebe der PATCH beim Speichern genau das zurück:
   *
   * * Ansprechpartner und Zutrittscode („Schlüssel im Kasten, Code 1234") wären
   *   nach einer bloßen Uhrzeitänderung **gelöscht** — der Monteur stünde ohne
   *   Code vor der Tür;
   * * der Auftragstitel wäre in den Einsatz eingebrannt und folgte einer späteren
   *   Auftragsumbenennung nicht mehr.
   *
   * Deshalb: `GET /planung/einsaetze/{id}` liefert `own_title` und
   * `on_site_contact_party_id` — die ROHEN Werte, die der PATCH zurückschreiben
   * darf.
   */
  private dialogOeffnen(
    job: BoardJob | BacklogJob,
    zeiten: { start_datum: string; start_zeit: string; end_datum: string; end_zeit: string },
  ): void {
    this.bearbeitet.set(job);
    // Der Auftragsbezug ist in der DB unveränderlich (WF-01) — das Feld wird
    // deshalb gar nicht erst angeboten. Kein „freien Termin hochstufen" durch die
    // Hintertür.
    this.auftragGesperrt.set(true);
    this.art.set(job.is_free ? 'frei' : 'auftrag');
    this.rueckstandModus.set(false);
    this.form.reset({
      work_order_id: '',
      title: '',
      property_id: '',
      appointment_category_id: job.category?.id ?? '',
      on_site_contact_party_id: '',
      access_instructions: '',
      rueckstand_grund: '',
      ...zeiten,
    });
    this.form.controls.work_order_id.clearValidators();
    this.form.controls.title.clearValidators();
    this.form.controls.work_order_id.updateValueAndValidity();
    this.form.controls.title.updateValueAndValidity();
    this.gewaehlteMitarbeiter.set([]);
    this.gewaehlteRessourcen.set([]);
    this.aktuellerKontakt.set(null);
    this.geladeneKontaktId = null;
    this.dialogFehler.set(null);
    this.dialogLaedt.set(true);
    this.dialogOffen.set(true);

    this.svc.get(job.id).subscribe({
      next: (d) => {
        // Ein zwischenzeitlich anderer Termin im Dialog gewinnt (schnelles Klicken).
        if (this.bearbeitet()?.id !== job.id) return;
        this.dialogLaedt.set(false);
        this.form.patchValue({
          title: d.own_title ?? '',
          appointment_category_id: d.category?.id ?? '',
          on_site_contact_party_id: d.on_site_contact_party_id ?? '',
          access_instructions: d.access_instructions ?? '',
        });
        this.geladeneKontaktId = d.on_site_contact_party_id ?? null;
        this.aktuellerKontakt.set(d.on_site_contact);
        this.gewaehlteMitarbeiter.set(d.assignments.map((a) => a.assignee_id));
        this.gewaehlteRessourcen.set(d.resources.map((r) => r.id));
      },
      error: () => {
        if (this.bearbeitet()?.id !== job.id) return;
        this.dialogLaedt.set(false);
        // Nicht mit halben Daten weiterarbeiten lassen: Speichern bliebe gesperrt,
        // sonst überschriebe der PATCH Felder, die nie geladen wurden.
        this.dialogFehler.set(
          'Der Termin ließ sich nicht laden. Bitte schließe den Dialog und ' +
            'versuche es erneut — ohne die gespeicherten Angaben kann nicht ' +
            'gespeichert werden, ohne sie zu verlieren.',
        );
      },
    });
  }

  /** Kachel bearbeiten (Hero: Doppelklick/Bearbeiten in der Detailansicht). */
  bearbeiten(job: BoardJob): void {
    if (!this.darfUmplanen()) return;
    const start = new Date(job.scheduled_start);
    const end = job.scheduled_end ? new Date(job.scheduled_end) : null;
    this.dialogOeffnen(job, {
      start_datum: isoVon(start),
      start_zeit: this.hhmm(start),
      end_datum: end ? isoVon(end) : '',
      end_zeit: end ? this.hhmm(end) : '',
    });
  }

  /** Rückstands-Eintrag terminieren (öffnet den Dialog mit Datum). */
  poolPlanen(job: BacklogJob): void {
    if (!this.darfUmplanen()) return;
    const slot = this.slots()[0];
    this.dialogOeffnen(job, {
      start_datum: slot.dayIso,
      start_zeit: '08:00',
      end_datum: slot.dayIso,
      end_zeit: '16:00',
    });
  }

  /**
   * Rückweg ins Backlog: der Termin verliert seinen Zeitraum und geht zurück in
   * den Rückstand. Der Statuswechsel GEPLANT → UNGEPLANT ist
   * begründungspflichtig — deshalb fragt der Dialog den Grund ab, statt den
   * Vorgang still scheitern zu lassen.
   */
  protected readonly rueckstandModus = signal(false);

  rueckstandUmschalten(): void {
    const an = !this.rueckstandModus();
    this.rueckstandModus.set(an);
    const grund = this.form.controls.rueckstand_grund;
    if (an) {
      grund.setValidators([Validators.required]);
    } else {
      grund.clearValidators();
      grund.setValue('');
    }
    grund.updateValueAndValidity();
    this.dialogFehler.set(null);
  }

  /** Kachel aufs Rückstandsfeld gezogen → Dialog im Rückstand-Modus. */
  poolDragUeber(ev: DragEvent): void {
    const q = this.zieht();
    if (!q || q.art !== 'board') return;
    ev.preventDefault();
    if (ev.dataTransfer) ev.dataTransfer.dropEffect = 'move';
    this.poolDropZiel.set(true);
  }
  poolDragVerlassen(): void {
    this.poolDropZiel.set(false);
  }
  poolDrop(ev: DragEvent): void {
    ev.preventDefault();
    const q = this.zieht();
    this.dragEnde();
    this.poolDropZiel.set(false);
    if (!q || q.art !== 'board') return;
    this.zurueckInDenRueckstand(q.job);
  }
  protected readonly poolDropZiel = signal(false);

  /** Öffnet den Dialog gleich im Rückstand-Modus (Begründung wird abgefragt). */
  zurueckInDenRueckstand(job: BoardJob): void {
    if (!this.darfUmplanen()) return;
    this.bearbeiten(job);
    this.rueckstandUmschalten();
  }

  dialogSchliessen(): void {
    if (this.dialogBusy()) return;
    this.dialogOffen.set(false);
  }

  mitarbeiterUmschalten(id: string): void {
    const liste = this.gewaehlteMitarbeiter();
    this.gewaehlteMitarbeiter.set(
      liste.includes(id) ? liste.filter((x) => x !== id) : [...liste, id],
    );
  }
  ressourceUmschalten(id: string): void {
    const liste = this.gewaehlteRessourcen();
    this.gewaehlteRessourcen.set(
      liste.includes(id) ? liste.filter((x) => x !== id) : [...liste, id],
    );
  }
  istGewaehlt(liste: string[], id: string): boolean {
    return liste.includes(id);
  }

  // ===================== Serientermine (Migration 0077) =====================
  /**
   * Einen geplanten Termin wiederholen. Es entstehen **echte, eigenständige
   * Folgetermine** (kein virtuelles Vorkommen einer Regel): Jeder hat eigene
   * Nummer und eigenen Status, ein abgesagter Dienstag macht den Mittwoch nicht
   * kaputt. Mitarbeiter, Ressourcen, Kategorie und Dauer werden mitkopiert.
   */
  protected readonly serieOffen = signal(false);
  protected readonly serieBusy = signal(false);
  protected readonly serieFehler = signal<string | null>(null);
  protected readonly serieZiel = signal<BoardJob | null>(null);
  protected readonly serieForm = this.fb.group({
    intervall: this.fb.control<SerienIntervall>('WOECHENTLICH', { nonNullable: true }),
    anzahl: this.fb.control(3, {
      nonNullable: true,
      validators: [Validators.required, Validators.min(1), Validators.max(52)],
    }),
    werktags: this.fb.control(true, { nonNullable: true }),
  });
  protected readonly serieIntervalle: FeldOption[] = [
    { wert: 'TAEGLICH', label: 'täglich' },
    { wert: 'WOECHENTLICH', label: 'wöchentlich' },
    { wert: 'ZWEIWOECHENTLICH', label: 'alle zwei Wochen' },
    { wert: 'MONATLICH', label: 'monatlich' },
  ];

  /**
   * Der Termin im Dialog, wenn er sich wiederholen lässt — sonst null.
   *
   * Nur ein bereits GEPLANTER Termin (mit Beginn) kann eine Serie tragen: ohne
   * Raster gibt es nichts zu takten. Ein Rückstands-Eintrag wird erst geplant,
   * dann wiederholt.
   */
  protected readonly wiederholbar = computed<BoardJob | null>(() => {
    const job = this.bearbeitet();
    if (!job || this.rueckstandModus() || !this.darfPlanen()) return null;
    return 'scheduled_start' in job && job.scheduled_start ? (job as BoardJob) : null;
  });

  serieOeffnen(job: BoardJob): void {
    if (!this.darfPlanen()) return;
    this.serieZiel.set(job);
    this.serieForm.reset({ intervall: 'WOECHENTLICH', anzahl: 3, werktags: true });
    this.serieFehler.set(null);
    this.serieOffen.set(true);
  }

  serieSchliessen(): void {
    if (!this.serieBusy()) this.serieOffen.set(false);
  }

  serieAnlegen(): void {
    const job = this.serieZiel();
    if (!job || this.serieBusy()) return;
    this.serieForm.markAllAsTouched();
    if (this.serieForm.invalid) return;
    const v = this.serieForm.getRawValue();
    this.serieBusy.set(true);
    this.serieFehler.set(null);
    this.svc
      .serieAnlegen(job.id, {
        intervall: v.intervall,
        anzahl: Number(v.anzahl),
        werktags: v.werktags,
      })
      .subscribe({
        next: (r) => {
          this.serieBusy.set(false);
          this.serieOffen.set(false);
          this.dialogOffen.set(false);
          // Die Warnungen der neuen Termine gehören AUSGESPROCHEN: Sie liegen in
          // einer Zukunft, die der Disponent gerade nicht auf dem Board sieht
          // (Doppelbelegung, Abwesenheit, Feiertag). Blockieren tun sie nicht.
          const hinweise = r.warnungen.length
            ? ` Hinweise: ${r.warnungen.join(' · ')}`
            : '';
          this.ansage.set(
            `${r.anzahl} Folgetermin${r.anzahl === 1 ? '' : 'e'} angelegt. ` +
              'Jeder ist ein eigener Einsatz und lässt sich einzeln umplanen.' +
              hinweise,
          );
          this.laden(true);
        },
        error: (err) => {
          this.serieBusy.set(false);
          this.serieFehler.set(fehlerDetail(err) ?? 'Die Serie ließ sich nicht anlegen.');
        },
      });
  }

  private hhmm(d: Date): string {
    return `${`${d.getHours()}`.padStart(2, '0')}:${`${d.getMinutes()}`.padStart(2, '0')}`;
  }

  private zuIso(datum: string, zeit: string): string | null {
    if (!datum || !zeit) return null;
    const [y, m, d] = datum.split('-').map(Number);
    const [hh, mm] = zeit.split(':').map(Number);
    return new Date(y, m - 1, d, hh, mm, 0, 0).toISOString();
  }

  speichern(): void {
    // Solange die gespeicherten Werte nicht geladen sind, kennt das Formular sie
    // nicht — es dürfte sie also auch nicht zurückschreiben.
    if (this.dialogBusy() || this.dialogLaedt()) return;
    this.form.markAllAsTouched();
    if (this.form.invalid) return;
    const v = this.form.getRawValue();
    const rueckstand = this.rueckstandModus();
    const start = rueckstand ? null : this.zuIso(v.start_datum, v.start_zeit);
    const end = rueckstand ? null : this.zuIso(v.end_datum || v.start_datum, v.end_zeit);
    if (start && end && end <= start) {
      this.dialogFehler.set('Das Ende muss nach dem Beginn liegen.');
      return;
    }
    this.dialogBusy.set(true);
    this.dialogFehler.set(null);

    const ziel = this.bearbeitet();
    const gemeinsam = {
      title: v.title.trim() || null,
      appointment_category_id: v.appointment_category_id || null,
      scheduled_start: start,
      scheduled_end: end,
      assignee_ids: this.gewaehlteMitarbeiter(),
      resource_ids: this.gewaehlteRessourcen(),
      on_site_contact_party_id: v.on_site_contact_party_id || null,
      access_instructions: v.access_instructions.trim() || null,
    };

    const ruf = ziel
      ? this.svc.updateTermin(ziel.id, {
          ...gemeinsam,
          // `scheduled_start: null` heißt für den Server „zurück in den
          // Rückstand" — der Statuswechsel dorthin ist begründungspflichtig.
          ...(rueckstand ? { reason: v.rueckstand_grund.trim() } : {}),
        })
      : this.svc.createTermin({
          ...gemeinsam,
          work_order_id: this.art() === 'auftrag' ? v.work_order_id : null,
          property_id: this.art() === 'frei' ? v.property_id || null : null,
        });

    ruf.subscribe({
      next: (res) => {
        this.dialogBusy.set(false);
        this.dialogOffen.set(false);
        this.warnungen.set(res.warnings ?? []);
        this.sagen(
          rueckstand
            ? `Termin ${res.job_number} liegt wieder im Rückstand.`
            : ziel
              ? `Termin ${res.job_number} gespeichert.`
              : `Termin ${res.job_number} angelegt.`,
        );
        this.refresh();
      },
      error: (err) => {
        this.dialogBusy.set(false);
        // Der Server schreibt in EINER Transaktion — bei einem Fehler ist nichts
        // geschrieben. Deshalb hier keine „vielleicht halb angelegt"-Warnung mehr.
        this.dialogFehler.set(
          fehlerDetail(err) ?? 'Der Termin konnte nicht gespeichert werden.',
        );
      },
    });
  }

  // =========================================================================
  // Filter, Laden, Darstellung
  // =========================================================================
  sucheSetzen(wert: string): void {
    this.suche.set(wert);
    this.fetch();
  }
  katFilterSetzen(wert: string): void {
    this.katFilter.set(wert);
    this.fetch();
  }
  backlogSucheSetzen(wert: string): void {
    this.backlogSuche.set(wert);
    this.fetch();
  }
  filterZuruecksetzen(): void {
    this.suche.set('');
    this.katFilter.set('');
    this.backlogSuche.set('');
    this.fetch();
  }
  protected readonly filterAktiv = computed(
    () => !!this.suche() || !!this.katFilter() || !!this.backlogSuche(),
  );

  private laden(stillestehen: boolean): void {
    const id = ++this.reqId;
    if (!stillestehen) this.state.set({ kind: 'loading' });
    this.svc
      .plantafel({
        date_from: this.von(),
        date_to: this.bis(),
        q: this.suche(),
        category_id: this.katFilter() || null,
        backlog_q: this.backlogSuche(),
      })
      .subscribe({
        next: (data) => {
          if (id === this.reqId) this.state.set({ kind: 'ready', data });
        },
        error: (err) => {
          if (id === this.reqId) this.state.set(fehlerState(err));
        },
      });
  }
  private fetch(): void {
    this.laden(false);
  }
  /** Stilles Nachladen: das Board bleibt stehen, die Daten werden ersetzt. */
  private refresh(): void {
    this.laden(true);
  }

  protected readonly summary = computed(() => {
    const s = this.state();
    if (s.kind === 'loading') return 'Plantafel wird geladen.';
    if (s.kind === 'forbidden') return 'Keine Berechtigung für die Plantafel.';
    if (s.kind === 'error') return 'Plantafel konnte nicht geladen werden.';
    const k = this.konfliktZahl();
    return (
      `${s.data.jobs.length} Termine auf ${s.data.lanes.length} Bahnen im Zeitraum ` +
      `${this.zeitraumLabel()}. ${s.data.backlog_total} ungeplante Einsätze im Rückstand. ` +
      (k ? `${k} Termine mit Konflikt.` : 'Keine Konflikte.')
    );
  });

  private melden(text: string): void {
    this.fehler.set(text);
    this.sagen(text);
  }
  private sagen(text: string): void {
    this.ansage.set(text);
  }
  hinweiseSchliessen(): void {
    this.fehler.set(null);
    this.warnungen.set([]);
  }

  statusLabel(s: ServiceJobStatus): string {
    return serviceJobStatusLabel(s);
  }

  /**
   * Kurzform des Status für die Kachelmarke — sie bleibt in JEDER Breite stehen.
   *
   * Die Projektregel ist eindeutig: **Status nie nur über Farbe** (WCAG 1.4.1).
   * Blendete die Container-Query den vollen Statustext aus, bliebe an einer
   * schmalen Kachel nur die Rahmenfarbe — genau das, was verboten ist. Vier
   * Zeichen passen auch in einen 2-Stunden-Balken.
   */
  statusKurz(s: ServiceJobStatus): string {
    return STATUS_KURZ[s] ?? '—';
  }
  statusMod(s: ServiceJobStatus): string {
    return STATUS_MOD[s] ?? 'neutral';
  }
  zeit(iso: string): string {
    return this.timeFmt.format(new Date(iso));
  }
  categoryClass(kat: { color_token: string } | null): string {
    return kat ? categoryColorClass(kat.color_token as never) : '';
  }
  resTyp(t: string): string {
    return resourceTypeLabel(t as never);
  }
  kSymbol(k: Konflikt): string {
    return konfliktSymbol(k.kind);
  }
  kLabel(k: Konflikt): string {
    return konfliktLabel(k.kind);
  }

  /**
   * Sichtbare Konflikte auf der Kachel — höchstens die ersten zwei.
   *
   * Ein hoffnungslos überbuchter Mitarbeiter kann Dutzende Kollisionen haben;
   * die alle auszuschreiben bläht die Kachel zu einer Textwand auf und macht das
   * Board unlesbar — ausgerechnet dort, wo man am dringendsten den Überblick
   * braucht. Die restlichen werden GEZÄHLT (nicht verschwiegen) und stehen
   * vollständig im `title` und im `aria-label`.
   */
  sichtbareKonflikte(job: BoardJob): Konflikt[] {
    return job.conflicts.slice(0, 2);
  }
  weitereKonflikte(job: BoardJob): number {
    return Math.max(0, job.conflicts.length - 2);
  }
  /** Alle Konflikte als ein Text — für title und aria-label (nichts geht verloren). */
  konfliktText(job: BoardJob): string {
    return job.conflicts.map((k) => k.text).join(' ');
  }
}
