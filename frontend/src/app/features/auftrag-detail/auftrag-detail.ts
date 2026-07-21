import { Component, computed, effect, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { map } from 'rxjs';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { AuftragService } from '../../core/auftrag.service';
import { EinsatzService } from '../../core/einsatz.service';
import { BelegService } from '../../core/beleg.service';
import { PartyService } from '../../core/party.service';
import { AuthService } from '../../core/auth.service';
import { QuoteCreate } from '../../core/beleg.model';
import {
  Kundenhistorie,
  OrderPriority,
  WorkOrderDetail,
  WorkOrderPartyCreate,
  WorkOrderStatus,
  workOrderStatusClass,
  workOrderStatusLabel,
} from '../../core/auftrag.model';
import {
  ServiceJob,
  ServiceJobCreate,
  ServiceJobDetail,
  ServiceJobStatus,
  serviceJobStatusLabel,
} from '../../core/einsatz.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { vonLokalerEingabe } from '../../shared/datum';
import { Dateien } from '../../shared/dateien/dateien';
import { Berichte } from '../../shared/berichte/berichte';
import { SollIstAbgleich } from '../../shared/soll-ist/soll-ist';
import { Abrechnung } from './abrechnung';
import { Nachtrag } from './nachtrag';
import { ZielFilter } from '../../core/datei.model';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';
import { Dialog } from '../../shared/dialog/dialog';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { ReferenzWahl, RefSuche } from '../../shared/formular/referenz-wahl';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import { deZuApiDezimal, dezimalValidator } from '../../shared/formular/dezimal';
import { nichtNurLeerraumValidator } from '../../shared/formular/text';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: WorkOrderDetail }
  | VerbotenState
  | { kind: 'error' };

type Meldung = { art: 'erfolg' | 'fehler'; text: string };
type DialogArt = 'beteiligter' | 'status' | 'nachweis' | 'verantwortung' | 'termin';

/**
 * Ein Tor der Auftragsfreigabe, wie es `workflow.recheck_work_order_gates`
 * (`db/migrations/0013_auftrag.sql:143-179`) prüft — als Zeile für die
 * Checkliste am Auftrag.
 */
type FreigabeSchritt = {
  id: 'nachweis' | 'verantwortung' | 'auftraggeber';
  label: string;
  regel: string;
  erfuellt: boolean;
  /** Der Notfall (`is_emergency`) hebt genau dieses Tor auf — A-26 nicht. */
  imNotfallEntbehrlich: boolean;
  /** Der Ist-Wert, damit die Zeile auch im erfüllten Fall etwas aussagt. */
  wert: string;
  aktion: DialogArt;
  aktionLabel: string;
};

/** Zustand einer Checklistenzeile. `entbehrlich` = durch Notfall aufgehoben. */
type SchrittZustand = 'erfuellt' | 'entbehrlich' | 'offen';

// Kandidaten für den allgemeinen Statuswechsel (FREIGEGEBEN läuft über die
// gesonderte Freigabe mit Bestätigung und eigenem Recht).
const STATUS_KANDIDATEN: WorkOrderStatus[] = [
  'FREIGABE_AUSSTEHEND',
  'IN_PLANUNG',
  'IN_AUSFUEHRUNG',
  'TECHNISCH_ABGESCHLOSSEN',
  'KAUFMAENNISCH_GEPRUEFT',
  'ABGERECHNET',
  'STORNIERT',
];

