import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { map } from 'rxjs';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { BelegService } from '../../core/beleg.service';
import { MailService } from '../../core/mail.service';
import { PropertyService } from '../../core/property.service';
import { ProjektService } from '../../core/projekt.service';
import { AuthService } from '../../core/auth.service';
import {
  LINE_TYPE_LABEL,
  LineType,
  QUOTE_STATUS_LABEL,
  QuoteAusgang,
  QuoteCopy,
  QuoteDetail,
  QuoteStatus,
} from '../../core/beleg.model';
import { AngebotMengen } from '../angebot-mengen/angebot-mengen';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { Dateien } from '../../shared/dateien/dateien';
import { ZielFilter } from '../../core/datei.model';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld } from '../../shared/formular/feld';
import { ReferenzWahl, RefSuche } from '../../shared/formular/referenz-wahl';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';
import { VerbotenState, fehlerDetail, fehlerState, istVerboten } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: QuoteDetail }
  | VerbotenState
  | { kind: 'error' };

type Meldung = { art: 'erfolg' | 'fehler'; text: string };

/** Angebotsstatus, ab denen das Angebot versendet (festgeschrieben) ist und ein
 *  finales PDF erhält. Vorher (Entwurfsphase) gibt es kein „finales" PDF. */
const VERSENDET_STATUS: readonly QuoteStatus[] = [
  'VERSENDET',
  'ANGENOMMEN',
  'ABGELEHNT',
  'ABGELAUFEN',
  'ERSETZT',
];

/** Der Ausgang eines versendeten Angebots — Texte des Bestätigungsdialogs.
 *
 *  Jeder Ausgang sagt, **was er bewirkt**: „Angenommen" ist die Grundlage des
 *  Auftrags; „Abgelehnt" nimmt das Angebot aus dem Soll der Baustelle (der
 *  Soll-Ist-Abgleich rechnet sich neu). Das ist keine Etikettenfrage.
 */
const AUSGANG_TITEL: Record<QuoteAusgang, string> = {
  ANGENOMMEN: 'Angebot als angenommen festhalten?',
  ABGELEHNT: 'Angebot als abgelehnt festhalten?',
  ABGELAUFEN: 'Angebot als abgelaufen festhalten?',
};
const AUSGANG_TEXT: Record<QuoteAusgang, string> = {
  ANGENOMMEN:
    'Der Kunde hat zugesagt: Das Angebot gilt als vereinbart und bleibt das Soll der Baustelle. Am Beleg selbst ändert sich nichts — Snapshot und Prüf-Hash des versendeten Angebots bleiben unangetastet.',
  ABGELEHNT:
    'Das Angebot wurde nicht beauftragt. Es bildet danach KEIN Soll mehr: Der Soll-Ist-Abgleich der zugehörigen Baustelle rechnet sich neu. Am Beleg selbst ändert sich nichts.',
  ABGELAUFEN:
    'Die Bindefrist ist verstrichen, ohne dass der Kunde entschieden hat. Das Angebot bleibt das Soll der Baustelle (angeboten wurde es ja so). Am Beleg selbst ändert sich nichts.',
};
const AUSGANG_LABEL: Record<QuoteAusgang, string> = {
  ANGENOMMEN: 'Als angenommen festhalten',
  ABGELEHNT: 'Als abgelehnt festhalten',
  ABGELAUFEN: 'Als abgelaufen festhalten',
};
const AUSGANG_ERFOLG: Record<QuoteAusgang, string> = {
  ANGENOMMEN: 'Angebot als angenommen festgehalten.',
  ABGELEHNT: 'Angebot als abgelehnt festgehalten — es bildet kein Soll mehr.',
  ABGELAUFEN: 'Angebot als abgelaufen festgehalten.',
};

/**
 * Status, aus denen der Server eine Rechnung erzeugt (`abrechnung.rechnung_aus_angebot`):
 * Das Angebot muss eine **Vereinbarung** sein — also NICHT in
 * `site_report.SOLL_AUSGESCHLOSSENE_STATUS` (ENTWURF/INTERN_GEPRUEFT/ABGELEHNT/ERSETZT).
 * Übrig bleiben genau diese vier. Ein Entwurf oder ein abgelehntes/ersetztes Angebot
 * ist keine Vereinbarung — dafür bleibt die Aktion aus (der Server antwortete sonst 422).
 */
