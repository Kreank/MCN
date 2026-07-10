import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import {
  CdkDrag,
  CdkDragDrop,
  CdkDropList,
  DragDropModule,
} from '@angular/cdk/drag-drop';
import { Subject, debounceTime, distinctUntilChanged } from 'rxjs';
import { ProjektService } from '../../core/projekt.service';
import { AuthService } from '../../core/auth.service';
import {
  BoardColumn,
  CasePriority,
  ServiceCaseCard,
  ServiceCaseStatus,
  ServiceCaseTransition,
} from '../../core/projekt.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { ProjekteNav } from '../projekte-nav/projekte-nav';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready' }
  | VerbotenState
  | { kind: 'error' };

/** Ausstehender begründungspflichtiger Statuswechsel (wartet auf den Dialog). */
interface PendingMove {
  cardId: string;
  von: ServiceCaseStatus;
  transition: ServiceCaseTransition;
}

/**
 * Kanban-Board der Vorgänge (service_case), Spalten = Statuskatalog. Statuswechsel
 * per Drag&Drop ODER per Tastatur (Verschieben-Auswahl je Karte) — beide Wege
 * laufen durch dieselbe Logik und denselben Schreibpfad
 * (POST /service_cases/{id}/status).
 *
 * Nur erlaubte Übergänge sind möglich: die erlaubten Ziele je Ausgangsstatus
 * kommen aus /transitions (einmal je belegter Spalte geladen). Ungültige Drops
 * verhindert das enterPredicate; ein serverseitiger Fehlschlag (422/403) rollt
 * die optimistische Verschiebung zurück (Karte springt sichtbar zurück) und zeigt
 * die Servermeldung. Begründungspflichtige Ziele (ABGELEHNT, Reversals) gehen
 * durch den Pflicht-Begründungsdialog; Abbruch rollt ebenfalls zurück.
 */
@Component({
  selector: 'app-vorgang-kanban',
  imports: [RouterLink, DragDropModule, KeinZugriff, Bestaetigung, ProjekteNav],
  templateUrl: './vorgang-kanban.html',
  styleUrl: './vorgang-kanban.scss',
})
export class VorgangKanban {
  private readonly svc = inject(ProjektService);
  private readonly auth = inject(AuthService);

  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly columns = signal<BoardColumn[]>([]);
  protected readonly cards = signal<ServiceCaseCard[]>([]);
  private readonly transitionsByStatus = signal<Record<string, ServiceCaseTransition[]>>({});

  protected readonly query = signal('');
  protected readonly includeTerminal = signal(false);
  // Serverseitige Gesamtzahl der Treffer (das Board lädt höchstens `ladeGrenze`).
  protected readonly total = signal(0);
  private readonly ladeGrenze = 500;
  protected readonly abgeschnitten = computed(() => this.total() > this.cards().length);

  // Screenreader-Ansage für Verschiebe-Ergebnisse.
  protected readonly ansage = signal('');
  // Sichtbare Fehlermeldung (Statuswechsel abgelehnt).
  protected readonly meldung = signal<string | null>(null);

  // Begründungspflichtiger Wechsel wartet auf Bestätigung.
  protected readonly pendingMove = signal<PendingMove | null>(null);
  protected readonly moveLaedt = signal(false);

  protected readonly skeletons = Array.from({ length: 4 });

  protected readonly darfAendern = computed(() => this.auth.darf('workflow', 'AENDERN'));
  protected readonly darfFreigeben = computed(() => this.auth.darf('workflow', 'FREIGEBEN'));
  // Verschieben ist möglich, wenn der Akteur mindestens ein Vorgangsrecht hat.
  // Die eigentliche Filterung je Übergang macht `hatRecht` (ein Konto mit nur
  // FREIGEBEN darf ausschließlich FREIGABE_AUSSTEHEND → BEAUFTRAGT ziehen). Nur
  // wer weder AENDERN noch FREIGEBEN hat, sieht das Board als Nur-Lese-Ansicht.
  protected readonly readonly = computed(() => !this.darfAendern() && !this.darfFreigeben());

  /** Karten je Status (nach Eingang, neueste zuerst). */
  protected readonly gruppen = computed<Record<string, ServiceCaseCard[]>>(() => {
    const map: Record<string, ServiceCaseCard[]> = {};
    for (const c of this.cards()) (map[c.status] ??= []).push(c);
    for (const s of Object.keys(map)) {
      map[s].sort((a, b) => (a.received_at < b.received_at ? 1 : -1));
    }
    return map;
  });

