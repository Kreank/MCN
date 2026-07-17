import { Component, computed, inject, signal } from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Observable } from 'rxjs';

import { AuthService } from '../../core/auth.service';
import { DossierService } from '../../core/dossier.service';
import {
  AuftragDossier,
  DossierTyp,
  KontaktDossier,
  LiegenschaftDossier,
  ProjektDossier,
  anzahlRechnungen,
  dossierAdressart,
  dossierAufgabeStatus,
  dossierBelegStatus,
  dossierEinheitTyp,
  dossierKanal,
  dossierKontaktart,
  dossierLiegenschaftTyp,
  dossierPrioritaet,
  dossierProjektStatus,
  dossierRichtung,
  dossierRolle,
  dossierStammStatus,
  dossierStammStatusClass,
  dossierVerantwortung,
  dossierVorgangStatus,
} from '../../core/dossier.model';
import {
  WorkOrderStatus,
  workOrderStatusClass,
  workOrderStatusLabel,
} from '../../core/auftrag.model';
import {
  ServiceJobStatus,
  serviceJobStatusClass,
  serviceJobStatusLabelStr,
} from '../../core/einsatz.model';
import {
  SiteReportStatus,
  siteReportStatusClass,
  siteReportStatusLabel,
  SollIstArt,
  sollIstArtClass,
  sollIstArtLabel,
  sollIstArtSymbol,
} from '../../core/site-report.model';
import {
  PaymentStatus,
  paymentStatusClass,
  paymentStatusLabel,
  invoiceTypeLabel,
} from '../../core/buchhaltung.model';
import {
  FaelligkeitArt,
  FaelligkeitStatus,
  artLabel as faelligkeitArtLabel,
  statusLabel as faelligkeitStatusLabel,
} from '../../core/faelligkeit.model';
import {
  ContractStatus,
  IntervalKind,
  contractStatusClass,
  contractStatusLabel,
  dueActionLabel,
  intervalKindLabel,
} from '../../core/wartung.model';
import { DueAction } from '../../core/wartung.model';
import { AssetType, artLabel as anlageArtLabel } from '../../core/anlage.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { MargeBlock } from '../../shared/marge-block/marge-block';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';
import { apiZuDeAnzeige } from '../../shared/formular/dezimal';
import { isoDatumDe } from '../../shared/datum';

type Daten =
  | { typ: 'kontakt'; d: KontaktDossier }
  | { typ: 'liegenschaft'; d: LiegenschaftDossier }
  | { typ: 'projekt'; d: ProjektDossier }
  | { typ: 'auftrag'; d: AuftragDossier };

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; daten: Daten }
  | VerbotenState
  | { kind: 'error'; text: string };

interface Abschnitt {
  id: string;
  label: string;
}

const TYPEN: DossierTyp[] = ['kontakt', 'liegenschaft', 'projekt', 'auftrag'];

