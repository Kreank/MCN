import { Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Observable, concat, map, of, switchMap, tap, toArray } from 'rxjs';
import { EinsatzService } from '../../core/einsatz.service';
import { PlanungStammdatenService } from '../../core/planung-stammdaten.service';
import {
  BoardJob,
  Plantafel as PlantafelData,
  ServiceJobStatus,
  categoryColorClass,
  resourceTypeLabel,
  serviceJobStatusLabel,
} from '../../core/einsatz.model';
import { AuftragService } from '../../core/auftrag.service';
import { PropertyService } from '../../core/property.service';
import { AuthService } from '../../core/auth.service';
import { PlanungNav } from '../planung-nav/planung-nav';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld } from '../../shared/formular/feld';
import { ReferenzWahl, RefSuche } from '../../shared/formular/referenz-wahl';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: PlantafelData }
  | VerbotenState
  | { kind: 'error' };

/** Bahn: Mitarbeiter, Betriebsmittel-Ressource oder Sammelbahn ohne Zuweisung. */
type Lane = {
  id: string;
  name: string;
  kind: 'user' | 'resource' | 'unassigned';
  sub?: string;
};

/** Terminart im Anlage-Dialog: an einem Auftrag oder frei (ohne Auftrag). */
type TerminArt = 'auftrag' | 'frei';

/** Aufgenommene Kachel (Maus-Drag ODER Tastatur-/Touch-Griff): woher kommt sie? */
type Aufnahme = { job: BoardJob; lane: Lane; dayIso: string };

/** Tastatur-Cursor über dem Board (Index in lanes() bzw. days()). */
type Zielzelle = { laneIdx: number; dayIdx: number };

function laneKey(lane: Lane): string {
  return `${lane.kind}:${lane.id}`;
}

const WINDOW_DAYS = 7;

// Statusfarbe der Kacheln (nie nur Farbe — die Kachel trägt zusätzlich das
// Status-Label als Text).
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

function todayIso(): string {
  const d = new Date();
  const m = `${d.getMonth() + 1}`.padStart(2, '0');
  const day = `${d.getDate()}`.padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}

