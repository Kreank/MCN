import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { AuthService } from '../../core/auth.service';
import { KiService } from '../../core/ki.service';
import {
  KiVorschlag,
  KiVorschlagDetail,
  ProposalStatus,
  proposalStatusClass,
  proposalStatusLabel,
  proposalTypLabel,
  zeileTypLabel,
} from '../../core/ki.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { VerbotenState, fehlerDetail, fehlerState, istVerboten } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: KiVorschlag[] }
  | VerbotenState
  | { kind: 'error' };

type Segment = { value: ProposalStatus; label: string };

/** Art der laufenden Entscheidung — steuert den Bestätigungsdialog. */
type AktionTyp = 'annehmen' | 'ablehnen' | 'loeschen';

type Meldung = {
  art: 'erfolg' | 'fehler';
  text: string;
  link?: { label: string; path: string };
};

/** Aufgeklappte Detailansicht eines Vorschlags (Entwurf wird bei Bedarf geladen). */
type DetailState =
  | { id: string; kind: 'loading' }
  | { id: string; kind: 'ready'; data: KiVorschlagDetail }
  | { id: string; kind: 'error' };

@Component({
  selector: 'app-ki-vorschlaege',
  imports: [RouterLink, KeinZugriff, Bestaetigung],
  templateUrl: './ki-vorschlaege.html',
  styleUrl: './ki-vorschlaege.scss',
})
export class KiVorschlaege {
  private readonly svc = inject(KiService);
  private readonly auth = inject(AuthService);

  protected readonly segments: Segment[] = [
    { value: 'PENDING', label: 'Offen' },
    { value: 'APPROVED', label: 'Angenommen' },
    { value: 'REJECTED', label: 'Abgelehnt' },
    { value: 'EXPIRED', label: 'Abgelaufen' },
  ];

  protected readonly status = signal<ProposalStatus>('PENDING');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly meldung = signal<Meldung | null>(null);
  protected readonly detail = signal<DetailState | null>(null);

  protected readonly skeletons = Array.from({ length: 3 });

  // --- Rechte-Tore (nur UI-Ausblendung; der Server tort ohnehin fail-closed) --
  // Annehmen materialisiert einen Bericht + Positionen: dieselben Rechte wie die
  // manuelle Anlage (workflow/ANLEGEN + AENDERN). `darfAlle`, weil der Server auf
  // diesen Endpunkten `require` (fail-closed) nutzt — Scope EIGENE ⇒ 403.
  protected readonly darfAnnehmen = computed(
    () => this.auth.darfAlle('workflow', 'ANLEGEN') && this.auth.darfAlle('workflow', 'AENDERN'),
  );
  protected readonly darfAendern = computed(() => this.auth.darfAlle('workflow', 'AENDERN'));

  // --- Bestätigungsdialog --------------------------------------------------
  protected readonly aktion = signal<{ typ: AktionTyp; v: KiVorschlag } | null>(null);
  protected readonly aktionLaedt = signal(false);

  private reqId = 0;

