/**
 * Zur Entscheidung — die Arbeitsliste der technischen Leitung.
 *
 * Gegenstück zum Vorlege-Weg im Anruf-Dialog: Was die Disposition fachlich nicht
 * entscheiden konnte, steht hier und wartet. Fachlich sind das Aufträge in
 * FREIGABE_AUSSTEHEND — kein eigener Datentopf, sondern ein fester Ausschnitt aus
 * `GET /work_orders`. Deshalb auch kein eigener Endpunkt: Die Auftragsliste
 * filtert bereits nach Status, hier ist der Filter nur nicht wählbar.
 *
 * Warum trotzdem eine eigene Ansicht und nicht bloß ein Segment in
 * `features/auftrag-liste`: Der Unterschied ist nicht der Filter, sondern die
 * Frage. „Aufträge" beantwortet „was läuft?", diese Ansicht beantwortet „was
 * liegt bei MIR?". Ein Entscheider, der seine Liste erst zusammenklicken muss,
 * übersieht sie — und ein vorgelegter Auftrag, den niemand ansieht, ist genau
 * der verpuffte Anruf, den der Vorlege-Weg verhindern sollte.
 *
 * Entschieden wird hier NICHT: Jede Zeile führt in die Auftragsmappe. Dort steht
 * die Rückfrage als Begründung im Statusverlauf, dort sind Beteiligte, Objekt und
 * Termin sichtbar — und dort sitzt das Freigabetor bereits. Ein zweiter
 * Freigabe-Knopf in der Liste würde zur Entscheidung ohne Kontext verleiten.
 *
 * Aufbau (Suche, Zeilen, Blättern, Skelett, Zustände) folgt `auftrag-liste`.
 */
import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { Subject, debounceTime, distinctUntilChanged } from 'rxjs';

import { AuftragService } from '../../core/auftrag.service';
import { OrderPriority, WorkOrderPage } from '../../core/auftrag.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: WorkOrderPage }
  | VerbotenState
  | { kind: 'error' };

@Component({
  selector: 'app-entscheidungen',
  imports: [RouterLink, KeinZugriff],
  templateUrl: './entscheidungen.html',
  styleUrl: './entscheidungen.scss',
})
export class Entscheidungen {
  private readonly svc = inject(AuftragService);

  protected readonly pageSize = 20;

  protected readonly query = signal('');
  protected readonly page = signal(1);
  protected readonly state = signal<ViewState>({ kind: 'loading' });

  protected readonly skeletons = Array.from({ length: 6 });

  private readonly searchInput$ = new Subject<string>();
  private reqId = 0;

  private readonly dateFmt = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  });

  protected readonly totalPages = computed(() => {
    const s = this.state();
    if (s.kind !== 'ready') return 1;
    return Math.max(1, Math.ceil(s.data.total / s.data.page_size));
  });

  protected readonly resultSummary = computed(() => {
    const s = this.state();
    if (s.kind === 'loading') return 'Vorlagen werden geladen.';
    if (s.kind === 'forbidden') return 'Keine Berechtigung für die Auftragsfreigabe.';
    if (s.kind === 'error') return 'Vorlagen konnten nicht geladen werden.';
    const t = s.data.total;
    if (t === 0) return 'Keine Aufträge warten auf eine Entscheidung.';
    return `${t} ${t === 1 ? 'Auftrag wartet' : 'Aufträge warten'} auf eine Entscheidung, Seite ${s.data.page} von ${this.totalPages()}.`;
  });

  constructor() {
    this.searchInput$
      .pipe(debounceTime(300), distinctUntilChanged(), takeUntilDestroyed())
      .subscribe((v) => {
        this.query.set(v);
        this.page.set(1);
        this.fetch();
      });
    this.fetch();
  }

  onSearch(value: string): void {
    this.searchInput$.next(value);
  }

  prev(): void {
    if (this.page() <= 1) return;
    this.page.update((p) => p - 1);
    this.fetch();
  }

  next(): void {
    if (this.page() >= this.totalPages()) return;
    this.page.update((p) => p + 1);
    this.fetch();
  }

  retry(): void {
    this.fetch();
  }

  private fetch(): void {
    const id = ++this.reqId;
    this.state.set({ kind: 'loading' });
    // `status` ist hier fest verdrahtet, nicht wählbar: Wer den Filter aufmachen
    // will, ist in der Auftragsliste besser aufgehoben.
    this.svc
      .list({
        page: this.page(),
        page_size: this.pageSize,
        q: this.query(),
        status: 'FREIGABE_AUSSTEHEND',
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

  // ---- Darstellungshelfer -------------------------------------------------
  datum(iso: string): string {
    return this.dateFmt.format(new Date(iso));
  }

  /**
   * Wie lange der Auftrag schon wartet, in vollen Tagen.
   *
   * Die wichtigste Zahl dieser Liste: Ein Auftrag in FREIGABE_AUSSTEHEND hält
   * einen Kunden hin, der bereits einen Termin genannt bekommen hat. Ein Datum
   * allein („angelegt am 14.07.") verlangt Kopfrechnen; die Wartedauer nicht.
   */
  wartetSeitTagen(iso: string): number {
    const ms = Date.now() - new Date(iso).getTime();
    return Math.max(0, Math.floor(ms / 86_400_000));
  }

  wartezeitLabel(iso: string): string {
    const t = this.wartetSeitTagen(iso);
    if (t === 0) return 'heute';
    return t === 1 ? 'seit 1 Tag' : `seit ${t} Tagen`;
  }

  /** Ab einer Woche ohne Entscheidung ist die Wartezeit selbst das Problem. */
  wartezeitClass(iso: string): string {
    return this.wartetSeitTagen(iso) >= 7 ? 'stamp--warn' : '';
  }

  priorityLabel(p: OrderPriority): string {
    const map: Record<OrderPriority, string> = {
      NORMAL: 'Normal',
      DRINGEND: 'Dringend',
      NOTFALL: 'Notfall',
    };
    return map[p] ?? p;
  }

  priorityClass(p: OrderPriority): string {
    if (p === 'NOTFALL') return 'stamp--negativ';
    if (p === 'DRINGEND') return 'stamp--warn';
    return '';
  }
}