/** Abschnitte je Dossier-Art — GESPERRTE Abschnitte stehen mit drin. */
const ABSCHNITTE: Record<DossierTyp, Abschnitt[]> = {
  kontakt: [
    { id: 'stamm', label: 'Stammdaten' },
    { id: 'adressen', label: 'Adressen & Kontaktwege' },
    { id: 'ansprech', label: 'Ansprechpartner' },
    { id: 'liegenschaften', label: 'Liegenschaften' },
    { id: 'workflow', label: 'Vorgänge & Aufträge' },
    { id: 'aufgaben', label: 'Aufgaben' },
    { id: 'posten', label: 'Offene Posten' },
    { id: 'zahlung', label: 'Zahlungsverhalten' },
    { id: 'kommunikation', label: 'Kommunikation' },
    { id: 'dokumente', label: 'Dokumente' },
  ],
  liegenschaft: [
    { id: 'stamm', label: 'Stammdaten' },
    { id: 'struktur', label: 'Gebäude & Einheiten' },
    { id: 'anlagen', label: 'Anlagen' },
    { id: 'beteiligte', label: 'Beteiligte' },
    { id: 'workflow', label: 'Vorgänge, Aufträge & Einsätze' },
    { id: 'zutritt', label: 'Zutrittshinweise' },
    { id: 'wartung', label: 'Wartung & Fristen' },
    { id: 'posten', label: 'Offene Posten' },
    { id: 'dokumente', label: 'Dokumente' },
  ],
  projekt: [
    { id: 'stamm', label: 'Stammdaten' },
    { id: 'liegenschaften', label: 'Liegenschaften' },
    { id: 'workflow', label: 'Vorgänge & Aufträge' },
    { id: 'aufgaben', label: 'Aufgaben' },
    { id: 'checklisten', label: 'Checklisten' },
    { id: 'logbuch', label: 'Logbuch' },
    { id: 'belege', label: 'Angebote & Rechnungen' },
    { id: 'posten', label: 'Offene Posten' },
    { id: 'marge', label: 'Deckungsbeitrag' },
    { id: 'dokumente', label: 'Dokumente' },
  ],
  auftrag: [
    { id: 'stamm', label: 'Stammdaten & Status' },
    { id: 'beteiligte', label: 'Beteiligte' },
    { id: 'einsaetze', label: 'Einsätze' },
    { id: 'zeiten', label: 'Zeiten' },
    { id: 'material', label: 'Material' },
    { id: 'berichte', label: 'Baustellenberichte' },
    { id: 'sollist', label: 'Soll-Ist' },
    { id: 'abrechnung', label: 'Abrechnungsstand' },
    { id: 'belege', label: 'Angebote & Rechnungen' },
    { id: 'posten', label: 'Offene Posten' },
    { id: 'dokumente', label: 'Dokumente' },
  ],
};

const KICKER: Record<DossierTyp, string> = {
  kontakt: 'Dossier · Kontakt',
  liegenschaft: 'Dossier · Liegenschaft',
  projekt: 'Dossier · Projekt',
  auftrag: 'Dossier · Auftrag',
};

/**
 * Das **Entitäts-Dossier**: alles zu EINER Entität auf EINER Seite, in einem
 * einzigen Serveraufruf (`/api/dossier/…`). Vier Arten (Kontakt, Liegenschaft,
 * Projekt, Auftrag), eine Ansicht.
 *
 * ## Warum das keine Mappe mit Reitern ist
 * Die Mappe fragt „welchen Teil willst du sehen?". Das Dossier beantwortet
 * „was weiß das System über dieses Objekt?" — und zwar **vollständig und auf
 * einmal**. Reiter würden genau das verstecken, was hier der Zweck ist. Statt
 * dessen: durchlaufende, überschriebene Abschnitte plus ein Sprungmarken-Index
 * (echte Anker, tastaturbedienbar; das Ziel bekommt den Fokus).
 *
 * ## Die zwei Doktrinen, die diese Ansicht durchsetzt
 *
 * **1. Ein gesperrter Baustein wird BENANNT, nie weggelassen — und niemals als
 * leer oder 0 dargestellt.** Fehlt das Modulrecht, sagt die Seite es im
 * Klartext („Nicht sichtbar — dafür fehlt das Recht invoicing/LESEN"). Ein
 * ausgeblendeter Abschnitt wäre von „es gibt nichts" nicht zu unterscheiden;
 * eine 0,00 € wäre eine glatte Lüge über den Kunden.
 *
 * **2. Ein fehlender WERT heißt „unbekannt", nie 0.** Laufende Zeitbuchung ohne
 * Ende → Dauer unbekannt. Keine bezahlte Rechnung → Zahlungsverzug unbekannt,
 * nicht „0 Tage" (das hieße „zahlt pünktlich"). Fehlender EK → Marge unbekannt,
 * nicht 0 %. Geld und Mengen bleiben durchgehend **Decimal-Strings**; `Number()`
 * fällt ausschließlich in den Anzeigehelfern unten und nirgends sonst.
 *
 * Gerechnet wird **nichts**: Summen, Soll-Ist und Marge kommen fertig vom
 * Server (dieselben Rechenstellen wie in Auftragsmappe und Auswertungen).
 */
