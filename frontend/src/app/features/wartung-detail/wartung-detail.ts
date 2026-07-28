import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerDetail, fehlerState, istVerboten } from '../../shared/http-fehler';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { Dialog } from '../../shared/dialog/dialog';
import { AuthService } from '../../core/auth.service';
import { WartungService } from '../../core/wartung.service';
import { AnlageService } from '../../core/anlage.service';
import { Anlage, artLabel } from '../../core/anlage.model';
import {
  ContractDetail,
  ContractStatus,
  DueAction,
  IntervalKind,
  contractStatusClass,
  contractStatusLabel,
  dueActionLabel,
  intervalKindLabel,
} from '../../core/wartung.model';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: ContractDetail }
  | VerbotenState
  | { kind: 'error' };

type Meldung = { art: 'erfolg' | 'fehler'; text: string };

@Component({
  selector: 'app-wartung-detail',
  imports: [Mappe, RouterLink, KeinZugriff, Bestaetigung, Dialog],
  templateUrl: './wartung-detail.html',
  styleUrl: './wartung-detail.scss',
})
export class WartungDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(WartungService);
  private readonly anlageSvc = inject(AnlageService);
  private readonly auth = inject(AuthService);

  protected readonly darfAendern = computed(() => this.auth.darf('maintenance', 'AENDERN'));

  protected readonly tab = signal('details');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  // --- Schreibaktionen -----------------------------------------------------
  protected readonly meldung = signal<Meldung | null>(null);
  protected readonly aktionBusy = signal(false);
  protected readonly archivFragen = signal(false);
  protected readonly archivLaedt = signal(false);
  protected readonly triggerFragen = signal(false);
  protected readonly triggerLaedt = signal(false);

  protected readonly tabs: MappeTab[] = [
    { id: 'details', label: 'Details' },
    { id: 'erinnerung', label: 'Erinnerung' },
    { id: 'verlauf', label: 'Verlauf' },
  ];

  private readonly dateFmt = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  });

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.tab.set('details');
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
    this.svc.get(id).subscribe({
      next: (data) => {
        if (rid === this.reqId) this.state.set({ kind: 'ready', data });
      },
      error: (err) => {
        if (rid === this.reqId) this.state.set(fehlerState(err));
      },
    });
  }

  // ---- Statuswechsel & Auslösen -------------------------------------------
  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  private aktionsFehler(err: unknown): void {
    const text = istVerboten(err)
      ? (fehlerDetail(err) ?? 'Keine Berechtigung für diese Aktion.')
      : (fehlerDetail(err) ?? 'Die Aktion ist fehlgeschlagen. Bitte erneut versuchen.');
    this.meldung.set({ art: 'fehler', text });
  }

  /** Aktivieren / Deaktivieren (reversibel) — direkt, ohne Bestätigung. */
  statusSetzen(to: ContractStatus, erfolg: string): void {
    const d = this.daten();
    if (!d || this.aktionBusy()) return;
    this.aktionBusy.set(true);
    this.meldung.set(null);
    this.svc.setStatus(d.id, { to_status: to }).subscribe({
      next: () => {
        this.aktionBusy.set(false);
        this.meldung.set({ art: 'erfolg', text: erfolg });
        this.load(d.id);
      },
      error: (err) => {
        this.aktionBusy.set(false);
        this.aktionsFehler(err);
      },
    });
  }

  // Archivieren (final) → Bestätigung.
  archivFragen_(): void {
    this.archivFragen.set(true);
  }
  archivAbbrechen(): void {
    if (!this.archivLaedt()) this.archivFragen.set(false);
  }
  archivBestaetigen(): void {
    const d = this.daten();
    if (!d) return;
    this.archivLaedt.set(true);
    this.svc.setStatus(d.id, { to_status: 'ARCHIVIERT' }).subscribe({
      next: () => {
        this.archivLaedt.set(false);
        this.archivFragen.set(false);
        this.meldung.set({ art: 'erfolg', text: 'Vertrag archiviert.' });
        this.load(d.id);
      },
      error: (err) => {
        this.archivLaedt.set(false);
        this.archivFragen.set(false);
        this.aktionsFehler(err);
      },
    });
  }

  // Fälligkeit auslösen (erzeugt Folgeobjekte) → Bestätigung.
  triggerFragen_(): void {
    this.triggerFragen.set(true);
  }
  triggerAbbrechen(): void {
    if (!this.triggerLaedt()) this.triggerFragen.set(false);
  }
  triggerBestaetigen(): void {
    const d = this.daten();
    if (!d) return;
    this.triggerLaedt.set(true);
    this.svc.trigger(d.id, { note: null }).subscribe({
      next: () => {
        this.triggerLaedt.set(false);
        this.triggerFragen.set(false);
        this.meldung.set({ art: 'erfolg', text: 'Fälligkeit ausgelöst; Folgeobjekt erzeugt.' });
        this.load(d.id);
      },
      error: (err) => {
        this.triggerLaedt.set(false);
        this.triggerFragen.set(false);
        this.aktionsFehler(err);
      },
    });
  }

  // ---- Abgedeckte Anlagen (Migration 0135) --------------------------------
  //
  // Der Dialog lädt die Anlagen der Liegenschaft und schickt die vollständige
  // Menge zurück — kein „minus diese eine". Der gewünschte Zustand ist damit
  // immer eindeutig, auch wenn zwei Leute gleichzeitig am Vertrag arbeiten.

  protected readonly anlagenDialog = signal(false);
  protected readonly anlagen = signal<Anlage[]>([]);
  protected readonly anlagenLaedt = signal(false);
  protected readonly anlagenSpeichert = signal(false);
  protected readonly anlagenFehler = signal<string | null>(null);
  protected readonly gewaehlt = signal<ReadonlySet<string>>(new Set());
  private anlagenReq = 0;

  protected readonly anlageArt = artLabel;

  anlagenOeffnen(): void {
    const d = this.daten();
    if (!d || !this.darfAendern()) return;
    this.gewaehlt.set(new Set(d.assets.map((a) => a.id)));
    this.anlagenFehler.set(null);
    this.anlagenDialog.set(true);

    const rid = ++this.anlagenReq;
    this.anlagenLaedt.set(true);
    // **Mit** stillgelegten laden: Eine bereits zugeordnete, inzwischen
    // stillgelegte Anlage fehlte sonst in der Liste — sie bliebe angehakt
    // (`gewaehlt` kommt aus dem Vertrag), ließe sich aber nicht mehr abwählen.
    this.anlageSvc.list(d.property.id, true).subscribe({
      next: (liste) => {
        if (rid !== this.anlagenReq) return;
        this.anlagen.set(liste);
        this.anlagenLaedt.set(false);
      },
      error: () => {
        if (rid !== this.anlagenReq) return;
        this.anlagen.set([]);
        this.anlagenLaedt.set(false);
        this.anlagenFehler.set('Die Anlagen dieser Liegenschaft konnten nicht geladen werden.');
      },
    });
  }

  anlagenSchliessen(): void {
    if (!this.anlagenSpeichert()) this.anlagenDialog.set(false);
  }

  anlageUmschalten(id: string): void {
    this.gewaehlt.update((alt) => {
      const neu = new Set(alt);
      if (neu.has(id)) neu.delete(id);
      else neu.add(id);
      return neu;
    });
  }

  anlageGewaehlt(id: string): boolean {
    return this.gewaehlt().has(id);
  }

  anlagenSpeichern(): void {
    const d = this.daten();
    if (!d || this.anlagenSpeichert()) return;
    this.anlagenSpeichert.set(true);
    this.anlagenFehler.set(null);
    this.svc.setAssets(d.id, { asset_ids: [...this.gewaehlt()] }).subscribe({
      next: () => {
        this.anlagenSpeichert.set(false);
        this.anlagenDialog.set(false);
        this.meldung.set({ art: 'erfolg', text: 'Anlagen-Zuordnung gespeichert.' });
        this.load(d.id);
      },
      error: (err) => {
        this.anlagenSpeichert.set(false);
        this.anlagenFehler.set(
          fehlerDetail(err) ?? 'Die Zuordnung konnte nicht gespeichert werden.',
        );
      },
    });
  }

  /** Standort einer Anlage in einer Zeile — für die Auswahlliste. */
  anlageOrt(a: Anlage): string {
    return [a.building_label, a.unit_label, a.unit_storey, a.location_note]
      .filter(Boolean)
      .join(' · ');
  }

  // ---- Darstellungshelfer -------------------------------------------------
  statusLabel(s: ContractStatus): string {
    return contractStatusLabel(s);
  }
  statusClass(s: ContractStatus): string {
    return contractStatusClass(s);
  }
  intervalLabel(k: IntervalKind, days: number | null): string {
    return intervalKindLabel(k, days);
  }
  actionLabel(a: DueAction): string {
    return dueActionLabel(a);
  }
  d(iso: string | null): string {
    if (!iso) return '—';
    return this.dateFmt.format(new Date(iso));
  }
}