  private readonly dateFmt = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });

  protected readonly resultSummary = computed(() => {
    const s = this.state();
    if (s.kind === 'loading') return 'KI-Vorschläge werden geladen.';
    if (s.kind === 'forbidden') return 'Keine Berechtigung für die KI-Vorschläge.';
    if (s.kind === 'error') return 'KI-Vorschläge konnten nicht geladen werden.';
    const t = s.data.length;
    if (t === 0) return 'Keine Vorschläge für diese Auswahl.';
    return `${t} ${t === 1 ? 'Vorschlag' : 'Vorschläge'} für diese Auswahl.`;
  });

  protected readonly dialogTitel = computed(() => {
    switch (this.aktion()?.typ) {
      case 'annehmen':
        return 'Vorschlag annehmen?';
      case 'ablehnen':
        return 'Vorschlag ablehnen?';
      case 'loeschen':
        return 'Vorschlag löschen?';
      default:
        return 'Aktion bestätigen';
    }
  });
  protected readonly dialogText = computed(() => {
    switch (this.aktion()?.typ) {
      case 'annehmen':
        return 'Aus dem Entwurf entsteht ein Einsatzbericht im Entwurf, den du danach prüfst, korrigierst und unterschreiben lässt. Preise führt der Bericht nicht.';
      case 'ablehnen':
        return 'Der Vorschlag wird abgelehnt. Bitte begründe die Ablehnung.';
      case 'loeschen':
        return 'Der Entwurf wird endgültig gelöscht (DSGVO). Das lässt sich nicht rückgängig machen.';
      default:
        return '';
    }
  });
  protected readonly dialogLabel = computed(() => {
    switch (this.aktion()?.typ) {
      case 'annehmen':
        return 'Annehmen';
      case 'ablehnen':
        return 'Ablehnen';
      case 'loeschen':
        return 'Löschen';
      default:
        return 'Bestätigen';
    }
  });
  protected readonly dialogGefahr = computed(() => this.aktion()?.typ !== 'annehmen');
  protected readonly dialogBegruendung = computed(() => this.aktion()?.typ === 'ablehnen');

  constructor() {
    this.fetch();
  }

  // ---- Liste laden --------------------------------------------------------
  selectSegment(value: ProposalStatus): void {
    if (this.status() === value) return;
    this.status.set(value);
    this.meldung.set(null);
    this.detail.set(null);
    this.fetch();
  }

  retry(): void {
    this.fetch();
  }

  private fetch(): void {
    const id = ++this.reqId;
    this.state.set({ kind: 'loading' });
    this.svc.vorschlaege(this.status()).subscribe({
      next: (data) => {
        if (id === this.reqId) this.state.set({ kind: 'ready', data });
      },
      error: (err) => {
        if (id === this.reqId) this.state.set(fehlerState(err));
      },
    });
  }

  // ---- Detail (Entwurf) auf-/zuklappen ------------------------------------
  detailOffen(v: KiVorschlag): boolean {
    return this.detail()?.id === v.id;
  }

  detailUmschalten(v: KiVorschlag): void {
    if (this.detail()?.id === v.id) {
      this.detail.set(null);
      return;
    }
    this.detail.set({ id: v.id, kind: 'loading' });
    this.svc.vorschlag(v.id).subscribe({
      next: (data) => {
        if (this.detail()?.id === v.id) this.detail.set({ id: v.id, kind: 'ready', data });
      },
      error: () => {
        if (this.detail()?.id === v.id) this.detail.set({ id: v.id, kind: 'error' });
      },
    });
  }

  // ---- Entscheidungen -----------------------------------------------------
  darfAnnehmenUeber(v: KiVorschlag): boolean {
    return this.darfAnnehmen() && v.status === 'PENDING';
  }
  darfAblehnenUeber(v: KiVorschlag): boolean {
    return this.darfAendern() && v.status === 'PENDING';
  }
  darfLoeschenUeber(v: KiVorschlag): boolean {
    return this.darfAendern() && (v.status === 'REJECTED' || v.status === 'EXPIRED');
  }

  aktionOeffnen(typ: AktionTyp, v: KiVorschlag): void {
    this.meldung.set(null);
    this.aktion.set({ typ, v });
  }

  aktionAbbrechen(): void {
    if (!this.aktionLaedt()) this.aktion.set(null);
  }

  aktionBestaetigen(begruendung: string | null): void {
    const a = this.aktion();
    if (!a || this.aktionLaedt()) return;
    this.aktionLaedt.set(true);

    if (a.typ === 'annehmen') {
      this.svc.annehmen(a.v.id).subscribe({
        next: (res) => {
          this.abschluss();
          this.entferne(a.v.id);
          this.meldung.set({
            art: 'erfolg',
            text: 'Vorschlag angenommen — ein Einsatzbericht (Entwurf) wurde erzeugt.',
            link: res.work_order_id
              ? { label: 'Zum Auftrag', path: `/auftraege/${res.work_order_id}` }
              : undefined,
          });
        },
        error: (err) => this.aktionFehler(err),
      });
      return;
    }

    if (a.typ === 'ablehnen') {
      this.svc.ablehnen(a.v.id, begruendung ?? '').subscribe({
        next: () => {
          this.abschluss();
          this.entferne(a.v.id);
          this.meldung.set({ art: 'erfolg', text: 'Vorschlag abgelehnt.' });
        },
        error: (err) => this.aktionFehler(err),
      });
      return;
    }

    // loeschen
    this.svc.loeschen(a.v.id).subscribe({
      next: () => {
        this.abschluss();
        this.entferne(a.v.id);
        this.meldung.set({ art: 'erfolg', text: 'Vorschlag gelöscht.' });
      },
      error: (err) => this.aktionFehler(err),
    });
  }

  private abschluss(): void {
    this.aktionLaedt.set(false);
    this.aktion.set(null);
  }

  private aktionFehler(err: unknown): void {
    this.abschluss();
    this.meldung.set({ art: 'fehler', text: this.fehlerText(err) });
  }

  /** Entfernt einen Eintrag aus der aktuellen Liste (er hat den Status gewechselt). */
  private entferne(id: string): void {
    const s = this.state();
    if (s.kind !== 'ready') return;
    if (this.detail()?.id === id) this.detail.set(null);
    this.state.set({ kind: 'ready', data: s.data.filter((v) => v.id !== id) });
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  private fehlerText(err: unknown): string {
    if (istVerboten(err)) return fehlerDetail(err) ?? 'Keine Berechtigung für diese Aktion.';
    return fehlerDetail(err) ?? 'Die Aktion ist fehlgeschlagen. Bitte erneut versuchen.';
  }

  // ---- Darstellungshelfer -------------------------------------------------
  statusLabel(s: ProposalStatus): string {
    return proposalStatusLabel(s);
  }
  statusClass(s: ProposalStatus): string {
    return proposalStatusClass(s);
  }
  typLabel(t: string): string {
    return proposalTypLabel(t);
  }
  zeileTyp(t: string): string {
    return zeileTypLabel(t);
  }

  d(iso: string | null): string {
    if (!iso) return '—';
    return this.dateFmt.format(new Date(iso));
  }
}
