import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { ProjektService } from '../../core/projekt.service';
import { AuthService } from '../../core/auth.service';
import {
  CasePriority,
  ServiceCaseDetail,
  ServiceCaseStatus,
  ServiceCaseTransition,
} from '../../core/projekt.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { Dateien } from '../../shared/dateien/dateien';
import { ZielFilter } from '../../core/datei.model';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: ServiceCaseDetail }
  | VerbotenState
  | { kind: 'error' };

type Meldung = { art: 'erfolg' | 'fehler'; text: string };

// Schwer umkehrbare Ziele: auch ohne Begründungspflicht hinter eine
// hervorgehobene Bestätigung (Amber) stellen.
const SCHWER_UMKEHRBAR: ReadonlySet<ServiceCaseStatus> = new Set<ServiceCaseStatus>([
  'ABGELEHNT',
  'ABGESCHLOSSEN',
]);

@Component({
  selector: 'app-vorgang-detail',
  imports: [Mappe, RouterLink, KeinZugriff, Dateien, Bestaetigung],
  templateUrl: './vorgang-detail.html',
  styleUrl: './vorgang-detail.scss',
})
export class VorgangDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(ProjektService);
  private readonly auth = inject(AuthService);

  protected readonly tab = signal('uebersicht');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  protected readonly tabs: MappeTab[] = [
    { id: 'uebersicht', label: 'Übersicht' },
    { id: 'verlauf', label: 'Verlauf' },
    { id: 'dateien', label: 'Dateien' },
  ];

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  // ---- Statusaktionen -----------------------------------------------------
  protected readonly uebergaenge = signal<ServiceCaseTransition[]>([]);
  protected readonly meldung = signal<Meldung | null>(null);
  /** Der gewählte Zielübergang, für den der Bestätigungsdialog offen ist. */
  protected readonly gewaehlt = signal<ServiceCaseTransition | null>(null);
  protected readonly statusLaedt = signal(false);

  /** Nur Übergänge, für die der Benutzer das nötige Recht hat. */
  protected readonly erlaubteUebergaenge = computed(() =>
    this.uebergaenge().filter((t) => this.auth.darf('workflow', t.recht)),
  );

  /** Ein gewählter Übergang ist folgenreich (Amber), wenn begründungspflichtig
   *  oder schwer umkehrbar (ABGELEHNT/ABGESCHLOSSEN). */
  protected readonly gewaehltGefahr = computed(() => {
    const t = this.gewaehlt();
    return !!t && (t.reason_required || SCHWER_UMKEHRBAR.has(t.to_status));
  });

  protected readonly dialogTitel = computed(() => {
    const t = this.gewaehlt();
    return t ? `Status auf „${t.label}“ ändern?` : '';
  });

  protected readonly dialogText = computed(() => {
    const t = this.gewaehlt();
    const d = this.daten();
    if (!t || !d) return '';
    return (
      `Der Vorgang wechselt von „${this.statusLabel(d.status)}“ auf „${t.label}“. ` +
      'Der Wechsel wird im Statusverlauf protokolliert.'
    );
  });

  /** Stabile Zielreferenz fuer den Dateien-Tab (nur bei Vorgangswechsel neu). */
  protected readonly dateienZiel = computed<ZielFilter>(() => ({
    service_case_id: this.daten()?.id ?? '',
  }));

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.tab.set('uebersicht');
      this.meldung.set(null);
      this.gewaehlt.set(null);
      if (!id) {
        this.state.set({ kind: 'error' });
        return;
      }
      this.load(id);
    });
  }

  retry(): void {
    const id = this.route.snapshot.paramMap.get('id');
    if (id) this.load(id);
  }

  private load(id: string): void {
    const rid = ++this.reqId;
    this.state.set({ kind: 'loading' });
    this.uebergaenge.set([]);
    this.svc.getServiceCase(id).subscribe({
      next: (data) => {
        if (rid === this.reqId) {
          this.state.set({ kind: 'ready', data });
          this.ladeUebergaenge(data.id);
        }
      },
      error: (err) => {
        if (rid === this.reqId) this.state.set(fehlerState(err));
      },
    });
  }

  private ladeUebergaenge(id: string): void {
    const rid = this.reqId;
    this.svc.getServiceCaseTransitions(id).subscribe({
      next: (ts) => {
        if (rid === this.reqId) this.uebergaenge.set(ts);
      },
      // Fehlschlag der Übergangsliste ist nicht fatal: Detail bleibt sichtbar,
      // es werden nur keine Statusknöpfe angeboten.
      error: () => {
        if (rid === this.reqId) this.uebergaenge.set([]);
      },
    });
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  statusFragen(t: ServiceCaseTransition): void {
    this.meldung.set(null);
    this.gewaehlt.set(t);
  }

  statusAbbrechen(): void {
    if (!this.statusLaedt()) this.gewaehlt.set(null);
  }

  statusBestaetigen(grund: string | null): void {
    const ziel = this.gewaehlt();
    const d = this.daten();
    if (!ziel || !d) return;
    this.statusLaedt.set(true);
    this.svc
      .advanceServiceCaseStatus(d.id, { to_status: ziel.to_status, reason: grund })
      .subscribe({
        next: (res) => {
          this.statusLaedt.set(false);
          this.gewaehlt.set(null);
          // reqId erhöhen, damit ein noch laufender load() das nicht überschreibt.
          ++this.reqId;
          this.state.set({ kind: 'ready', data: res });
          this.ladeUebergaenge(res.id);
          this.meldung.set({
            art: 'erfolg',
            text: `Status geändert auf „${this.statusLabel(res.status)}".`,
          });
        },
        error: (err) => {
          this.statusLaedt.set(false);
          this.gewaehlt.set(null);
          this.meldung.set({
            art: 'fehler',
            text: fehlerDetail(err) ?? 'Der Statuswechsel ist fehlgeschlagen.',
          });
        },
      });
  }

  // ---- Darstellungshelfer -------------------------------------------------
  statusLabel(s: ServiceCaseStatus): string {
    const map: Record<ServiceCaseStatus, string> = {
      NEU: 'Neu',
      IN_PRUEFUNG: 'In Prüfung',
      RUECKFRAGE: 'Rückfrage',
      FREIGABE_AUSSTEHEND: 'Freigabe ausstehend',
      BEAUFTRAGT: 'Beauftragt',
      ABGESCHLOSSEN: 'Abgeschlossen',
      ABGELEHNT: 'Abgelehnt',
    };
    return map[s] ?? s;
  }
  statusClass(s: ServiceCaseStatus): string {
    if (s === 'ABGESCHLOSSEN') return 'stamp--positive';
    if (s === 'ABGELEHNT') return 'stamp--warn';
    return '';
  }
  // Auch für Verlaufseinträge (String-Status).
  statusLabelStr(s: string | null): string {
    if (s === null) return 'Anlage';
    return this.statusLabel(s as ServiceCaseStatus);
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
    return p === 'NORMAL' ? '' : 'stamp--warn';
  }

  scopeLabel(s: string): string {
    const map: Record<string, string> = {
      UNKNOWN: 'Ungeklärt',
      COMMON_PROPERTY: 'Gemeinschaftseigentum',
      PRIVATE_UNIT: 'Sondereigentum',
      MIXED: 'Gemischt',
    };
    return map[s] ?? s;
  }
}