  protected readonly gesamt = computed(() => this.cards().length);

  protected readonly resultSummary = computed(() => {
    const s = this.state();
    if (s.kind === 'loading') return 'Vorgänge werden geladen.';
    if (s.kind === 'forbidden') return 'Keine Berechtigung für die Vorgänge.';
    if (s.kind === 'error') return 'Vorgänge konnten nicht geladen werden.';
    const n = this.gesamt();
    return `${n} ${n === 1 ? 'Vorgang' : 'Vorgänge'} auf dem Board.`;
  });

  private readonly searchInput$ = new Subject<string>();
  private reqId = 0;

  constructor() {
    this.searchInput$
      .pipe(debounceTime(300), distinctUntilChanged(), takeUntilDestroyed())
      .subscribe((v) => {
        this.query.set(v);
        this.ladeBoard();
      });
    this.ladeBoard();
  }

  onSearch(value: string): void {
    this.searchInput$.next(value);
  }

  terminalUmschalten(): void {
    this.includeTerminal.update((v) => !v);
    this.ladeBoard();
  }

  retry(): void {
    this.ladeBoard();
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  private ladeBoard(): void {
    const id = ++this.reqId;
    this.state.set({ kind: 'loading' });
    this.svc
      .listServiceCases({
        q: this.query(),
        include_terminal: this.includeTerminal(),
        page_size: this.ladeGrenze,
      })
      .subscribe({
        next: (board) => {
          if (id !== this.reqId) return;
          this.columns.set(board.columns);
          this.cards.set(board.items);
          this.total.set(board.total);
          this.transitionsByStatus.set({});
          this.state.set({ kind: 'ready' });
          this.ladeTransitions(board.items);
        },
        error: (err) => {
          if (id !== this.reqId) return;
          this.state.set(fehlerState(err));
        },
      });
  }

  /**
   * Lädt die erlaubten Übergänge je in den Karten vorkommendem Ausgangsstatus —
   * einmal pro Status (ein Repräsentant genügt, die Kanten hängen nur am
   * from_status). Fehlschlag ist unkritisch: ohne Ziele bleibt die Karte
   * unverschiebbar (fail-closed), der Server bleibt ohnehin maßgeblich.
   */
  private ladeTransitions(cards: ServiceCaseCard[]): void {
    const have = this.transitionsByStatus();
    const stati = new Set(cards.map((c) => c.status));
    stati.forEach((s) => {
      if (have[s]) return;
      const rep = cards.find((c) => c.status === s);
      if (!rep) return;
      this.svc.getServiceCaseTransitions(rep.id).subscribe({
        next: (ts) => this.transitionsByStatus.update((m) => ({ ...m, [s]: ts })),
        error: () => {
          /* fail-closed: keine Ziele → keine Verschiebung */
        },
      });
    });
  }

  /** Erlaubte Ziel-Übergänge einer Karte, gefiltert nach vorhandenem Recht. */
  zielTransitions(card: ServiceCaseCard): ServiceCaseTransition[] {
    const ts = this.transitionsByStatus()[card.status] ?? [];
    return ts.filter((t) => this.hatRecht(t));
  }

  private hatRecht(t: ServiceCaseTransition): boolean {
    return t.recht === 'FREIGEBEN' ? this.darfFreigeben() : this.darfAendern();
  }

  private zielErlaubt(card: ServiceCaseCard, zielStatus: string): boolean {
    if (zielStatus === card.status) return true; // Rückkehr in die eigene Spalte
    return this.zielTransitions(card).some((t) => t.to_status === zielStatus);
  }

  /**
   * CDK-enterPredicate: eine Spalte akzeptiert eine Karte nur, wenn der Übergang
   * vom aktuellen Status der Karte in den Status der Spalte erlaubt ist (inkl.
   * Recht). So gelingt kein ungültiger Drop.
   */
  readonly enterPredicate = (
    drag: CdkDrag<ServiceCaseCard>,
    drop: CdkDropList<BoardColumn>,
  ): boolean => this.zielErlaubt(drag.data, drop.data.status);

  onDrop(event: CdkDragDrop<BoardColumn>): void {
    if (this.readonly()) return;
    const card = event.item.data as ServiceCaseCard;
    const ziel = event.container.data.status;
    if (ziel === card.status) return; // gleiche Spalte → kein Statuswechsel
    this.verschiebe(card, ziel);
  }

  /** Tastaturweg: Auswahl „Verschieben nach …" je Karte. */
  moveViaSelect(card: ServiceCaseCard, zielStatus: string): void {
    if (!zielStatus) return;
    this.verschiebe(card, zielStatus as ServiceCaseStatus);
  }

  private verschiebe(card: ServiceCaseCard, zielStatus: string): void {
    this.meldung.set(null);
    const t = this.zielTransitions(card).find((x) => x.to_status === zielStatus);
    if (!t) {
      const text = `Wechsel „${this.statusLabel(card.status)}" → „${this.statusLabel(
        zielStatus,
      )}" ist nicht erlaubt.`;
      this.meldung.set(text);
      this.ansage.set(text);
      return;
    }
    if (t.reason_required) {
      // Optimistisch verschieben, dann Pflicht-Begründung erfragen.
      this.setzeStatus(card.id, zielStatus);
      this.pendingMove.set({ cardId: card.id, von: card.status, transition: t });
      return;
    }
    this.fuehreAus(card.id, card.status, t, null);
  }

  /** Bestätigt den begründungspflichtigen Wechsel (Dialog). */
  bestaetigeMove(reason: string | null): void {
    const pm = this.pendingMove();
    if (!pm) return;
    this.fuehreAus(pm.cardId, pm.von, pm.transition, reason);
  }

  /** Bricht den begründungspflichtigen Wechsel ab → Karte springt zurück. */
  abbrechenMove(): void {
    const pm = this.pendingMove();
    if (!pm) return;
    this.setzeStatus(pm.cardId, pm.von);
    this.pendingMove.set(null);
    this.ansage.set('Verschieben abgebrochen.');
  }

  private fuehreAus(
    cardId: string,
    von: ServiceCaseStatus,
    t: ServiceCaseTransition,
    reason: string | null,
  ): void {
    const card = this.cards().find((c) => c.id === cardId);
    const nr = card?.case_number ?? 'Vorgang';
    this.setzeStatus(cardId, t.to_status); // optimistisch (idempotent bei reason-Flow)
    this.moveLaedt.set(true);
    this.svc.advanceServiceCaseStatus(cardId, { to_status: t.to_status, reason }).subscribe({
      next: (detail) => {
        this.moveLaedt.set(false);
        this.pendingMove.set(null);
        this.setzeStatus(cardId, detail.status); // Server ist maßgeblich
        this.ansage.set(`${nr} nach „${this.statusLabel(detail.status)}" verschoben.`);
        this.ladeTransitions(this.cards()); // Ziele der neuen Spalte nachladen
      },
      error: (err) => {
        this.moveLaedt.set(false);
        this.pendingMove.set(null);
        this.setzeStatus(cardId, von); // Rollback → Karte springt zurück
        const msg = fehlerDetail(err) ?? 'Der Statuswechsel war nicht möglich.';
        this.meldung.set(msg);
        this.ansage.set(`Verschieben fehlgeschlagen: ${msg}`);
      },
    });
  }

  private setzeStatus(cardId: string, status: string): void {
    this.cards.update((cs) =>
      cs.map((c) => (c.id === cardId ? { ...c, status: status as ServiceCaseStatus } : c)),
    );
  }

  // --- Darstellungshelfer ---------------------------------------------------
  count(status: string): number {
    return this.gruppen()[status]?.length ?? 0;
  }

  statusLabel(status: string): string {
    return this.columns().find((c) => c.status === status)?.label ?? status;
  }

  priorityLabel(p: CasePriority): string {
    const map: Record<CasePriority, string> = {
      NORMAL: 'Normal',
      DRINGEND: 'Dringend',
      NOTFALL: 'Notfall',
    };
    return map[p] ?? p;
  }

  priorityClass(p: CasePriority): string {
    if (p === 'NOTFALL') return 'stamp--negativ';
    if (p === 'DRINGEND') return 'stamp--warn';
    return '';
  }

  /** Text für den Bestätigungsdialog des ausstehenden Wechsels. */
  pendingText(): string {
    const pm = this.pendingMove();
    if (!pm) return '';
    return `Der Vorgang wird auf „${this.statusLabel(pm.transition.to_status)}" gesetzt.`;
  }

  pendingTitel(): string {
    const pm = this.pendingMove();
    if (!pm) return 'Statuswechsel bestätigen';
    return `Auf „${this.statusLabel(pm.transition.to_status)}" setzen?`;
  }
}
