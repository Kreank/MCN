import { Component, computed, inject, signal } from '@angular/core';
import { AuthService } from '../../core/auth.service';
import { FreigabenService } from '../../core/freigaben.service';
import {
  Approval,
  ApprovalStatus,
  approvalStatusClass,
  approvalStatusLabel,
  payloadLabel,
  payloadWert,
} from '../../core/freigaben.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import {
  VerbotenState,
  fehlerDetail,
  fehlerState,
  istVerboten,
} from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: Approval[] }
  | VerbotenState
  | { kind: 'error' };

type Segment = { value: ApprovalStatus | null; label: string };

/** Art der laufenden Entscheidung — steuert den Bestätigungsdialog. */
type AktionTyp = 'approve' | 'reject' | 'withdraw';

type Meldung = { art: 'erfolg' | 'fehler'; text: string };

/** Ein Payload-Eintrag für die lesbare Schlüssel/Wert-Liste. */
interface PayloadEintrag {
  label: string;
  wert: string;
}

@Component({
  selector: 'app-freigaben',
  imports: [KeinZugriff, Bestaetigung],
  templateUrl: './freigaben.html',
  styleUrl: './freigaben.scss',
})
export class Freigaben {
  private readonly svc = inject(FreigabenService);
  private readonly auth = inject(AuthService);

  protected readonly segments: Segment[] = [
    { value: 'ANGEFORDERT', label: 'Offen' },
    { value: 'GENEHMIGT', label: 'Genehmigt' },
    { value: 'ABGELEHNT', label: 'Abgelehnt' },
    { value: 'ZURUECKGEZOGEN', label: 'Zurückgezogen' },
    { value: null, label: 'Alle' },
  ];

  // Standard: die offenen (angeforderten) Anträge — sie brauchen eine Entscheidung.
  protected readonly status = signal<ApprovalStatus | null>('ANGEFORDERT');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly meldung = signal<Meldung | null>(null);

  protected readonly skeletons = Array.from({ length: 4 });

  // --- Rechte-Tore (nur UI-Ausblendung; der Server setzt sie ohnehin durch) --
  protected readonly darfEntscheiden = computed(() => this.auth.darf('security', 'FREIGEBEN'));
  protected readonly darfAnlegen = computed(() => this.auth.darf('security', 'ANLEGEN'));