const RECHNUNG_AUS_ANGEBOT_STATUS: readonly QuoteStatus[] = [
  'FREIGEGEBEN',
  'VERSENDET',
  'ANGENOMMEN',
  'ABGELAUFEN',
];

@Component({
  selector: 'app-beleg-detail',
  imports: [
    Mappe,
    RouterLink,
    KeinZugriff,
    Bestaetigung,
    Dateien,
    ReactiveFormsModule,
    Dialog,
    Feld,
    ReferenzWahl,
    AngebotMengen,
  ],
  templateUrl: './beleg-detail.html',
  styleUrl: './beleg-detail.scss',
})
export class BelegDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly svc = inject(BelegService);
  private readonly mailSvc = inject(MailService);
  private readonly propertySvc = inject(PropertyService);
  private readonly projektSvc = inject(ProjektService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  /** Liegenschaftssuche für den Kopie-Dialog (leer = Liegenschaft der Quelle). */
  protected readonly liegenschaftSuche: RefSuche = (q) =>
    this.propertySvc.list({ page: 1, page_size: 20, q }).pipe(
      map((p) =>
        p.items.map((o) => ({ id: o.id, label: o.name, sub: `${o.property_number} · ${o.city}` })),
      ),
    );
  /** Projektsuche für Kopieren (Zielprojekt) und Verschieben. */
  protected readonly projektSuche: RefSuche = (q) =>
    this.projektSvc.list({ page: 1, page_size: 20, q }).pipe(
      map((p) => p.items.map((o) => ({ id: o.id, label: o.name, sub: o.project_number }))),
    );

  /**
   * row_scope EIGENE auf `invoicing` (Monteur, Migration 0102) → **die Mengensicht**.
   *
   * Diese Mappe zeigt Einzelpreise, Summen und den Versand-Workflow; der Server
   * antwortet dem Monteur auf `GET /invoicing/quotes/{id}` deshalb mit **403**. Statt
   * ihn auf „Kein Zugriff" laufen zu lassen (er DARF das Angebot ja sehen — nur ohne
   * Preise), übernimmt hier die preisfreie Ansicht. Dieselbe Route, dieselben Links
   * aus Suche und Dossier — eine andere Komponente.
   *
   * **Der Preis wird nicht ausgeblendet, er wird nicht geladen**: Die Mengensicht
   * ruft einen eigenen Endpunkt auf, der keinen Betrag ausliefert. Ein Template, das
   * nur eine Spalte weglässt, hätte den Betrag trotzdem im Netzwerk-Tab.
   */
  protected readonly nurMengen = computed(
    () =>
      this.auth.darf('invoicing', 'LESEN') && !this.auth.darfAlle('invoicing', 'LESEN'),
  );

  protected readonly tab = signal('positionen');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  // --- Versenden (unumkehrbar) --------------------------------------------
  protected readonly darfVersenden = computed(() => this.auth.darf('invoicing', 'VERSENDEN'));
  protected readonly meldung = signal<Meldung | null>(null);
  protected readonly versendenOffen = signal(false);
  protected readonly versendenLaedt = signal(false);

  /** Versenden ist nur vor dem Versand sinnvoll (Server setzt die Tore durch). */
  protected readonly kannVersenden = computed(() => {
    const d = this.daten();
    if (!d) return false;
    return d.status === 'ENTWURF' || d.status === 'INTERN_GEPRUEFT' || d.status === 'FREIGEGEBEN';
  });

  /** Im Editor bearbeitbar (Positionen per Palette/Drag&Drop): dieselben Status
   * wie im Angebotseditor (EDITIERBAR) + Recht invoicing/AENDERN. Der „Bearbeiten"-
   * Knopf führt in den Angebotseditor (`/dokumente/angebot/:id`) — bisher war der
   * Editor aus dem UI gar nicht erreichbar. */
  protected readonly darfBearbeiten = computed(() => {
    const d = this.daten();
    if (!d || !this.auth.darf('invoicing', 'AENDERN')) return false;
    return d.status === 'ENTWURF' || d.status === 'INTERN_GEPRUEFT' || d.status === 'FREIGEGEBEN';
  });

  // --- Per E-Mail senden (nur versendetes Angebot) ------------------------
  /** Nur versendete Angebote lassen sich per Mail versenden (Server erzwingt es). */
  protected readonly kannPerMailSenden = computed(() => this.daten()?.status === 'VERSENDET');
  /** Ab welchem Status ein finales PDF existiert (für „PDF ansehen"). */
  protected readonly kannPdf = computed(() => {
    const s = this.daten()?.status;
    return s !== undefined && VERSENDET_STATUS.includes(s);
  });
  /** Ob ein Absenderkonto hinterlegt ist (null = noch nicht geladen). Der Server
   *  bleibt maßgeblich; das UI blendet die Aktion ohne Konto nur aus/deaktiviert. */
  protected readonly mailKontoVorhanden = signal<boolean | null>(null);
  protected readonly versandOffen = signal(false);
  protected readonly versandLaedt = signal(false);
  protected readonly versandMeldung = signal<string | null>(null);
  protected readonly versandForm = this.fb.group({
    to_address: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.email],
    }),
  });

  protected readonly tabs: MappeTab[] = [
    { id: 'positionen', label: 'Positionen' },
    { id: 'uebersicht', label: 'Übersicht' },
    { id: 'dateien', label: 'Dateien' },
  ];

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  /**
   * Zielreferenz fuer den Dateien-Tab. Diese Mappe zeigt ausschliesslich
   * Angebote (invoicing.quote) — daher `quote_id`. Rechnungen haben eine eigene
   * Mappe (rechnung-detail). Stabile Referenz (nur bei Belegwechsel neu).
   */
  protected readonly dateienZiel = computed<ZielFilter>(() => ({
    quote_id: this.daten()?.id ?? '',
  }));

  constructor() {
    // Mengensicht: NICHTS von hier laden. Der preisführende Endpunkt würde 403
    // antworten, und der Fehlerzustand dieser Mappe würde die Kindkomponente
    // überdecken, die gerade sauber lädt.
    if (this.nurMengen()) return;

    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.tab.set('positionen');
      if (!id) {
        this.state.set({ kind: 'error' });
        return;
      }
      this.load(id);
    });

    // Ob ein Absenderkonto konfiguriert ist, entscheidet über die Versand-Aktion.
    // Nur laden, wenn die Rolle überhaupt versenden darf.
    if (this.darfVersenden()) {
      this.mailSvc.getAccount().subscribe({
        next: (k) => this.mailKontoVorhanden.set(k.exists),
        error: () => this.mailKontoVorhanden.set(false),
      });
    }
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

  // ---- Versenden ----------------------------------------------------------
  versendenFragen(): void {
    this.meldung.set(null);
    this.versendenOffen.set(true);
  }

  versendenAbbrechen(): void {
    if (!this.versendenLaedt()) this.versendenOffen.set(false);
  }

  versendenBestaetigen(): void {
    const d = this.daten();
    if (!d || this.versendenLaedt()) return;
    this.versendenLaedt.set(true);
    this.svc.sendQuote(d.id).subscribe({
      next: (aktualisiert) => {
        this.versendenLaedt.set(false);
        this.versendenOffen.set(false);
        this.state.set({ kind: 'ready', data: aktualisiert });
        this.meldung.set({
          art: 'erfolg',
          text: `Angebot versendet. Belegnummer ${aktualisiert.quote_number ?? '—'} wurde vergeben.`,
        });
      },
      error: (err) => {
        this.versendenLaedt.set(false);
        this.versendenOffen.set(false);
        this.meldung.set({ art: 'fehler', text: this.fehlerText(err) });
      },
    });
  }

  // --- Der Ausgang des Angebots: angenommen | abgelehnt | abgelaufen -------
  //
  // Der Statusautomat kennt diese Übergänge seit Migration 0016 — **gesetzt hat
  // sie nie ein Produktpfad**. Ein Angebot blieb für immer „versendet", auch wenn
  // der Kunde längst zugesagt hatte.
  //
  // **ERSETZT gibt es hier bewusst nicht**: Der Status verlangt ein
  // Nachfolgeangebot (DB-Regel) und ist damit der Vorgang „Ersatzangebot anlegen",
  // kein Statuswechsel. Ein Knopf, der zuverlässig an einem CHECK scheitert, wäre
  // ein Versprechen, das das System nicht hält.

  /** Der Ausgang wird am **versendeten** Angebot festgehalten (Server: QUOTE_AUSGANG). */
  protected readonly kannAusgangSetzen = computed(
    () => this.daten()?.status === 'VERSENDET' && this.auth.darf('invoicing', 'AENDERN'),
  );
  protected readonly ausgangOffen = signal<QuoteAusgang | null>(null);
  protected readonly ausgangLaedt = signal(false);

  ausgangFragen(ziel: QuoteAusgang): void {
    this.meldung.set(null);
    this.ausgangOffen.set(ziel);
  }

  ausgangAbbrechen(): void {
    if (!this.ausgangLaedt()) this.ausgangOffen.set(null);
  }

  ausgangBestaetigen(): void {
    const d = this.daten();
    const ziel = this.ausgangOffen();
    if (!d || !ziel || this.ausgangLaedt()) return;
    this.ausgangLaedt.set(true);
    this.svc.setQuoteStatus(d.id, ziel).subscribe({
      next: (aktualisiert) => {
        this.ausgangLaedt.set(false);
        this.ausgangOffen.set(null);
        this.state.set({ kind: 'ready', data: aktualisiert });
        this.meldung.set({ art: 'erfolg', text: AUSGANG_ERFOLG[ziel] });
      },
      error: (err) => {
        this.ausgangLaedt.set(false);
        this.ausgangOffen.set(null);
        this.meldung.set({ art: 'fehler', text: this.fehlerText(err) });
      },
    });
  }

  /** Titel/Text des Bestätigungsdialogs — je Ausgang eine eigene Aussage. */
  ausgangTitel(): string {
    const z = this.ausgangOffen();
    return z ? AUSGANG_TITEL[z] : '';
  }

  ausgangText(): string {
    const z = this.ausgangOffen();
    return z ? AUSGANG_TEXT[z] : '';
  }

  ausgangLabel(): string {
    const z = this.ausgangOffen();
    return z ? AUSGANG_LABEL[z] : '';
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  // ---- Rechnung aus diesem Angebot erzeugen (Dokumente-1) ----------------
  //
  // Der direkte, sichtbare Weg VOM Angebot: POST /invoicing/invoices/aus-angebot
  // kopiert die Positionen wertgleich in einen Rechnungs-ENTWURF und bindet jede
  // übernommene Betragsposition (ANGEBOTSPOSITION) — der Rückverweis. Bisher war
  // dieser Weg nur über den Abrechnung-Tab der Auftragsmappe erreichbar.
  //
  // Server-Tore (`abrechnung.rechnung_aus_angebot`):
  //  * Recht invoicing/ANLEGEN.
  //  * Status ist eine Vereinbarung → `RECHNUNG_AUS_ANGEBOT_STATUS`. Das gaten wir
  //    hier, damit die Aktion nur erscheint, wenn der Server sie annimmt.
  //  * REGIE-Auftrag am Angebot → 422 (das Ist wird abgerechnet, nicht die Kopie).
  //  * Schon abgerechnet (aktive Bindung) → 422, mit Nennung der Rechnung.
  // Die letzten beiden kann das UI nicht vorab wissen — sie kommen als Fehler über
  // die `meldung`-Leiste.
  protected readonly kannRechnungErzeugen = computed(() => {
    const d = this.daten();
    if (!d || !this.auth.darf('invoicing', 'ANLEGEN')) return false;
    return RECHNUNG_AUS_ANGEBOT_STATUS.includes(d.status);
  });
  protected readonly rechnungOffen = signal(false);
  protected readonly rechnungLaedt = signal(false);

  rechnungFragen(): void {
    this.meldung.set(null);
    this.rechnungOffen.set(true);
  }

  rechnungAbbrechen(): void {
    if (!this.rechnungLaedt()) this.rechnungOffen.set(false);
  }

  rechnungBestaetigen(): void {
    const d = this.daten();
    if (!d || this.rechnungLaedt()) return;
    this.rechnungLaedt.set(true);
    this.svc.rechnungAusAngebot({ quote_id: d.id }).subscribe({
      next: (rechnung) => {
        this.rechnungLaedt.set(false);
        this.rechnungOffen.set(false);
        // In die neue Rechnung navigieren (Rechnungsmappe).
        this.router.navigate(['/dokumente/rechnung', rechnung.id]);
      },
      error: (err) => {
        this.rechnungLaedt.set(false);
        this.rechnungOffen.set(false);
        this.meldung.set({ art: 'fehler', text: this.fehlerText(err) });
      },
    });
  }

  // ---- Kopieren: neuer Entwurf aus diesem Angebot ------------------------
  //
  // Erzeugt serverseitig einen frischen ENTWURF mit eigener Nummer (GoBD: eine
  // Kopie ist ein neuer Beleg, kein Duplikat des festgeschriebenen Originals).
  // Aus JEDEM Status kopierbar — die Quelle wird nur gelesen. Recht ANLEGEN.
  protected readonly darfKopieren = computed(() => this.auth.darf('invoicing', 'ANLEGEN'));
  protected readonly kopierenOffen = signal(false);
  protected readonly kopierenLaedt = signal(false);
  protected readonly kopierenMeldung = signal<string | null>(null);
  /** Ziel-Liegenschaft/-Projekt: leer = wie Quelle (Server-Default). */
  protected readonly kopierenForm = this.fb.group({
    property_id: this.fb.control('', { nonNullable: true }),
    project_id: this.fb.control('', { nonNullable: true }),
  });

  kopierenOeffnen(): void {
    this.meldung.set(null);
    this.kopierenMeldung.set(null);
    this.kopierenForm.reset({ property_id: '', project_id: '' });
    this.kopierenOffen.set(true);
  }

  kopierenSchliessen(): void {
    if (!this.kopierenLaedt()) this.kopierenOffen.set(false);
  }

  kopierenAbsenden(): void {
    const d = this.daten();
    if (!d || this.kopierenLaedt()) return;
    this.kopierenMeldung.set(null);
    // Nur aktiv gewählte Ziele überschreiben die Quelle; leer bleibt weggelassen.
    const ziel: QuoteCopy = {};
    const prop = this.kopierenForm.controls.property_id.value.trim();
    const proj = this.kopierenForm.controls.project_id.value.trim();
    if (prop) ziel.property_id = prop;
    if (proj) ziel.project_id = proj;
    this.kopierenLaedt.set(true);
    this.svc.copyQuote(d.id, ziel).subscribe({
      next: (neu) => {
        this.kopierenLaedt.set(false);
        this.kopierenOffen.set(false);
        // In den neuen Entwurf navigieren (Editor).
        this.router.navigate(['/dokumente/angebot', neu.id]);
      },
      error: (err) => {
        this.kopierenLaedt.set(false);
        this.kopierenMeldung.set(this.fehlerText(err));
      },
    });
  }

  // ---- Verschieben: Angebotsentwurf einem anderen Projekt zuordnen -------
  //
  // Nur solange der Beleg nicht festgeschrieben ist (Entwurfsphase) — ab VERSENDET
  // friert die DB alle Spalten außer dem Status ein (B-30). Recht AENDERN. Passt
  // ein hängender Auftrag nicht zum neuen Projekt, antwortet der Server mit 422.
  protected readonly darfVerschieben = computed(() => {
    const d = this.daten();
    if (!d || !this.auth.darf('invoicing', 'AENDERN')) return false;
    return d.status === 'ENTWURF' || d.status === 'INTERN_GEPRUEFT' || d.status === 'FREIGEGEBEN';
  });
  protected readonly verschiebenOffen = signal(false);
  protected readonly verschiebenLaedt = signal(false);
  protected readonly verschiebenMeldung = signal<string | null>(null);
  protected readonly verschiebenForm = this.fb.group({
    project_id: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
  });

  verschiebenOeffnen(): void {
    this.meldung.set(null);
    this.verschiebenMeldung.set(null);
    this.verschiebenForm.reset({ project_id: '' });
    this.verschiebenOffen.set(true);
  }

  verschiebenSchliessen(): void {
    if (!this.verschiebenLaedt()) this.verschiebenOffen.set(false);
  }

  verschiebenAbsenden(): void {
    const d = this.daten();
    if (!d || this.verschiebenLaedt()) return;
    this.verschiebenMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.verschiebenForm);
    if (this.verschiebenForm.invalid) return;
    const proj = this.verschiebenForm.controls.project_id.value.trim();
    this.verschiebenLaedt.set(true);
    this.svc.updateQuote(d.id, { project_id: proj }).subscribe({
      next: (aktualisiert) => {
        this.verschiebenLaedt.set(false);
        this.verschiebenOffen.set(false);
        this.state.set({ kind: 'ready', data: aktualisiert });
        this.meldung.set({
          art: 'erfolg',
          text: `Angebot wurde dem Projekt „${aktualisiert.project?.name ?? '—'}" zugeordnet.`,
        });
      },
      error: (err) => {
        this.verschiebenLaedt.set(false);
        this.verschiebenMeldung.set(apiFehlerZuweisen(err, this.verschiebenForm).formular);
      },
    });
  }

  // ---- Per E-Mail senden --------------------------------------------------
  versandOeffnen(): void {
    const d = this.daten();
    if (!d) return;
    this.versandForm.reset({ to_address: d.recipient_email ?? '' });
    serverFehlerZuruecksetzen(this.versandForm);
    this.versandMeldung.set(null);
    this.meldung.set(null);
    this.versandOffen.set(true);
  }

  versandSchliessen(): void {
    if (!this.versandLaedt()) this.versandOffen.set(false);
  }

  versandAbsenden(): void {
    const d = this.daten();
    if (!d || this.versandLaedt()) return;
    serverFehlerZuruecksetzen(this.versandForm);
    this.versandMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.versandForm);
    if (this.versandForm.invalid) return;

    const to = this.versandForm.controls.to_address.value.trim();
    this.versandLaedt.set(true);
    this.svc.sendQuoteEmail(d.id, to).subscribe({
      next: (res) => {
        this.versandLaedt.set(false);
        this.versandOffen.set(false);
        this.meldung.set({
          art: 'erfolg',
          text: `Angebot wurde als PDF an ${res.to_address} gesendet.`,
        });
      },
      error: (err) => {
        this.versandLaedt.set(false);
        this.versandMeldung.set(apiFehlerZuweisen(err, this.versandForm).formular);
      },
    });
  }

  /** URL der (archivierten oder on-the-fly gerenderten) PDF-Ausfertigung. */
  pdfUrl(id: string): string {
    return `/api/invoicing/quotes/${id}/pdf`;
  }

  /** Entwurfsvorschau (jeder Status, ENTWURF-Aufdruck, wird nie archiviert). */
  vorschauUrl(id: string): string {
    return `/api/invoicing/quotes/${id}/pdf/vorschau`;
  }

  private fehlerText(err: unknown): string {
    if (istVerboten(err)) return fehlerDetail(err) ?? 'Keine Berechtigung für diese Aktion.';
    return fehlerDetail(err) ?? 'Die Aktion ist fehlgeschlagen. Bitte erneut versuchen.';
  }

  // ---- Darstellungshelfer -------------------------------------------------
  euro(amount: string | null): string {
    if (amount === null) return '—';
    return new Intl.NumberFormat('de-DE', {
      style: 'currency',
      currency: 'EUR',
    }).format(Number(amount));
  }

  menge(qty: string | null, unit: string | null): string {
    if (qty === null) return '';
    // Trailing-Nullen der numeric(15,3) glätten.
    const n = Number(qty);
    const formatted = new Intl.NumberFormat('de-DE', {
      maximumFractionDigits: 3,
    }).format(n);
    return unit ? `${formatted} ${unit}` : formatted;
  }

  statusLabel(s: QuoteStatus): string {
    // Eine Quelle für alle Belegansichten (auch die preisfreie Mengensicht).
    return QUOTE_STATUS_LABEL[s] ?? s;
  }
  statusClass(s: QuoteStatus): string {
    if (s === 'ANGENOMMEN') return 'stamp--positive';
    if (s === 'ABGELEHNT' || s === 'ABGELAUFEN' || s === 'ERSETZT') return 'stamp--warn';
    return '';
  }

  lineTypeLabel(t: LineType): string {
    return LINE_TYPE_LABEL[t] ?? t;
  }

  isText(t: LineType): boolean {
    return t === 'TEXT' || t === 'ZWISCHENSUMME';
  }

  // Kurzform des Inhalts-Hashes (Beleg-Fingerabdruck) für die Anzeige.
  hashKurz(h: string | null): string {
    return h ? h.slice(0, 12) + '…' : '—';
  }
}