function addDaysIso(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** Kalendertag eines Zeitstempels in LOKALER Zeit (konsistent mit der lokalen
 * Zeitanzeige und dem lokalen „Heute"). */
function localDayIso(iso: string): string {
  const d = new Date(iso);
  const m = `${d.getMonth() + 1}`.padStart(2, '0');
  const day = `${d.getDate()}`.padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
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
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  protected readonly windowDays = WINDOW_DAYS;
  protected readonly rangeStart = signal(todayIso());
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  private readonly dowFmt = new Intl.DateTimeFormat('de-DE', { weekday: 'short' });
  private readonly dayFmt = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
  });
  private readonly timeFmt = new Intl.DateTimeFormat('de-DE', {
    hour: '2-digit',
    minute: '2-digit',
  });

  protected readonly days = computed(() => {
    const start = this.rangeStart();
    const today = todayIso();
    return Array.from({ length: WINDOW_DAYS }, (_, i) => {
      const iso = addDaysIso(start, i);
      const d = new Date(`${iso}T00:00:00Z`);
      return {
        iso,
        dow: this.dowFmt.format(d),
        label: this.dayFmt.format(d),
        isToday: iso === today,
      };
    });
  });

  // Bahnen: erst Mitarbeiter (aus Zuweisungen), dann Ressourcen-Bahnen
  // (Betriebsmittel aus resource.job_resource), zuletzt eine „Ohne Zuweisung"-
  // Sammelbahn, falls es verplante, aber niemandem zugeordnete Einsätze gibt.
  protected readonly lanes = computed<Lane[]>(() => {
    const s = this.state();
    if (s.kind !== 'ready') return [];
    const lanes: Lane[] = s.data.resources.map((r) => ({
      id: r.id,
      name: r.display_name,
      kind: 'user' as const,
    }));
    for (const rl of s.data.resource_lanes) {
      lanes.push({
        id: rl.id,
        name: rl.display_name,
        kind: 'resource',
        sub: resourceTypeLabel(rl.resource_type),
      });
    }
    if (s.data.unassigned_count > 0) {
      lanes.push({ id: '', name: 'Ohne Zuweisung', kind: 'unassigned' });
    }
    return lanes;
  });

  protected readonly rangeLabel = computed(() => {
    const s = this.rangeStart();
    const from = new Date(`${s}T00:00:00Z`);
    const to = new Date(`${addDaysIso(s, WINDOW_DAYS - 1)}T00:00:00Z`);
    return `${this.dayFmt.format(from)} – ${this.dayFmt.format(to)}`;
  });

  protected readonly summary = computed(() => {
    const s = this.state();
    if (s.kind === 'loading') return 'Plantafel wird geladen.';
    if (s.kind === 'forbidden') return 'Keine Berechtigung für die Plantafel.';
    if (s.kind === 'error') return 'Plantafel konnte nicht geladen werden.';
    return `${s.data.jobs.length} Einsätze auf ${this.lanes().length} Bahnen im Zeitraum ${this.rangeLabel()}.`;
  });

  constructor() {
    this.fetch();
  }

  prev(): void {
    this.rangeStart.set(addDaysIso(this.rangeStart(), -WINDOW_DAYS));
    this.fetch();
  }
  next(): void {
    this.rangeStart.set(addDaysIso(this.rangeStart(), WINDOW_DAYS));
    this.fetch();
  }
  today(): void {
    this.rangeStart.set(todayIso());
    this.fetch();
  }
  retry(): void {
    this.fetch();
  }

  /** Einsätze einer Bahn an einem Tag (Mehrfachzuweisung → in jeder Bahn). */
  cellJobs(lane: Lane, dayIso: string): BoardJob[] {
    const s = this.state();
    if (s.kind !== 'ready') return [];
    return s.data.jobs.filter((j) => {
      if (localDayIso(j.scheduled_start) !== dayIso) return false;
      if (lane.kind === 'resource') return j.resource_ids.includes(lane.id);
      if (lane.kind === 'unassigned') {
        return j.assignee_ids.length === 0 && j.resource_ids.length === 0;
      }
      return j.assignee_ids.includes(lane.id);
    });
  }

  // ---- Termin für einen Auftrag setzen (Klick in eine Bahn/Tag-Zelle) -------
  // Der Ablauf braucht ANLEGEN (create) UND AENDERN (Status/Zuweisung) — beides
  // gaten, sonst startet der Nutzer die Kette und kassiert mittendrin ein 403.
  protected readonly darfPlanen = computed(
    () => this.auth.darf('workflow', 'ANLEGEN') && this.auth.darf('workflow', 'AENDERN'),
  );
  protected readonly neuOffen = signal(false);
  protected readonly neuBusy = signal(false);
  protected readonly neuFehler = signal<string | null>(null);
  /** Angeklickte Zelle: Ziel-Bahn (ggf. Mitarbeiter) + Tag. */
  protected readonly neuCell = signal<{ lane: Lane; dayIso: string; dayLabel: string } | null>(null);

  /** Terminart: an einem Auftrag (Regelfall) oder frei (Begehung o. Ä.). */
  protected readonly art = signal<TerminArt>('auftrag');
  protected readonly neuForm = this.fb.group({
    work_order_id: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    title: this.fb.control('', { nonNullable: true }),
    property_id: this.fb.control('', { nonNullable: true }),
    start: this.fb.control('08:00', { nonNullable: true, validators: [Validators.required] }),
    end: this.fb.control('', { nonNullable: true }),
  });

  /** Umschalten: die Pflichtfelder wandern mit der Terminart. */
  artWaehlen(art: TerminArt): void {
    if (this.art() === art) return;
    this.art.set(art);
    const auftrag = this.neuForm.controls.work_order_id;
    const titel = this.neuForm.controls.title;
    if (art === 'auftrag') {
      auftrag.setValidators([Validators.required]);
      titel.clearValidators();
      titel.setValue('');
      this.neuForm.controls.property_id.setValue('');
    } else {
      auftrag.clearValidators();
      auftrag.setValue('');
      titel.setValidators([Validators.required]);
    }
    auftrag.updateValueAndValidity();
    titel.updateValueAndValidity();
  }

  /** Auftragssuche (Titel/Nummer) für die Termin-Zuordnung. */
  protected readonly auftragSuche: RefSuche = (q) =>
    this.auftragSvc
      .list({ page: 1, page_size: 20, q })
      .pipe(map((p) => p.items.map((o) => ({ id: o.id, label: o.title, sub: o.order_number }))));

  /** Liegenschaftssuche (nur beim freien Termin — sonst kommt sie vom Auftrag). */
  protected readonly liegenschaftSuche: RefSuche = (q) =>
    this.propertySvc.list({ page: 1, page_size: 20, q }).pipe(
      map((p) =>
        p.items.map((x) => ({ id: x.id, label: x.name, sub: `${x.property_number} · ${x.city}` })),
      ),
    );

  terminOeffnen(lane: Lane, day: { iso: string; label: string }): void {
    if (!this.darfPlanen()) return;
    this.neuCell.set({ lane, dayIso: day.iso, dayLabel: `${day.label}` });
    this.neuFehler.set(null);
    this.neuForm.reset({
      work_order_id: '', title: '', property_id: '', start: '08:00', end: '',
    });
    this.art.set('frei');
    this.artWaehlen('auftrag');
    this.neuOffen.set(true);
  }

  terminSchliessen(): void {
    if (this.neuBusy()) return;
    this.neuOffen.set(false);
  }

  private toIso(dayIso: string, time: string): string {
    // Lokale Zeit (dayIso + HH:MM) → UTC-ISO für den Server (timestamptz).
    return new Date(`${dayIso}T${time}:00`).toISOString();
  }

  terminAnlegen(): void {
    if (this.neuBusy()) return;
    const cell = this.neuCell();
    if (!cell) return;
    this.neuForm.markAllAsTouched();
    if (this.neuForm.invalid) return;
    const v = this.neuForm.getRawValue();
    const start = this.toIso(cell.dayIso, v.start);
    const end = v.end ? this.toIso(cell.dayIso, v.end) : null;
    if (end && end <= start) {
      this.neuFehler.set('Das Ende muss nach dem Beginn liegen.');
      return;
    }
    this.neuBusy.set(true);
    this.neuFehler.set(null);
    const frei = this.art() === 'frei';
    // Anlegen (UNGEPLANT mit Zeitraum) → auf GEPLANT setzen → Bahn-Mitarbeiter
    // zuweisen (nur bei Mitarbeiter-Bahnen).
    this.svc
      .create({
        work_order_id: frei ? null : v.work_order_id,
        title: frei ? v.title.trim() : null,
        property_id: frei ? v.property_id || null : null,
        scheduled_start: start,
        scheduled_end: end,
        appointment_category_id: null,
      })
      .pipe(
        switchMap((job) =>
          this.svc.advanceStatus(job.id, { to_status: 'GEPLANT' }).pipe(map(() => job)),
        ),
        switchMap((job) =>
          cell.lane.kind === 'user'
            ? this.svc
                .assign(job.id, { assignee_user_id: cell.lane.id, role: 'TECHNICIAN' })
                .pipe(map(() => job))
            : of(job),
        ),
      )
      .subscribe({
        next: () => {
          this.neuBusy.set(false);
          this.neuOffen.set(false);
          this.fetch();
        },
        error: (err) => {
          this.neuBusy.set(false);
          // Der Einsatz kann bereits (teilweise) angelegt sein (create ok, aber
          // Status/Zuweisung schlug fehl). Das Board neu laden, damit der reale
          // Zustand sichtbar wird, und ehrlich benennen (kein stilles „nichts").
          this.neuFehler.set(
            (fehlerDetail(err) ?? 'Der Termin konnte nicht vollständig angelegt werden.') +
              ' Der Einsatz ist ggf. angelegt, aber noch nicht verplant/zugewiesen — bitte in der Einsatzliste prüfen.',
          );
          this.fetch();
        },
      });
  }

  // =========================================================================
  // Umplanen per Drag & Drop — mit gleichwertiger Tastaturbedienung
  // =========================================================================
  // Die Bahnen sind Mitarbeiter (aus den Zuweisungen), Betriebsmittel-Ressourcen
  // und die Sammelbahn „Ohne Zuweisung"; die Spalten sind Tage. Eine Kachel zu
  // verschieben heißt deshalb ZWEIERLEI:
  //   * andere Spalte  → Umplanen (POST /einsaetze/{id}/schedule): der Tag
  //     wechselt, Uhrzeit und Dauer bleiben erhalten.
  //   * andere Bahn    → Zuweisung/Zuordnung umhängen (POST/DELETE
  //     .../assignments bzw. .../ressourcen).
  //
  // Bewusst KEIN neues Framework (kein @angular/cdk): die native HTML5-
  // Drag&Drop-API reicht für „Kachel auf Zelle" vollständig aus. Weil sie auf
  // Touch-Geräten nicht greift und für die Tastatur ohnehin unbrauchbar ist,
  // trägt jede Kachel zusätzlich einen „Aufnehmen"-Knopf: er startet denselben
  // Verschiebe-Modus (Pfeiltasten bewegen, Enter legt ab, Escape bricht ab) —
  // ein Zustandsautomat für Maus, Tastatur und Touch.
  //
  // Doppelbelegung ist eine WEICHE Invariante: der Server warnt, blockiert aber
  // nicht. Die Warnungen werden angezeigt, nicht verschluckt.

  /** Umplanen braucht nur AENDERN (Anlegen ist eine andere Aktion). */
  protected readonly darfUmplanen = computed(() => this.auth.darf('workflow', 'AENDERN'));

  /** Tastatur-/Touch-Griff: Kachel ist „aufgenommen". */
  protected readonly griff = signal<Aufnahme | null>(null);
  /** Laufender Maus-Drag (HTML5). */
  protected readonly zieht = signal<Aufnahme | null>(null);
  /** Tastatur-Cursor auf dem Board (nur bei aktivem Griff). */
  protected readonly zielzelle = signal<Zielzelle | null>(null);
  /** Zelle unter dem Mauszeiger während eines Drags (Schlüssel `laneKey|tag`). */
  protected readonly dropZiel = signal<string | null>(null);

  protected readonly busy = signal(false);
  protected readonly fehler = signal<string | null>(null);
  protected readonly warnungen = signal<string[]>([]);
  /** Ansage für die ARIA-Live-Region (und sichtbar in der Statusleiste). */
  protected readonly ansage = signal('');

  /** Aktive Quelle — egal ob Maus oder Tastatur. */
  protected readonly quelle = computed<Aufnahme | null>(() => this.griff() ?? this.zieht());

  /** Bahnen, in die die aktuelle Quelle überhaupt darf (Tastatur-Navigation
   * überspringt die anderen). */
  protected readonly zielBahnen = computed<number[]>(() => {
    const q = this.quelle();
    if (!q) return [];
    return this.lanes()
      .map((lane, i) => ({ lane, i }))
      .filter((x) => this.bahnKompatibel(q.lane, x.lane))
      .map((x) => x.i);
  });

  /**
   * Eine Kachel wechselt nur zwischen Bahnen DERSELBEN Art; die Sammelbahn
   * „Ohne Zuweisung" ist mit beiden verträglich (dort wird nur gelöst bzw.
   * erstmals zugeordnet). Ein Mitarbeiter gegen ein Fahrzeug zu tauschen wäre
   * keine Umplanung, sondern ein Datenverlust — ein Einsatz trägt beides
   * nebeneinander. Solche Zuordnungen bleiben dem Einsatz-Detail vorbehalten.
   */
  private bahnKompatibel(von: Lane, nach: Lane): boolean {
    if (von.kind === nach.kind) return true;
    return von.kind === 'unassigned' || nach.kind === 'unassigned';
  }

  /** Ist diese Zelle gerade ein markiertes Ziel (Maus-Hover oder Tastatur-Cursor)? */
  istZiel(laneIdx: number, dayIdx: number): boolean {
    const z = this.zielzelle();
    if (z && z.laneIdx === laneIdx && z.dayIdx === dayIdx) return true;
    const lane = this.lanes()[laneIdx];
    const day = this.days()[dayIdx];
    return !!lane && !!day && this.dropZiel() === `${laneKey(lane)}|${day.iso}`;
  }

  /** Ist diese Zelle für die aktive Quelle ein zulässiges Ziel? */
  istErlaubtesZiel(laneIdx: number): boolean {
    const q = this.quelle();
    const lane = this.lanes()[laneIdx];
    return !!q && !!lane && this.bahnKompatibel(q.lane, lane);
  }

  /** Wird diese Kachel gerade verschoben (optisch ausgegraut)? */
  istInBewegung(job: BoardJob): boolean {
    return this.quelle()?.job.id === job.id;
  }

  // --- Maus (HTML5-Drag&Drop) ---------------------------------------------
  dragStart(ev: DragEvent, job: BoardJob, lane: Lane, dayIso: string): void {
    if (!this.darfUmplanen() || this.busy()) {
      ev.preventDefault();
      return;
    }
    this.griff.set(null);
    this.zieht.set({ job, lane, dayIso });
    this.fehler.set(null);
    this.warnungen.set([]);
    if (ev.dataTransfer) {
      ev.dataTransfer.effectAllowed = 'move';
      // Nutzlast nur als Beleg; die Wahrheit steht im Signal (kein Fremd-Drop).
      ev.dataTransfer.setData('text/plain', job.job_number);
    }
  }

  dragEnde(): void {
    this.zieht.set(null);
    this.dropZiel.set(null);
  }

  dragUeber(ev: DragEvent, laneIdx: number, lane: Lane, dayIso: string): void {
    if (!this.zieht() || !this.istErlaubtesZiel(laneIdx)) return;
    // preventDefault MACHT die Zelle erst zum Drop-Ziel.
    ev.preventDefault();
    if (ev.dataTransfer) ev.dataTransfer.dropEffect = 'move';
    this.dropZiel.set(`${laneKey(lane)}|${dayIso}`);
  }

  dragVerlassen(lane: Lane, dayIso: string): void {
    if (this.dropZiel() === `${laneKey(lane)}|${dayIso}`) this.dropZiel.set(null);
  }

  drop(ev: DragEvent, lane: Lane, dayIso: string): void {
    ev.preventDefault();
    const q = this.zieht();
    this.dragEnde();
    if (q) this.verschieben(q, lane, dayIso);
  }

  // --- Tastatur & Touch (gleichwertiger Weg) -------------------------------
  /** Der Knopf, der den Griff ausgelöst hat — dorthin geht der Fokus zurück.
   *
   * Bewusst das ELEMENT, keine DOM-ID: ein Einsatz mit mehreren Zuweisungen
   * erscheint in JEDER Bahn (n:m). Eine ID aus der Job-ID wäre mehrfach im
   * Dokument und `getElementById` gäbe die Kachel der FALSCHEN Bahn zurück
   * (Muster wie `menuAusloeser` im Beleg-Editor). */
  private griffAusloeser: HTMLElement | null = null;

  aufnehmen(job: BoardJob, lane: Lane, dayIso: string, ev?: Event): void {
    if (!this.darfUmplanen() || this.busy()) return;
    const laneIdx = this.lanes().findIndex((l) => laneKey(l) === laneKey(lane));
    const dayIdx = this.days().findIndex((d) => d.iso === dayIso);
    if (laneIdx < 0 || dayIdx < 0) return;
    this.griffAusloeser = (ev?.currentTarget as HTMLElement) ?? null;
    this.fehler.set(null);
    this.warnungen.set([]);
    this.griff.set({ job, lane, dayIso });
    this.zielzelle.set({ laneIdx, dayIdx });
    this.sagen(
      `Einsatz ${job.job_number}, ${job.title}, aufgenommen. ` +
        'Pfeiltasten zum Verschieben, Enter zum Ablegen, Escape zum Abbrechen.',
    );
    this.zielFokussieren();
  }

  abbrechen(): void {
    const q = this.griff();
    const ausloeser = this.griffAusloeser;
    this.griff.set(null);
    this.zielzelle.set(null);
    this.griffAusloeser = null;
    if (q) {
      this.sagen(`Verschieben abgebrochen. Einsatz ${q.job.job_number} bleibt, wo er war.`);
      // Fokus zurück auf GENAU den Knopf, der den Griff ausgelöst hat (nicht auf
      // eine gleichnamige Kachel in einer anderen Bahn) — der Fokus geht nie verloren.
      setTimeout(() => {
        if (ausloeser?.isConnected) ausloeser.focus();
      });
    }
  }

  /** Tastensteuerung, solange eine Kachel aufgenommen ist (Fokus liegt auf der
   * Zielzelle, das Event blubbert zum Board). */
  boardTaste(ev: KeyboardEvent): void {
    const q = this.griff();
    const z = this.zielzelle();
    if (!q || !z) return;
    const bahnen = this.zielBahnen();
    const pos = bahnen.indexOf(z.laneIdx);
    const letzterTag = this.days().length - 1;
    let neu: Zielzelle | null = null;
    switch (ev.key) {
      case 'ArrowLeft':
        neu = { ...z, dayIdx: Math.max(0, z.dayIdx - 1) };
        break;
      case 'ArrowRight':
        neu = { ...z, dayIdx: Math.min(letzterTag, z.dayIdx + 1) };
        break;
      case 'ArrowUp':
        if (pos > 0) neu = { ...z, laneIdx: bahnen[pos - 1] };
        break;
      case 'ArrowDown':
        if (pos >= 0 && pos < bahnen.length - 1) neu = { ...z, laneIdx: bahnen[pos + 1] };
        break;
      case 'Enter':
      case ' ':
      case 'Spacebar': {
        ev.preventDefault();
        const lane = this.lanes()[z.laneIdx];
        const day = this.days()[z.dayIdx];
        this.griff.set(null);
        this.zielzelle.set(null);
        this.verschieben(q, lane, day.iso);
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
    const day = this.days()[neu.dayIdx];
    this.sagen(`Ziel: ${day.dow}, ${day.label}, Bahn ${lane.name}.`);
    this.zielFokussieren();
  }

  /** Fokus auf die Zielzelle — erst NACH dem Rendern (die Drop-Knöpfe entstehen
   * mit dem Griff; ein Microtask liefe zu früh und der Fokus bliebe hängen). */
  private zielFokussieren(): void {
    const z = this.zielzelle();
    if (!z) return;
    setTimeout(() => document.getElementById(`drop-${z.laneIdx}-${z.dayIdx}`)?.focus());
  }

  /** Klick/Tap auf eine Zielzelle (Maus & Touch nutzen denselben Weg). */
  zielAnklicken(lane: Lane, dayIso: string): void {
    const q = this.griff();
    if (!q) return;
    this.griff.set(null);
    this.zielzelle.set(null);
    this.verschieben(q, lane, dayIso);
  }

  // --- Der eigentliche Umzug ----------------------------------------------
  /** Neuer Zeitraum: Tag wechselt, Uhrzeit und Dauer bleiben erhalten. */
  private neuerZeitraum(job: BoardJob, dayIso: string): { start: string; end: string | null } {
    const alt = new Date(job.scheduled_start);
    const start = new Date(`${dayIso}T00:00:00`);
    start.setHours(alt.getHours(), alt.getMinutes(), alt.getSeconds(), 0);
    let end: string | null = null;
    if (job.scheduled_end) {
      const dauer = new Date(job.scheduled_end).getTime() - alt.getTime();
      end = new Date(start.getTime() + dauer).toISOString();
    }
    return { start: start.toISOString(), end };
  }

  private verschieben(q: Aufnahme, ziel: Lane, dayIso: string): void {
    if (this.busy()) return;
    this.griffAusloeser = null;
    this.fehler.set(null);
    this.warnungen.set([]);
    if (!this.bahnKompatibel(q.lane, ziel)) {
      this.melden(
        'Ein Einsatz wechselt nur zwischen Bahnen derselben Art. ' +
          'Betriebsmittel ordnest du im Einsatz-Detail zu.',
      );
      return;
    }
    const tagWechsel = q.dayIso !== dayIso;
    const bahnWechsel = laneKey(q.lane) !== laneKey(ziel);
    if (!tagWechsel && !bahnWechsel) {
      this.sagen('Abgelegt, wo der Einsatz schon war — nichts geändert.');
      return;
    }
    const s = this.state();
    if (s.kind !== 'ready') return;

    const job = q.job;
    const zeit = this.neuerZeitraum(job, dayIso);
    const day = this.days().find((d) => d.iso === dayIso);
    const zielText = `${day?.dow ?? ''} ${day?.label ?? ''}, Bahn ${ziel.name}`;

    // --- Optimistisch: die Kachel springt sofort ---------------------------
    const vorher = s.data.jobs;
    const neuerJob: BoardJob = {
      ...job,
      scheduled_start: tagWechsel ? zeit.start : job.scheduled_start,
      scheduled_end: tagWechsel ? zeit.end : job.scheduled_end,
      assignee_ids: this.neueIds(job.assignee_ids, q.lane, ziel, 'user'),
      resource_ids: this.neueIds(job.resource_ids, q.lane, ziel, 'resource'),
    };
    this.state.set({
      kind: 'ready',
      data: { ...s.data, jobs: vorher.map((j) => (j.id === job.id ? neuerJob : j)) },
    });

    // --- Server: erst ans Ziel hängen, dann die Quelle räumen --------------
    // Reihenfolge mit Absicht: Schlägt das Räumen fehl, hängt der Einsatz an
    // beiden Bahnen (sichtbar, korrigierbar) — nie an gar keiner.
    const rufe: Observable<string[]>[] = [];
    if (tagWechsel) {
      rufe.push(
        this.svc
          .setSchedule(job.id, { scheduled_start: zeit.start, scheduled_end: zeit.end })
          .pipe(map((r) => r.warnings ?? [])),
      );
    }
    if (bahnWechsel) {
      if (ziel.kind === 'user' && !job.assignee_ids.includes(ziel.id)) {
        rufe.push(
          this.svc
            .assign(job.id, { assignee_user_id: ziel.id, role: 'TECHNICIAN' })
            .pipe(map((r) => r.warnings ?? [])),
        );
      }
      if (ziel.kind === 'resource' && !job.resource_ids.includes(ziel.id)) {
        rufe.push(
          this.stammSvc
            .assignRessource(job.id, ziel.id)
            .pipe(map((r) => r.warnings ?? [])),
        );
      }
      if (q.lane.kind === 'user') {
        rufe.push(this.svc.unassign(job.id, q.lane.id).pipe(map(() => [] as string[])));
      }
      if (q.lane.kind === 'resource') {
        rufe.push(
          this.stammSvc.unassignRessource(job.id, q.lane.id).pipe(map(() => [] as string[])),
        );
      }
    }
    if (rufe.length === 0) return;

    // Ein Umzug kann aus MEHREREN Server-Schritten bestehen (Umplanen +
    // Zuweisen + Lösen). Scheitert Schritt 2, ist Schritt 1 bereits GESCHRIEBEN —
    // dann wäre „steht wieder an seinem alten Platz" eine Lüge. Deshalb zählen
    // wir die geschriebenen Schritte mit und formulieren danach.
    const schritte = rufe.length;
    let erledigt = 0;

    this.busy.set(true);
    this.sagen(`Einsatz ${job.job_number} wird verschoben …`);
    concat(...rufe)
      .pipe(
        tap(() => erledigt++),
        toArray(),
      )
      .subscribe({
        next: (listen) => {
          this.busy.set(false);
          const warn = [...new Set(listen.flat())];
          this.warnungen.set(warn);
          const kern = `Einsatz ${job.job_number} abgelegt auf ${zielText}.`;
          this.sagen(
            warn.length
              ? `${kern} ${warn.length} Warnung${warn.length > 1 ? 'en' : ''}: ${warn.join(' ')}`
              : kern,
          );
          // Die Wahrheit kommt vom Server (Bahnen, Zähler, Nachbarkacheln).
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
          if (erledigt === 0) {
            // Nichts geschrieben → echter Rollback, und nur DANN darf man das
            // auch behaupten.
            const jetzt = this.state();
            if (jetzt.kind === 'ready') {
              this.state.set({ kind: 'ready', data: { ...jetzt.data, jobs: vorher } });
            }
            this.melden(
              `${kopf}${detail ? ' ' + detail : ''} Der Einsatz ${job.job_number} steht ` +
                'unverändert an seinem alten Platz.',
            );
          } else {
            // Teilerfolg: Schritt 1 (ggf. auch 2) IST geschrieben. Kein Rollback
            // vortäuschen — den Stand vom Server holen und ehrlich benennen.
            this.melden(
              `${kopf}${detail ? ' ' + detail : ''} Achtung: Der Umzug wurde nur ` +
                `TEILWEISE ausgeführt (${erledigt} von ${schritte} Schritten sind ` +
                `geschrieben). Der Stand von Einsatz ${job.job_number} wurde neu geladen — ` +
                'bitte prüfen und den Rest von Hand nachziehen.',
            );
          }
          // In beiden Fällen: der Server hat recht, nicht die Optimistik.
          this.refresh();
        },
      });
  }

  /** ID-Liste einer Bahnart nach dem Umzug (Ziel hinzu, Quelle raus). */
  private neueIds(ids: string[], von: Lane, nach: Lane, art: 'user' | 'resource'): string[] {
    let neu = [...ids];
    if (nach.kind === art && !neu.includes(nach.id)) neu.push(nach.id);
    if (von.kind === art && von.id !== nach.id) neu = neu.filter((i) => i !== von.id);
    return neu;
  }

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

  /** Stilles Nachladen: das Board bleibt stehen, die Daten werden ersetzt. */
  private refresh(): void {
    const id = ++this.reqId;
    const from = addDaysIso(this.rangeStart(), -1);
    const to = addDaysIso(this.rangeStart(), WINDOW_DAYS);
    this.svc.plantafel(from, to).subscribe({
      next: (data) => {
        if (id === this.reqId) this.state.set({ kind: 'ready', data });
      },
      error: (err) => {
        if (id === this.reqId) this.state.set(fehlerState(err));
      },
    });
  }

  private fetch(): void {
    const id = ++this.reqId;
    this.state.set({ kind: 'loading' });
    // Fenster um je einen Tag geweitet, damit Einsätze an der lokalen
    // Tagesgrenze (UTC-Datum ±1) korrekt in eine sichtbare Bahn-Spalte fallen.
    const from = addDaysIso(this.rangeStart(), -1);
    const to = addDaysIso(this.rangeStart(), WINDOW_DAYS);
    this.svc.plantafel(from, to).subscribe({
      next: (data) => {
        if (id === this.reqId) this.state.set({ kind: 'ready', data });
      },
      error: (err) => {
        if (id === this.reqId) this.state.set(fehlerState(err));
      },
    });
  }

  // ---- Darstellungshelfer -------------------------------------------------
  statusLabel(s: ServiceJobStatus): string {
    return serviceJobStatusLabel(s);
  }
  statusMod(s: ServiceJobStatus): string {
    return STATUS_MOD[s] ?? 'neutral';
  }
  time(iso: string): string {
    return this.timeFmt.format(new Date(iso));
  }
  /** Farbklasse der Kategorie-Kachel (Farbe nur Ergänzung; der Name steht als
   * Text dabei). Leerer String, wenn keine Kategorie gesetzt ist. */
  categoryClass(job: BoardJob): string {
    return job.category ? categoryColorClass(job.category.color_token) : '';
  }
}