@Component({
  selector: 'app-dossier',
  imports: [RouterLink, NgTemplateOutlet, KeinZugriff, MargeBlock],
  templateUrl: './dossier.html',
  styleUrl: './dossier.scss',
})
export class Dossier {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(DossierService);
  private readonly auth = inject(AuthService);

  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;
  private typ: DossierTyp | null = null;
  private id = '';

  /** Rechte für die weiterführenden Links (der Server tort ohnehin). */
  // `darfAlle`: Die Beleg-Links des Dossiers führen auf `/rechnungen/:id` — eine
  // Route hinter `darfAlleGuard('invoicing','LESEN')`, weil die Rechnungsmappe
  // fail-closed ist. Der Monteur trägt `invoicing/LESEN` nur mit EIGENE (Angebot
  // ohne Preise); ein Link, der ihn auf „Kein Zugriff" führt, ist ein toter Knopf.
  protected readonly darfBelege = computed(() => this.auth.darfAlle('invoicing', 'LESEN'));
  protected readonly darfWorkflow = computed(() => this.auth.darf('workflow', 'LESEN'));
  protected readonly darfProperty = computed(() => this.auth.darf('property', 'LESEN'));
  protected readonly darfIdentity = computed(() => this.auth.darf('identity', 'LESEN'));

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.daten : null;
  });

  /** Anlagenart als deutsches Label (statt des rohen Codes THERME_HEIZUNG …). */
  protected anlagenArt(t: string | null): string {
    return anlageArtLabel(t as AssetType | null);
  }

  protected readonly abschnitte = computed<Abschnitt[]>(() => {
    const d = this.daten();
    return d ? ABSCHNITTE[d.typ] : [];
  });

  protected readonly kicker = computed(() => {
    const d = this.daten();
    return d ? KICKER[d.typ] : 'Dossier';
  });

  protected readonly titel = computed(() => {
    const d = this.daten();
    if (!d) return 'Dossier';
    switch (d.typ) {
      case 'kontakt':
        return d.d.kontakt.display_name;
      case 'liegenschaft':
        return d.d.liegenschaft.name;
      case 'projekt':
        return d.d.projekt.name;
      case 'auftrag':
        return d.d.auftrag.title;
    }
  });

  /** Zurück zur klassischen Mappe derselben Entität. */
  protected readonly backLink = computed(() => {
    const d = this.daten();
    if (!d) return '/uebersicht';
    const pfad: Record<DossierTyp, string> = {
      kontakt: '/kontakte',
      liegenschaft: '/liegenschaften',
      projekt: '/projekte',
      auftrag: '/auftraege',
    };
    return `${pfad[d.typ]}/${this.id}`;
  });

  protected readonly backLabel = computed(() => {
    const d = this.daten();
    if (!d) return 'Zurück';
    const label: Record<DossierTyp, string> = {
      kontakt: 'Kontaktmappe',
      liegenschaft: 'Liegenschaftsmappe',
      projekt: 'Projektmappe',
      auftrag: 'Auftragsmappe',
    };
    return label[d.typ];
  });

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((p) => {
      const typ = (p.get('typ') ?? '') as DossierTyp;
      const id = p.get('id') ?? '';
      if (!TYPEN.includes(typ) || !id) {
        this.state.set({ kind: 'error', text: 'Diese Dossier-Art gibt es nicht.' });
        return;
      }
      this.typ = typ;
      this.id = id;
      this.laden();
    });
  }

  protected laden(): void {
    const typ = this.typ;
    if (!typ) return;
    const rid = ++this.reqId;
    this.state.set({ kind: 'loading' });

    const quellen: Record<DossierTyp, () => Observable<unknown>> = {
      kontakt: () => this.svc.kontakt(this.id),
      liegenschaft: () => this.svc.liegenschaft(this.id),
      projekt: () => this.svc.projekt(this.id),
      auftrag: () => this.svc.auftrag(this.id),
    };

    quellen[typ]().subscribe({
      next: (d) => {
        if (rid !== this.reqId) return;
        this.state.set({ kind: 'ready', daten: { typ, d } as Daten });
      },
      error: (err) => {
        if (rid !== this.reqId) return;
        const s = fehlerState(err);
        this.state.set(
          s.kind === 'forbidden'
            ? s
            : { kind: 'error', text: 'Das Dossier konnte nicht geladen werden.' },
        );
      },
    });
  }

  // --- Typsichere Sichten für das Template (kein $any) ----------------------
  protected kontakt(): KontaktDossier | null {
    const d = this.daten();
    return d?.typ === 'kontakt' ? d.d : null;
  }
  protected liegenschaft(): LiegenschaftDossier | null {
    const d = this.daten();
    return d?.typ === 'liegenschaft' ? d.d : null;
  }
  protected projekt(): ProjektDossier | null {
    const d = this.daten();
    return d?.typ === 'projekt' ? d.d : null;
  }
  protected auftrag(): AuftragDossier | null {
    const d = this.daten();
    return d?.typ === 'auftrag' ? d.d : null;
  }
  protected verboten(): string | null {
    const s = this.state();
    return s.kind === 'forbidden' ? s.detail : null;
  }
  protected fehlerText(): string {
    const s = this.state();
    return s.kind === 'error' ? s.text : '';
  }

  /**
   * Sprungmarke: echter Anker (`href="#…"`), damit Link-Semantik und
   * Kontextmenü stimmen. Der Standardsprung wird abgefangen, weil der Router
   * sonst die Route verlässt; Ziel wird fokussiert (Tastatur folgt dem Sprung)
   * und respektiert `prefers-reduced-motion`.
   */
  protected springe(event: Event, id: string): void {
    event.preventDefault();
    const ziel = document.getElementById(`ds-${id}`);
    if (!ziel) return;
    const ruhig = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
    ziel.scrollIntoView({ behavior: ruhig ? 'auto' : 'smooth', block: 'start' });
    ziel.focus({ preventScroll: true });
  }

  // --- Anzeigehelfer: hier und NUR hier wird ein Decimal-String zur Zahl ----

  /** Geld. `null` → „unbekannt" (nie 0,00 €). */
  protected euro(wert: string | null | undefined): string {
    if (wert === null || wert === undefined) return 'unbekannt';
    return new Intl.NumberFormat('de-DE', { style: 'currency', currency: 'EUR' }).format(
      Number(wert),
    );
  }

  /** Menge/Anzahl (Decimal-String) in deutscher Anzeigeform, `null` → „unbekannt". */
  protected menge(wert: string | null | undefined): string {
    if (wert === null || wert === undefined) return 'unbekannt';
    return apiZuDeAnzeige(wert) || 'unbekannt';
  }

  /** Stunden. Laufende Buchung ohne Ende → „unbekannt", nie „0,0 h". */
  protected stunden(wert: string | null | undefined): string {
    if (wert === null || wert === undefined) return 'unbekannt';
    return `${apiZuDeAnzeige(wert, 2)} h`;
  }

  protected datum(iso: string | null | undefined): string {
    return iso ? isoDatumDe(iso) : '—';
  }

  protected zeitpunkt(iso: string | null | undefined): string {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return new Intl.DateTimeFormat('de-DE', {
      dateStyle: 'short',
      timeStyle: 'short',
    }).format(d);
  }

  protected bytes(n: number): string {
    if (n < 1024) return `${n} B`;
    const kb = n / 1024;
    if (kb < 1024) return `${new Intl.NumberFormat('de-DE', { maximumFractionDigits: 0 }).format(kb)} KB`;
    return `${new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1 }).format(kb / 1024)} MB`;
  }

  /** Tage Verzug: `null` heißt **unbekannt** (nie „0 Tage" = „zahlt pünktlich"). */
  protected tage(n: number | null): string {
    if (n === null) return 'unbekannt';
    const de = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1 }).format(n);
    return `${de} ${Math.abs(n) === 1 ? 'Tag' : 'Tage'}`;
  }

  protected text(w: string | null | undefined): string {
    return w && w.trim() ? w : '—';
  }

  // --- Labels (Status immer als TEXT, nie nur als Farbe) --------------------
  // Die Dossier-Schemata führen die Enums als freie Strings (sie stammen aus
  // mehreren Modulen). Die Label-Helfer der Fachmodule bleiben die EINZIGE
  // Quelle der Beschriftungen — hier nur die Brücke; ein unbekannter Code fällt
  // in allen Helfern auf sich selbst zurück, nie auf einen erfundenen Text.
  protected vorgangStatus = dossierVorgangStatus;
  protected aufgabeStatus = dossierAufgabeStatus;
  protected projektStatus = dossierProjektStatus;
  protected belegStatus = dossierBelegStatus;
  protected prioritaet = dossierPrioritaet;
  protected rolle = dossierRolle;
  protected adressart = dossierAdressart;
  protected kontaktart = dossierKontaktart;
  protected kanal = dossierKanal;
  protected richtung = dossierRichtung;
  protected verantwortung = dossierVerantwortung;
  protected liegenschaftTyp = dossierLiegenschaftTyp;
  protected einheitTyp = dossierEinheitTyp;
  protected stammStatus = dossierStammStatus;
  protected stammStatusClass = dossierStammStatusClass;
  protected anzahlRechnungen = anzahlRechnungen;

  protected auftragStatus(s: string): string {
    return workOrderStatusLabel(s as WorkOrderStatus);
  }
  protected auftragStatusClass(s: string): string {
    return workOrderStatusClass(s as WorkOrderStatus);
  }
  protected einsatzStatus(s: string): string {
    return serviceJobStatusLabelStr(s);
  }
  protected einsatzStatusClass(s: string): string {
    return serviceJobStatusClass(s as ServiceJobStatus);
  }
  protected berichtStatus(s: string): string {
    return siteReportStatusLabel(s as SiteReportStatus);
  }
  protected berichtStatusClass(s: string): string {
    return siteReportStatusClass(s as SiteReportStatus);
  }
  protected sollIstLabel(a: string): string {
    return sollIstArtLabel(a as SollIstArt);
  }
  protected sollIstSymbol(a: string): string {
    return sollIstArtSymbol(a as SollIstArt);
  }
  protected sollIstClass(a: string): string {
    return sollIstArtClass(a as SollIstArt);
  }
  protected zahlStatus(s: string): string {
    return paymentStatusLabel(s as PaymentStatus);
  }
  protected zahlStatusClass(s: string): string {
    return paymentStatusClass(s as PaymentStatus);
  }
  protected belegArt(t: string): string {
    return invoiceTypeLabel(t);
  }
  protected fristArt(a: string): string {
    return faelligkeitArtLabel(a as FaelligkeitArt);
  }
  protected fristStatus(s: string): string {
    return faelligkeitStatusLabel(s as FaelligkeitStatus);
  }
  protected vertragStatus(s: string): string {
    return contractStatusLabel(s as ContractStatus);
  }
  protected vertragStatusClass(s: string): string {
    return contractStatusClass(s as ContractStatus);
  }
  protected intervall(k: string): string {
    return intervalKindLabel(k as IntervalKind, null);
  }
  protected folgeaktion(a: string): string {
    return dueActionLabel(a as DueAction);
  }

  protected abrechnungsart(m: string): string {
    return m === 'REGIE' ? 'Regie (nach Aufwand)' : m === 'PAUSCHAL' ? 'Pauschal' : m;
  }
}