  // --- Bestätigungsdialog für eine Entscheidung ----------------------------
  protected readonly aktion = signal<{ typ: AktionTyp; ap: Approval } | null>(null);
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
    if (s.kind === 'loading') return 'Freigabeanträge werden geladen.';
    if (s.kind === 'forbidden') return 'Keine Berechtigung für die Freigaben.';
    if (s.kind === 'error') return 'Freigabeanträge konnten nicht geladen werden.';
    const t = s.data.length;
    if (t === 0) return 'Keine Anträge für diese Auswahl.';
    return `${t} ${t === 1 ? 'Antrag' : 'Anträge'} für diese Auswahl.`;
  });

  // Dialog-Beschriftung je Aktionsart.
  protected readonly dialogTitel = computed(() => {
    switch (this.aktion()?.typ) {
      case 'approve':
        return 'Antrag genehmigen?';
      case 'reject':
        return 'Antrag ablehnen?';
      case 'withdraw':
        return 'Antrag zurückziehen?';
      default:
        return 'Aktion bestätigen';
    }
  });
  protected readonly dialogText = computed(() => {
    const a = this.aktion();
    if (!a) return '';
    switch (a.typ) {
      case 'approve':
        return `„${a.ap.action_label}" wird freigegeben und die beantragte Änderung angewendet.`;
      case 'reject':
        return `„${a.ap.action_label}" wird abgelehnt. Bitte begründe die Ablehnung.`;
      case 'withdraw':
        return `Dein Antrag „${a.ap.action_label}" wird zurückgezogen.`;
      default:
        return '';
    }
  });
  protected readonly dialogLabel = computed(() => {
    switch (this.aktion()?.typ) {
      case 'approve':
        return 'Genehmigen';
      case 'reject':
        return 'Ablehnen';
      case 'withdraw':
        return 'Zurückziehen';
      default:
        return 'Bestätigen';
    }
  });
  protected readonly dialogGefahr = computed(() => this.aktion()?.typ !== 'approve');
  protected readonly dialogBegruendung = computed(() => this.aktion()?.typ === 'reject');

  constructor() {
    this.fetch();
  }

  // ---- Liste laden --------------------------------------------------------
  selectSegment(value: ApprovalStatus | null): void {
    if (this.status() === value) return;
    this.status.set(value);
    this.meldung.set(null);
    this.fetch();
  }

  retry(): void {
    this.fetch();
  }

  private fetch(): void {
    const id = ++this.reqId;
    this.state.set({ kind: 'loading' });
    this.svc.list(this.status()).subscribe({
      next: (data) => {
        if (id === this.reqId) this.state.set({ kind: 'ready', data });
      },
      error: (err) => {
        if (id === this.reqId) this.state.set(fehlerState(err));
      },
    });
  }

  // ---- Entscheidungen -----------------------------------------------------
  /** Genehmigen/Ablehnen — nur mit Recht FREIGEBEN und offenem Antrag. */
  darfEntscheidenUeber(a: Approval): boolean {
    return this.darfEntscheiden() && a.status === 'ANGEFORDERT';
  }

  /** Zurückziehen — nur mit Recht ANLEGEN, offen UND der EIGENE Antrag. */
  darfZurueckziehen(a: Approval): boolean {
    return (
      this.darfAnlegen() &&
      a.status === 'ANGEFORDERT' &&
      a.requested_by === this.auth.user()?.app_user_id
    );
  }

  aktionOeffnen(typ: AktionTyp, ap: Approval): void {
    this.meldung.set(null);
    this.aktion.set({ typ, ap });
  }

  aktionAbbrechen(): void {
    if (!this.aktionLaedt()) this.aktion.set(null);
  }

  aktionBestaetigen(begruendung: string | null): void {
    const a = this.aktion();
    if (!a || this.aktionLaedt()) return;

    const obs =
      a.typ === 'approve'
        ? this.svc.approve(a.ap.id)
        : a.typ === 'reject'
          ? this.svc.reject(a.ap.id, begruendung ?? '')
          : this.svc.withdraw(a.ap.id);

    const erfolgText =
      a.typ === 'approve'
        ? 'Antrag genehmigt.'
        : a.typ === 'reject'
          ? 'Antrag abgelehnt.'
          : 'Antrag zurückgezogen.';

    this.aktionLaedt.set(true);
    obs.subscribe({
      next: (aktualisiert) => {
        this.aktionLaedt.set(false);
        this.aktion.set(null);
        this.applyResult(aktualisiert);
        this.meldung.set({ art: 'erfolg', text: erfolgText });
      },
      error: (err) => {
        this.aktionLaedt.set(false);
        this.aktion.set(null);
        this.meldung.set({ art: 'fehler', text: this.fehlerText(err) });
      },
    });
  }

  /**
   * Aktualisiert die Liste nach einer Aktion: den Eintrag ersetzen und, falls
   * ein Statusfilter aktiv ist und der neue Status nicht mehr passt, entfernen.
   * Kein erneuter Ladevorgang (kein Skelett-Flackern).
   */
  private applyResult(updated: Approval): void {
    const s = this.state();
    if (s.kind !== 'ready') return;
    const filter = this.status();
    let items = s.data.map((a) => (a.id === updated.id ? updated : a));
    if (filter !== null && updated.status !== filter) {
      items = items.filter((a) => a.id !== updated.id);
    }
    this.state.set({ kind: 'ready', data: items });
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  private fehlerText(err: unknown): string {
    if (istVerboten(err)) return fehlerDetail(err) ?? 'Keine Berechtigung für diese Aktion.';
    return fehlerDetail(err) ?? 'Die Aktion ist fehlgeschlagen. Bitte erneut versuchen.';
  }

  // ---- Darstellungshelfer -------------------------------------------------
  statusLabel(s: ApprovalStatus): string {
    return approvalStatusLabel(s);
  }
  statusClass(s: ApprovalStatus): string {
    return approvalStatusClass(s);
  }

  /** Payload als lesbare Schlüssel/Wert-Liste (nicht rohes JSON). */
  payloadEintraege(a: Approval): PayloadEintrag[] {
    return Object.entries(a.payload ?? {}).map(([key, value]) => ({
      label: payloadLabel(key),
      wert: payloadWert(value),
    }));
  }

  d(iso: string | null): string {
    if (!iso) return '—';
    return this.dateFmt.format(new Date(iso));
  }
}