@Component({
  selector: 'app-auftrag-detail',
  imports: [
    Mappe,
    RouterLink,
    KeinZugriff,
    Dateien,
    ReactiveFormsModule,
    Dialog,
    Bestaetigung,
    Feld,
    ReferenzWahl,
    Berichte,
    SollIstAbgleich,
    Abrechnung,
    Nachtrag,
  ],
  templateUrl: './auftrag-detail.html',
  styleUrl: './auftrag-detail.scss',
})
export class AuftragDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly svc = inject(AuftragService);
  private readonly einsatzSvc = inject(EinsatzService);
  private readonly belegSvc = inject(BelegService);
  private readonly partySvc = inject(PartyService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  /**
   * `darfAlle`: Statuswechsel, Verantwortung, Nachweis und „+ Beteiligter" laufen
   * über fail-closed-Endpunkte (`require` in `auftrag.py`) — ein Konto mit
   * row_scope EIGENE bekommt dort 403. Der Monteur trägt `workflow/AENDERN` mit
   * EIGENE für Zeit-/Materialbuchung und Berichte, nicht für den Auftragsautomaten.
   */
  protected readonly darfAendern = computed(() => this.auth.darfAlle('workflow', 'AENDERN'));
  protected readonly darfFreigeben = computed(() => this.auth.darf('workflow', 'FREIGEBEN'));

  /**
   * Termin direkt am Auftrag anlegen ist Dispositionssache: `workflow/ANLEGEN`
   * mit Scope ALLE (fail-closed, wie in der Einsätze-Liste). Ein Monteur (EIGENE)
   * bekommt den „+ Termin"-Knopf nicht; der Server lehnt ihn ohnehin ab.
   */
  protected readonly darfAnlegen = computed(() => this.auth.darfAlle('workflow', 'ANLEGEN'));

  /**
   * „+ Angebot" direkt aus dem Auftrag. `POST /invoicing/quotes` (Anlegen) und der
   * Angebots-Editor (Ändern/Speichern) sind beide fail-closed (`require`) — ein
   * Konto mit row_scope EIGENE bekommt dort 403. Beide Tore mit Scope ALLE prüfen,
   * damit der Knopf nur erscheint, wenn die ganze Kette (anlegen → im Editor
   * bearbeiten) durchläuft.
   */
  // Volle Kette abbilden: anlegen (POST) → im Editor laden (GET → invoicing/LESEN)
  // → speichern (AENDERN). Rechte sind nicht hierarchisch (AENDERN impliziert kein
  // LESEN) — ohne LESEN landete der Nutzer nach der Anlage im Editor auf 403.
  protected readonly darfAngebot = computed(
    () =>
      this.auth.darfAlle('invoicing', 'LESEN') &&
      this.auth.darfAlle('invoicing', 'ANLEGEN') &&
      this.auth.darfAlle('invoicing', 'AENDERN'),
  );
  protected readonly angebotLaedt = signal(false);

  protected readonly tab = signal('uebersicht');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  /**
   * Soll-Ist: `darf`, NICHT `darfAlle`.
   *
   * `GET /work_orders/{id}/soll-ist` ist ausdrücklich `require_scoped`
   * (`site_report.py`): Der Abgleich führt **Mengen, keine Beträge** und ist für
   * die Objektsicht gedacht — er sagt dem Monteur, was am Auftrag geplant war und
   * was tatsächlich verbaut wurde. Hier stand vorher `darfAlle` und hat ihm eine
   * Ansicht vorenthalten, die der Server ihm gibt.
   */
  protected readonly darfSollIst = computed(() => this.auth.darf('workflow', 'LESEN'));

  /**
   * `offene-abrechnung` ist dieselbe Auftragssicht über die ganze Baustelle wie
   * das Soll-Ist — Rollen mit row_scope EIGENE bekommen 403 (fail-closed). Der
   * Reiter wird bei ihnen gar nicht erst angeboten; dazu braucht es das
   * Abrechnungsmodul überhaupt lesen zu dürfen.
   */
  protected readonly darfAbrechnung = computed(
    () => this.auth.darfAlle('workflow', 'LESEN') && this.auth.darf('invoicing', 'LESEN'),
  );

  protected readonly tabs = computed<MappeTab[]>(() => [
    { id: 'uebersicht', label: 'Übersicht' },
    { id: 'beteiligte', label: 'Beteiligte' },
    { id: 'termine', label: 'Termine' },
    { id: 'berichte', label: 'Berichte' },
    ...(this.darfSollIst() ? [{ id: 'soll-ist', label: 'Soll-Ist' }] : []),
    ...(this.darfAbrechnung() ? [{ id: 'abrechnung', label: 'Abrechnung' }] : []),
    { id: 'verlauf', label: 'Verlauf' },
    { id: 'dateien', label: 'Dateien' },
  ]);

  /** Die Abrechnung hat den Auftrag geändert (Abrechnungsart) — übernehmen. */
  auftragUebernehmen(d: WorkOrderDetail): void {
    this.aktualisieren(d);
  }

  // --- Termine-Tab (Termine des Auftrags + Kundenhistorie) ------------------
  // Lazy: erst beim Öffnen des Reiters laden; je Auftrag einmal.
  protected readonly termineLaden = signal(false);
  protected readonly termineFehler = signal(false);
  protected readonly termine = signal<ServiceJob[]>([]);
  protected readonly historie = signal<Kundenhistorie | null>(null);
  private termineFuer: string | null = null;
  private termineReqId = 0;

  /**
   * Der EINE Termin des Auftrags — 1:1 ist der Normalfall, und dort ist die
   * einzeilige Liste nur eine Klick-Steuer vor der Detailseite. Gesetzt, zeigt
   * der Reiter die Daten sofort.
   *
   * Warum ein zweiter Request: die Listen-Antwort (`ServiceJobOut`) führt nur
   * `assignee_count`, nicht die NAMEN der Zugewiesenen — die stehen erst im
   * Detail. Für genau einen Termin ist das der ersparte Seitenwechsel wert.
   *
   * Bleibt `null` — auch wenn das Nachladen scheitert —, rendert der Reiter die
   * gewohnte Liste. Ein Fehler ist hier also kein Fehlerzustand, sondern nur der
   * Verzicht auf die Zusatzansicht; die Listendaten liegen ja bereits vor.
   */
  protected readonly einzelTermin = signal<ServiceJobDetail | null>(null);

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  /** Stabile Zielreferenz fuer den Dateien-Tab (nur bei Auftragswechsel neu). */
  protected readonly dateienZiel = computed<ZielFilter>(() => ({
    work_order_id: this.daten()?.id ?? '',
  }));

  // --- Schreibaktionen -----------------------------------------------------
  protected readonly meldung = signal<Meldung | null>(null);
  protected readonly dialogOffen = signal<DialogArt | null>(null);
  protected readonly dialogLaedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);
  protected readonly freigabeOffen = signal(false);
  protected readonly freigabeLaedt = signal(false);

  protected readonly rollen: FeldOption[] = [
    { wert: 'PRINCIPAL', label: 'Auftraggeber' },
    { wert: 'REPRESENTATIVE', label: 'Vertretung' },
    { wert: 'SERVICE_RECIPIENT', label: 'Leistungsempfänger' },
    { wert: 'OCCUPANT', label: 'Nutzer' },
    { wert: 'COST_BEARER', label: 'Kostenträger' },
    { wert: 'INVOICE_DEBTOR', label: 'Rechnungsschuldner' },
    { wert: 'INVOICE_RECIPIENT', label: 'Rechnungsempfänger' },
    { wert: 'ON_SITE_CONTACT', label: 'Ansprechpartner vor Ort' },
  ];
  protected readonly scopes: FeldOption[] = [
    { wert: 'UNKNOWN', label: 'Ungeklärt' },
    { wert: 'COMMON_PROPERTY', label: 'Gemeinschaftseigentum' },
    { wert: 'PRIVATE_UNIT', label: 'Sondereigentum' },
    { wert: 'MIXED', label: 'Gemischt' },
  ];

  /** Statusoptionen (ohne aktuellen Status und ohne FREIGEGEBEN). */
  protected readonly statusOptionen = computed<FeldOption[]>(() => {
    const cur = this.daten()?.status;
    return STATUS_KANDIDATEN.filter((s) => s !== cur).map((s) => ({
      wert: s,
      label: workOrderStatusLabel(s),
    }));
  });

  /** Freigabe ist nur vor der Freigabe sinnvoll. */
  protected readonly freigabeMoeglich = computed(() => {
    const s = this.daten()?.status;
    return s === 'ENTWURF' || s === 'FREIGABE_AUSSTEHEND';
  });

  // --- Freigabe-Checkliste (A4) --------------------------------------------
  /**
   * Die drei Tore aus `workflow.recheck_work_order_gates` als lesbare Liste.
   *
   * Zweck: Der Disponent soll **vor** dem Klick sehen, was fehlt, statt es aus
   * einer 422-Meldung zu erfahren und dann drei Masken abzuklappern. Die
   * Nachtrag-Dialoge liegen ohnehin schon auf dieser Seite — die Checkliste
   * verdrahtet jede Lücke direkt mit ihrem Dialog.
   *
   * Wichtig: Das hier ist **Anzeige, kein Tor.** Die Regeln setzt die Datenbank
   * durch; die Oberfläche erfindet keine eigenen Sperren (siehe Befund A3, wo
   * genau so eine erfundene Sperre vermutet — und widerlegt — wurde). Der
   * „Freigeben"-Knopf bleibt deshalb bedienbar, auch wenn Zeilen offen sind.
   */
  protected readonly freigabeSchritte = computed<FreigabeSchritt[]>(() => {
    const d = this.daten();
    if (!d) return [];
    const auftraggeber = d.parties.find((p) => p.role === 'PRINCIPAL');
    // Die DB prüft BEIDES zusammen (0013_auftrag.sql:167-168) — ein bestätigter
    // Zeitpunkt bei Bereich UNKNOWN reicht ihr nicht. Also auch hier ein Wert.
    const verantwortungOk =
      d.responsibility_scope !== 'UNKNOWN' && !!d.responsibility_confirmed_at;
    return [
      {
        id: 'nachweis',
        label: 'Beauftragungsnachweis in Textform',
        regel: 'A-26',
        erfuellt: !!d.order_evidence_reference?.trim(),
        imNotfallEntbehrlich: false,
        wert: d.order_evidence_reference?.trim() || 'Nicht hinterlegt',
        aktion: 'nachweis',
        aktionLabel: 'Nachweis setzen',
      },
      {
        // Beides zusammen — die DB prüft Bereich UND Bestätigungszeitpunkt.
        id: 'verantwortung',
        label: 'Zuständigkeit bestätigt',
        regel: 'B-01/A-21',
        erfuellt: verantwortungOk,
        imNotfallEntbehrlich: true,
        // Am SELBEN Zustand wie `erfuellt` — sonst stünde bei bestätigtem
        // Zeitpunkt und Bereich UNKNOWN „fehlt" neben einem Bereichsnamen.
        wert: verantwortungOk ? this.scopeLabel(d.responsibility_scope) : 'Nicht bestätigt',
        aktion: 'verantwortung',
        aktionLabel: 'Verantwortung bestätigen',
      },
      {
        id: 'auftraggeber',
        label: 'Auftraggeber benannt',
        regel: 'B-01/A-25',
        erfuellt: !!auftraggeber,
        imNotfallEntbehrlich: true,
        wert: auftraggeber?.display_name ?? 'Niemand in der Rolle Auftraggeber',
        aktion: 'beteiligter',
        aktionLabel: 'Auftraggeber hinzufügen',
      },
    ];
  });

  /** Nur die Zeilen, die die Freigabe wirklich noch aufhalten. */
  protected readonly freigabeLuecken = computed(() =>
    this.freigabeSchritte().filter((s) => this.schrittZustand(s) === 'offen'),
  );

  /**
   * Lücke aus der Checkliste heraus schließen.
   *
   * Für den Auftraggeber wird die Rolle vorbelegt: Der Knopf verspricht
   * „Auftraggeber hinzufügen" — die Rolle dann selbst aus acht Einträgen suchen
   * zu müssen, wäre genau die Klickarbeit, gegen die das Arbeitspaket antritt.
   * Schlimmer noch: eine versehentlich andere Rolle ließe das Tor offen, ohne
   * dass die Zeile erklärt, warum.
   */
  luecheSchliessen(s: FreigabeSchritt): void {
    this.dialogOeffnen(s.aktion);
    if (s.id === 'auftraggeber') this.beteiligterForm.patchValue({ role: 'PRINCIPAL' });
  }

  schrittZustand(s: FreigabeSchritt): SchrittZustand {
    if (s.erfuellt) return 'erfuellt';
    if (s.imNotfallEntbehrlich && this.daten()?.is_emergency) return 'entbehrlich';
    return 'offen';
  }

  /** Marke der Zeile. Immer zusammen mit dem Zustandstext — nie Farbe allein. */
  schrittMarke(s: FreigabeSchritt): string {
    const z = this.schrittZustand(s);
    return z === 'erfuellt' ? '✓' : z === 'entbehrlich' ? '–' : '✕';
  }

  schrittZustandText(s: FreigabeSchritt): string {
    const z = this.schrittZustand(s);
    return z === 'erfuellt' ? 'erfüllt' : z === 'entbehrlich' ? 'im Notfall entbehrlich' : 'fehlt';
  }

  protected readonly partySuche: RefSuche = (q) =>
    this.partySvc.list({ page: 1, page_size: 20, q }).pipe(
      map((p) => p.items.map((x) => ({ id: x.id, label: x.display_name }))),
    );

  protected readonly beteiligterForm = this.fb.group({
    party_id: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    role: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    is_primary: this.fb.control(false, { nonNullable: true }),
    allocation_percent: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
  });
  protected readonly statusForm = this.fb.group({
    to_status: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    reason: this.fb.control('', { nonNullable: true }),
  });
  protected readonly nachweisForm = this.fb.group({
    // `nichtNurLeerraum` zusätzlich zu `required`: Ein Nachweis aus lauter
    // Leerzeichen käme durch die DB (dort steht nur NOT NULL, kein CHECK), die
    // Checkliste meldete danach aber dauerhaft „fehlt", weil sie trimmt. Lieber
    // gar nicht erst speichern, als eine Anzeige, die dem Verhalten widerspricht.
    reference: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, nichtNurLeerraumValidator],
    }),
  });
  protected readonly verantwortungForm = this.fb.group({
    scope: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
  });
  // Termin direkt zum Auftrag: work_order_id ist gesetzt, daher ist der Titel
  // optional (Fallback: Auftragstitel) und die Liegenschaft kommt vom Auftrag.
  protected readonly terminForm = this.fb.group({
    title: this.fb.control('', { nonNullable: true }),
    scheduled_start: this.fb.control('', { nonNullable: true }),
    scheduled_end: this.fb.control('', { nonNullable: true }),
    on_site_contact_party_id: this.fb.control('', { nonNullable: true }),
    access_instructions: this.fb.control('', { nonNullable: true }),
  });

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.tab.set('uebersicht');
      this.termineFuer = null;
      this.termine.set([]);
      this.historie.set(null);
      this.einzelTermin.set(null);
      if (!id) {
        this.state.set({ kind: 'error' });
        return;
      }
      this.load(id);
    });

    // Termine-Reiter erst beim Öffnen laden (je Auftrag einmal).
    effect(() => {
      const d = this.daten();
      if (this.tab() === 'termine' && d && this.termineFuer !== d.id) {
        this.termineFuer = d.id;
        this.ladeTermine(d.id);
      }
    });
  }

  private ladeTermine(id: string): void {
    // Generations-Guard: eine verspätete Antwort eines zuvor geöffneten Auftrags
    // darf die aktuellen Signale nicht überschreiben.
    const rid = ++this.termineReqId;
    this.termine.set([]);
    this.historie.set(null);
    this.einzelTermin.set(null);
    this.termineLaden.set(true);
    this.termineFehler.set(false);
    this.einsatzSvc.list({ page: 1, page_size: 100, work_order_id: id }).subscribe({
      next: (p) => {
        if (rid !== this.termineReqId) return;
        this.termine.set(p.items);
        this.termineLaden.set(false);
        // Genau einer? Dann lohnt das Detail (siehe `einzelTermin`).
        if (p.items.length === 1) this.ladeEinzelTermin(rid, p.items[0].id);
      },
      error: () => {
        if (rid !== this.termineReqId) return;
        this.termineLaden.set(false);
        this.termineFehler.set(true);
      },
    });
    this.svc.kundenhistorie(id).subscribe({
      next: (h) => {
        if (rid === this.termineReqId) this.historie.set(h);
      },
      error: () => {},
    });
  }

  /** Detail des einzigen Termins nachladen. Scheitert es (403/404/offline),
   *  bleibt `einzelTermin` leer und der Reiter zeigt die Liste — kein Fehler. */
  private ladeEinzelTermin(rid: number, id: string): void {
    this.einsatzSvc.get(id).subscribe({
      next: (d) => {
        if (rid === this.termineReqId) this.einzelTermin.set(d);
      },
      error: () => {},
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

  // ---- Schreibaktionen ----------------------------------------------------
  dialogOeffnen(art: DialogArt): void {
    this.formularMeldung.set(null);
    switch (art) {
      case 'beteiligter':
        this.beteiligterForm.reset({
          party_id: '',
          role: '',
          is_primary: false,
          allocation_percent: '',
        });
        break;
      case 'status':
        this.statusForm.reset({ to_status: '', reason: '' });
        break;
      case 'nachweis':
        this.nachweisForm.reset({ reference: this.daten()?.order_evidence_reference ?? '' });
        break;
      case 'verantwortung':
        this.verantwortungForm.reset({ scope: this.daten()?.responsibility_scope ?? '' });
        break;
      case 'termin':
        this.terminForm.reset({
          title: '',
          scheduled_start: '',
          scheduled_end: '',
          on_site_contact_party_id: '',
          access_instructions: '',
        });
        break;
    }
    this.dialogOffen.set(art);
  }

  dialogSchliessen(): void {
    if (this.dialogLaedt()) return;
    this.dialogOffen.set(null);
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  private aktualisieren(d: WorkOrderDetail): void {
    // Antwort ist der frische Auftrag — direkt übernehmen (kein zweiter GET).
    // reqId erhöhen, damit ein noch laufender load() das nicht überschreibt.
    ++this.reqId;
    this.state.set({ kind: 'ready', data: d });
  }

  private nichtBereit(form: Parameters<typeof serverFehlerZuruecksetzen>[0]): boolean {
    if (this.dialogLaedt()) return true;
    serverFehlerZuruecksetzen(form);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(form);
    return form.invalid;
  }

  beteiligterAbsenden(): void {
    const d = this.daten();
    if (!d || this.nichtBereit(this.beteiligterForm)) return;
    const v = this.beteiligterForm.getRawValue();
    const alloc = deZuApiDezimal(v.allocation_percent);
    const payload: WorkOrderPartyCreate = {
      party_id: v.party_id,
      role: v.role,
      is_primary: v.is_primary,
      allocation_percent: alloc || null,
    };
    this.dialogLaedt.set(true);
    this.svc.addParty(d.id, payload).subscribe({
      next: (res) => {
        this.dialogLaedt.set(false);
        this.dialogOffen.set(null);
        this.aktualisieren(res);
        this.meldung.set({ art: 'erfolg', text: 'Beteiligter hinzugefügt.' });
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.beteiligterForm).formular);
      },
    });
  }

  statusAbsenden(): void {
    const d = this.daten();
    if (!d || this.nichtBereit(this.statusForm)) return;
    const v = this.statusForm.getRawValue();
    this.dialogLaedt.set(true);
    this.svc
      .advanceStatus(d.id, {
        to_status: v.to_status as WorkOrderStatus,
        reason: v.reason.trim() || null,
      })
      .subscribe({
        next: (res) => {
          this.dialogLaedt.set(false);
          this.dialogOffen.set(null);
          this.aktualisieren(res);
          this.meldung.set({ art: 'erfolg', text: 'Status geändert.' });
        },
        error: (err) => {
          this.dialogLaedt.set(false);
          this.formularMeldung.set(apiFehlerZuweisen(err, this.statusForm).formular);
        },
      });
  }

  nachweisAbsenden(): void {
    const d = this.daten();
    if (!d || this.nichtBereit(this.nachweisForm)) return;
    const v = this.nachweisForm.getRawValue();
    this.dialogLaedt.set(true);
    this.svc.setEvidence(d.id, { reference: v.reference.trim() }).subscribe({
      next: (res) => {
        this.dialogLaedt.set(false);
        this.dialogOffen.set(null);
        this.aktualisieren(res);
        this.meldung.set({ art: 'erfolg', text: 'Beauftragungsnachweis gesetzt.' });
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.nachweisForm).formular);
      },
    });
  }

  verantwortungAbsenden(): void {
    const d = this.daten();
    if (!d || this.nichtBereit(this.verantwortungForm)) return;
    const v = this.verantwortungForm.getRawValue();
    this.dialogLaedt.set(true);
    this.svc.confirmResponsibility(d.id, { scope: v.scope }).subscribe({
      next: (res) => {
        this.dialogLaedt.set(false);
        this.dialogOffen.set(null);
        this.aktualisieren(res);
        this.meldung.set({ art: 'erfolg', text: 'Verantwortungsbereich bestätigt.' });
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.verantwortungForm).formular);
      },
    });
  }

  // --- Termin direkt am Auftrag anlegen ------------------------------------
  // Behebt den Bruch „Auftrag → Plantafel → Auftrag erneut suchen": der Einsatz
  // entsteht hier mit gesetztem work_order_id; Titel/Liegenschaft erbt er vom
  // Auftrag. Terminieren (Startzeit) geht direkt mit; die Antwort erscheint in
  // der Termine-Liste.
  terminAbsenden(): void {
    const d = this.daten();
    if (!d || this.nichtBereit(this.terminForm)) return;
    const v = this.terminForm.getRawValue();
    const payload: ServiceJobCreate = {
      work_order_id: d.id,
      title: v.title.trim() || null,
      property_id: null,
      scheduled_start: vonLokalerEingabe(v.scheduled_start),
      scheduled_end: vonLokalerEingabe(v.scheduled_end),
      on_site_contact_party_id: v.on_site_contact_party_id || null,
      access_instructions: v.access_instructions.trim() || null,
    };
    this.dialogLaedt.set(true);
    this.einsatzSvc.create(payload).subscribe({
      next: () => {
        this.dialogLaedt.set(false);
        this.dialogOffen.set(null);
        this.meldung.set({ art: 'erfolg', text: 'Termin angelegt.' });
        this.ladeTermine(d.id);
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.terminForm).formular);
      },
    });
  }

  // --- Angebot direkt aus dem Auftrag --------------------------------------
  // Kein Umweg über die kontextlose /dokumente-Liste: das Angebot entsteht mit
  // vorbelegter Liegenschaft, Auftrag (= Soll dieser Baustelle) und — falls
  // vorhanden — Projekt. Ohne Zwischendialog, weil der einzige Pflichtwert (Titel)
  // sinnvoll vom Auftrag geerbt und im Editor ohnehin änderbar ist. Die Positionen
  // erfasst der Nutzer direkt im Angebots-Editor, in den wir weiterleiten.
  angebotAnlegen(): void {
    const d = this.daten();
    if (!d || this.angebotLaedt()) return;
    this.meldung.set(null);
    const payload: QuoteCreate = {
      property_id: d.property.id,
      title: d.title,
      work_order_id: d.id,
      project_id: d.project?.id ?? null,
      lines: [],
    };
    this.angebotLaedt.set(true);
    this.belegSvc.createQuote(payload).subscribe({
      next: (q) => {
        this.angebotLaedt.set(false);
        this.router.navigate(['/dokumente/angebot', q.id]);
      },
      error: (err) => {
        this.angebotLaedt.set(false);
        this.meldung.set({
          art: 'fehler',
          text: fehlerDetail(err) ?? 'Das Angebot konnte nicht angelegt werden.',
        });
      },
    });
  }

  // --- Freigabe (Tor mit Folgen → Bestätigung, eigenes Recht FREIGEBEN) -----
  freigabeFragen(): void {
    this.freigabeOffen.set(true);
  }
  freigabeAbbrechen(): void {
    if (!this.freigabeLaedt()) this.freigabeOffen.set(false);
  }
  freigabeBestaetigen(grund: string | null): void {
    const d = this.daten();
    if (!d) return;
    this.freigabeLaedt.set(true);
    this.svc.advanceStatus(d.id, { to_status: 'FREIGEGEBEN', reason: grund }).subscribe({
      next: (res) => {
        this.freigabeLaedt.set(false);
        this.freigabeOffen.set(false);
        this.aktualisieren(res);
        this.meldung.set({ art: 'erfolg', text: 'Auftrag freigegeben.' });
      },
      error: (err) => {
        this.freigabeLaedt.set(false);
        this.freigabeOffen.set(false);
        const text = apiFehlerZuweisen(err, this.statusForm).formular;
        this.meldung.set({ art: 'fehler', text: text ?? 'Freigabe fehlgeschlagen.' });
      },
    });
  }

  // ---- Darstellungshelfer -------------------------------------------------
  private readonly terminFmt = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
  });
  private readonly uhrFmt = new Intl.DateTimeFormat('de-DE', {
    hour: '2-digit', minute: '2-digit',
  });
  /** Geplanter Zeitpunkt eines Termins (oder „ohne Termin"). */
  terminZeit(iso: string | null): string {
    return iso ? this.terminFmt.format(new Date(iso)) : 'ohne Termin';
  }
  /**
   * Planzeitraum als eine Zeile. Beim Ein-Tages-Termin genügt hinten die
   * Uhrzeit — das Datum zweimal zu schreiben, verrauscht die Angabe; ein
   * mehrtägiger Termin braucht es dagegen, sonst sähe er eintägig aus.
   */
  terminZeitraum(t: ServiceJob): string {
    if (!t.scheduled_start) return 'ohne Termin';
    const von = new Date(t.scheduled_start);
    const text = this.terminFmt.format(von);
    if (!t.scheduled_end) return text;
    const bis = new Date(t.scheduled_end);
    const gleicherTag = von.toDateString() === bis.toDateString();
    return `${text} – ${gleicherTag ? this.uhrFmt.format(bis) : this.terminFmt.format(bis)}`;
  }
  /** Zugewiesene Mitarbeiter als Namensliste (nur das Detail führt die Namen). */
  zugewieseneNamen(t: ServiceJobDetail): string {
    return t.assignments.map((a) => a.display_name).join(', ');
  }
  /** Liegenschaft als eine Zeile: „Name · Straße Hausnr, PLZ Stadt". Die
   *  Adressteile sind optional — dann bleibt es bei Name · Stadt. */
  ortZeile(p: NonNullable<ServiceJobDetail['property']>): string {
    const strasse = [p.street, p.house_number].filter((s) => !!s?.trim()).join(' ');
    const ort = [p.postal_code, p.city].filter((s) => !!s?.trim()).join(' ');
    const adresse = [strasse, ort].filter((s) => s).join(', ') || p.city;
    return [p.name, adresse].filter((s) => s).join(' · ');
  }
  jobStatusLabel(s: ServiceJobStatus): string {
    return serviceJobStatusLabel(s);
  }

  statusLabel(s: WorkOrderStatus): string {
    return workOrderStatusLabel(s);
  }
  statusClass(s: WorkOrderStatus): string {
    return workOrderStatusClass(s);
  }
  // Auch für Verlaufseinträge (String-Status).
  statusLabelStr(s: string | null): string {
    if (s === null) return 'Anlage';
    return workOrderStatusLabel(s as WorkOrderStatus);
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

  roleLabel(r: string): string {
    const map: Record<string, string> = {
      PRINCIPAL: 'Auftraggeber',
      REPRESENTATIVE: 'Vertretung',
      SERVICE_RECIPIENT: 'Leistungsempfänger',
      OCCUPANT: 'Nutzer',
      COST_BEARER: 'Kostenträger',
      INVOICE_DEBTOR: 'Rechnungsschuldner',
      INVOICE_RECIPIENT: 'Rechnungsempfänger',
      REPORTER: 'Melder',
      ON_SITE_CONTACT: 'Ansprechpartner vor Ort',
    };
    return map[r] ?? r;
  }
}
