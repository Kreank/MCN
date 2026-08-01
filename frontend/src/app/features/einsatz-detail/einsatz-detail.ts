import { Component, computed, effect, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { vonLokalerEingabe } from '../../shared/datum';
import { Dateien } from '../../shared/dateien/dateien';
import { Berichte } from '../../shared/berichte/berichte';
import { ZielFilter } from '../../core/datei.model';
import { VerbotenState, fehlerDetail, fehlerState, istVerboten } from '../../shared/http-fehler';
import { AuthService } from '../../core/auth.service';
import { EinsatzService } from '../../core/einsatz.service';
import { PartyService } from '../../core/party.service';
import { PlanungStammdatenService } from '../../core/planung-stammdaten.service';
import { KalenderService, icsFehlertext } from '../../core/kalender.service';
import { dateiDownloadAusloesen } from '../../shared/datei-download';
import { FirmaService } from '../../core/firma.service';
import { Trade } from '../../core/firma.model';
import {
  ASSIGNMENT_ROLES,
  CategoryColorToken,
  MaterialArtikel,
  MaterialLogInput,
  Qualifikation,
  ServiceJobDetail,
  ServiceJobStatus,
  ServiceJobUpdate,
  TimeLogInput,
  assignmentRoleLabel,
  categoryColorClass,
  resourceTypeLabel,
  serviceJobStatusClass,
  serviceJobStatusLabel,
  serviceJobStatusLabelStr,
  timeTypeLabel,
  workOrderStatusLabel,
} from '../../core/einsatz.model';
import { ResourceType } from '../../core/einsatz.model';
import { PropertyRef } from '../../core/projekt.model';
import { WorkOrderStatus } from '../../core/auftrag.model';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { ReferenzWahl, RefSuche } from '../../shared/formular/referenz-wahl';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import { Subject, debounceTime, distinctUntilChanged, map } from 'rxjs';
import { deZuApiDezimal, dezimalValidator } from '../../shared/formular/dezimal';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: ServiceJobDetail }
  | VerbotenState
  | { kind: 'error' };

type Meldung = { art: 'erfolg' | 'fehler'; text: string };
type DialogArt =
  | 'termin'
  | 'status'
  | 'zeit'
  | 'material'
  | 'zuweisung'
  | 'kategorie'
  | 'gewerk'
  | 'ressource'
  | 'kontakt';

const JOB_STATUSES: ServiceJobStatus[] = [
  'UNGEPLANT',
  'GEPLANT',
  'BESTAETIGT',
  'UNTERWEGS',
  'VOR_ORT',
  'PAUSIERT',
  'ABGESCHLOSSEN',
  'NACHARBEIT',
  'AUSGEFALLEN',
];

@Component({
  selector: 'app-einsatz-detail',
  imports: [
    Mappe,
    RouterLink,
    KeinZugriff,
    Dateien,
    Berichte,
    ReactiveFormsModule,
    Dialog,
    Feld,
    ReferenzWahl,
  ],
  templateUrl: './einsatz-detail.html',
  styleUrl: './einsatz-detail.scss',
})
export class EinsatzDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(EinsatzService);
  private readonly stammSvc = inject(PlanungStammdatenService);
  private readonly kalenderSvc = inject(KalenderService);
  private readonly partySvc = inject(PartyService);
  private readonly firmaSvc = inject(FirmaService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  /** Disposition (Termin/Status): AENDERN mit Scope ALLE. */
  protected readonly darfDispo = computed(() => {
    const u = this.auth.user();
    return (
      u?.permissions.some(
        (p) => p.module === 'workflow' && p.action === 'AENDERN' && p.row_scope === 'ALLE',
      ) ?? false
    );
  });
  /** Erfassung (Zeit/Material): AENDERN in beliebigem Scope (auch Monteur). */
  protected readonly darfErfassen = computed(() => this.auth.darf('workflow', 'AENDERN'));
  /**
   * Kontakt/Zutritt nachtragen: die Disposition immer; ein Monteur (Scope
   * EIGENE) nur am FREIEN Termin — genau dort entsteht der Kontakt erst vor Ort.
   * Am auftragsgebundenen Einsatz ist er Dispositionsdatum (Server: 403).
   */
  protected readonly darfKontaktPflegen = computed(
    () => this.darfDispo() || (this.darfErfassen() && (this.daten()?.is_free ?? false)),
  );

  /**
   * Qualifikationsbedarf LESEN: `workflow`/LESEN — aber mit Scope ALLE.
   *
   * Der Endpunkt (`GET /planung/einsaetze/{id}/qualifikationen`) benutzt die
   * fail-closed-Torfunktion `require`: Ein Monteur hat LESEN nur als 'EIGENE' und
   * bekommt dort 403. Die Bedienfläche wird ihm deshalb gar nicht erst gezeigt —
   * sonst liefe beim Öffnen des Reiters ein Request, der garantiert scheitert.
   */
  protected readonly darfBedarfSehen = computed(() => {
    const u = this.auth.user();
    return (
      u?.permissions.some(
        (p) => p.module === 'workflow' && p.action === 'LESEN' && p.row_scope === 'ALLE',
      ) ?? false
    );
  });
  /** Bedarf ÄNDERN: `workflow`/AENDERN mit Scope ALLE — dasselbe Tor wie am
   *  Server (`require`), deshalb `darfDispo`, nicht `darf('workflow','AENDERN')`. */
  protected readonly darfBedarfPflegen = computed(() => this.darfDispo());

  protected readonly tab = signal('uebersicht');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  protected readonly tabs: MappeTab[] = [
    { id: 'uebersicht', label: 'Übersicht' },
    { id: 'zuweisungen', label: 'Zuweisungen' },
    { id: 'erfassung', label: 'Zeiten & Material' },
    // Der Bericht zum Termin: beim freien Termin das Begehungsprotokoll, beim
    // auftragsgebundenen Einsatz der Tätigkeitsnachweis. Derselbe Baustein wie
    // in der Auftragsmappe (shared/berichte).
    { id: 'berichte', label: 'Berichte' },
    { id: 'verlauf', label: 'Verlauf' },
    { id: 'dateien', label: 'Dateien' },
  ];

  private readonly dateFmt = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  /** Stabile Zielreferenz fuer den Dateien-Tab (nur bei Einsatzwechsel neu). */
  protected readonly dateienZiel = computed<ZielFilter>(() => ({
    service_job_id: this.daten()?.id ?? '',
  }));

  // --- Schreibaktionen -----------------------------------------------------
  protected readonly meldung = signal<Meldung | null>(null);
  protected readonly dialogOffen = signal<DialogArt | null>(null);
  protected readonly dialogLaedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);

  protected readonly rollen: FeldOption[] = ASSIGNMENT_ROLES;

  /** Benutzersuche (aktive app_user) für die Einsatz-Zuweisung. */
  protected readonly benutzerSuche: RefSuche = (q) =>
    this.svc.listUsers(q).pipe(
      map((users) => users.map((u) => ({ id: u.id, label: u.display_name }))),
    );

  protected readonly zeitarten: FeldOption[] = [
    { wert: 'ARBEITSZEIT', label: 'Arbeitszeit' },
    { wert: 'FAHRTZEIT', label: 'Fahrtzeit' },
    { wert: 'PAUSE', label: 'Pause' },
    { wert: 'BEREITSCHAFT', label: 'Bereitschaft' },
    { wert: 'NACHARBEIT', label: 'Nacharbeit' },
    { wert: 'INTERNE_ZEIT', label: 'Interne Zeit' },
  ];

  protected readonly statusOptionen = computed<FeldOption[]>(() => {
    const cur = this.daten()?.status;
    return JOB_STATUSES.filter((s) => s !== cur).map((s) => ({
      wert: s,
      label: serviceJobStatusLabel(s),
    }));
  });

  protected readonly terminForm = this.fb.group({
    scheduled_start: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    scheduled_end: this.fb.control('', { nonNullable: true }),
  });
  protected readonly statusForm = this.fb.group({
    to_status: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    reason: this.fb.control('', { nonNullable: true }),
  });
  protected readonly zeitForm = this.fb.group({
    time_type: this.fb.control('ARBEITSZEIT', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    started_at: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    ended_at: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    note: this.fb.control('', { nonNullable: true }),
  });
  /**
   * Material buchen. **Kein Preisfeld** — und das bleibt so: Ein Preis, den der
   * Monteur auf der Baustelle nennt, wäre eine Preisvereinbarung (Invariante
   * Kap. 3). Der Artikelbezug ist optional; er ist die Grundlage, aus der der
   * SERVER später den Preis rechnet.
   */
  protected readonly materialForm = this.fb.group({
    description: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    quantity: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, dezimalValidator],
    }),
    unit: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    note: this.fb.control('', { nonNullable: true }),
    source_article_id: this.fb.control<string | null>(null),
  });
  protected readonly zuweisungForm = this.fb.group({
    assignee_user_id: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    role: this.fb.control('TECHNICIAN', { nonNullable: true, validators: [Validators.required] }),
  });
  protected readonly kategorieForm = this.fb.group({
    category_id: this.fb.control('', { nonNullable: true }),
  });
  /** Gewerk am Einsatz (0120). Leeres Feld = ausdrückliches Entfernen — es wird
   *  danach NICHT erneut vom Auftrag geerbt (siehe Server-Docstring). */
  protected readonly gewerkForm = this.fb.group({
    trade_id: this.fb.control('', { nonNullable: true }),
  });
  protected readonly ressourceForm = this.fb.group({
    resource_id: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
  });
  /** Kontakt/Zutritt nachtragen — beides darf auch der Monteur am eigenen Einsatz. */
  protected readonly kontaktForm = this.fb.group({
    on_site_contact_party_id: this.fb.control('', { nonNullable: true }),
    access_instructions: this.fb.control('', { nonNullable: true }),
  });

  /** Kontaktsuche (identity.party) für den Ansprechpartner vor Ort. */
  protected readonly partySuche: RefSuche = (q) =>
    this.partySvc.list({ page: 1, page_size: 20, q }).pipe(
      map((p) => p.items.map((x) => ({ id: x.id, label: x.display_name }))),
    );

  // Auswahllisten (aktive Stammdaten), erst beim Öffnen des Dialogs geladen.
  protected readonly kategorieOpt = signal<FeldOption[]>([]);
  protected readonly gewerkOpt = signal<FeldOption[]>([]);
  protected readonly ressourceOpt = signal<FeldOption[]>([]);
  protected readonly aktionBusyId = signal<string | null>(null);

  /** Läuft gerade ein ICS-Download? (Doppelklick-Schutz am Kalender-Knopf.) */
  protected readonly icsBusy = signal(false);

  // ---- Qualifikationsbedarf dieses Termins --------------------------------
  /**
   * Was DIESER Termin zusätzlich verlangt (workflow.service_job_qualification).
   *
   * Der Bedarf der **Kategorie** bleibt unberührt — wirksam ist die Vereinigung
   * beider. Die Plantafel WARNT bei fehlendem Nachweis, sie blockiert nicht
   * (weiche Invariante wie die Doppelbelegung).
   */
  protected readonly bedarfState = signal<'idle' | 'loading' | 'ready' | 'error'>(
    'idle',
  );
  protected readonly qualKatalog = signal<Qualifikation[]>([]);
  protected readonly bedarf = signal<string[]>([]);
  private bedarfGespeichert: string[] = [];
  protected readonly bedarfBusy = signal(false);
  protected readonly bedarfMeldung = signal<Meldung | null>(null);

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.tab.set('uebersicht');
      this.bedarfState.set('idle');
      this.bedarfMeldung.set(null);
      if (!id) {
        this.state.set({ kind: 'error' });
        return;
      }
      this.load(id);
    });

    // Der Qualifikationsbedarf hängt am Reiter „Zuweisungen" — dort, wo man
    // entscheidet, WER hinfährt. Lazy nachgeladen (Hausmuster projekt-detail).
    effect(() => {
      const d = this.daten();
      if (!d) return;
      if (this.tab() !== 'zuweisungen') return;
      if (!this.darfBedarfSehen()) return;
      if (this.bedarfState() !== 'idle') return;
      this.ladeBedarf(d.id);
    });

    this.artikelSuche$
      .pipe(debounceTime(300), distinctUntilChanged(), takeUntilDestroyed())
      .subscribe((q) => this.artikelFetch(q));
  }

  // --- Artikel-Picker der Materialbuchung ----------------------------------
  //
  // Preisfrei per Konstruktion: Die Antwort von `materialArtikelSuche` führt kein
  // Geldfeld (eigenes Server-Schema). Der Monteur sieht sein ganzes Objekt, aber
  // nie Preise — hier gibt es keinen, den er sehen könnte.
  protected readonly artikelTreffer = signal<MaterialArtikel[]>([]);
  protected readonly artikelLaedt = signal(false);
  protected readonly artikelFehler = signal(false);
  /** Der gewählte Artikel — null = Freitextbuchung (weiterhin erlaubt). */
  protected readonly artikelWahl = signal<MaterialArtikel | null>(null);
  private readonly artikelSuche$ = new Subject<string>();
  private artikelReq = 0;

  onArtikelSuche(wert: string): void {
    this.artikelSuche$.next(wert.trim());
  }

  private artikelFetch(q: string): void {
    const rid = ++this.artikelReq;
    this.artikelLaedt.set(true);
    this.artikelFehler.set(false);
    this.svc.materialArtikelSuche(q).subscribe({
      next: (treffer) => {
        if (rid !== this.artikelReq) return;
        this.artikelLaedt.set(false);
        this.artikelTreffer.set(treffer);
      },
      error: () => {
        if (rid !== this.artikelReq) return;
        this.artikelLaedt.set(false);
        this.artikelTreffer.set([]);
        // Wer das Recht nicht hat, bekommt 403 — dann bleibt die Freitextbuchung.
        this.artikelFehler.set(true);
      },
    });
  }

  /**
   * Artikel übernehmen: Bezeichnung und Einheit werden **vorbelegt**, nicht
   * festgeschrieben. Der Monteur präzisiert vor Ort („Rohr DN20, gekürzt"), und
   * die Einheit weicht in der Praxis ab („Stk" statt „Stück"). Was er tippt,
   * bleibt stehen.
   */
  artikelWaehlen(a: MaterialArtikel): void {
    this.artikelWahl.set(a);
    this.materialForm.patchValue({
      source_article_id: a.id,
      description: a.description,
      unit: a.unit,
    });
  }

  /** Zurück zur Freitextbuchung — Bezeichnung und Einheit bleiben stehen. */
  artikelLoesen(): void {
    this.artikelWahl.set(null);
    this.materialForm.patchValue({ source_article_id: null });
  }

  private ladeBedarf(jobId: string): void {
    this.bedarfState.set('loading');
    this.stammSvc.listQualifikationen().subscribe({
      next: (katalog) => {
        this.qualKatalog.set(katalog);
        this.stammSvc.einsatzBedarf(jobId).subscribe({
          next: (qs) => {
            const ids = qs.map((q) => q.id);
            this.bedarf.set(ids);
            this.bedarfGespeichert = ids;
            this.bedarfState.set('ready');
          },
          error: () => this.bedarfState.set('error'),
        });
      },
      error: () => this.bedarfState.set('error'),
    });
  }

  bedarfUmschalten(qualId: string): void {
    if (this.bedarfBusy()) return;
    const liste = this.bedarf();
    this.bedarf.set(
      liste.includes(qualId) ? liste.filter((x) => x !== qualId) : [...liste, qualId],
    );
  }

  bedarfGesetzt(qualId: string): boolean {
    return this.bedarf().includes(qualId);
  }

  /** Ungespeicherte Änderung? Der Knopf sagt es, statt still zu wirken. */
  protected readonly bedarfGeaendert = computed(() => {
    const a = [...this.bedarf()].sort();
    const b = [...this.bedarfGespeichert].sort();
    return a.length !== b.length || a.some((x, i) => x !== b[i]);
  });

  bedarfSpeichern(): void {
    const d = this.daten();
    if (!d || this.bedarfBusy()) return;
    this.bedarfBusy.set(true);
    this.bedarfMeldung.set(null);
    this.stammSvc.setEinsatzBedarf(d.id, this.bedarf()).subscribe({
      next: (qs) => {
        const ids = qs.map((q) => q.id);
        this.bedarf.set(ids);
        this.bedarfGespeichert = ids;
        this.bedarfBusy.set(false);
        this.bedarfMeldung.set({
          art: 'erfolg',
          text: ids.length
            ? 'Qualifikationsbedarf gespeichert. Fehlt einem Zugewiesenen ein Nachweis, warnt die Plantafel — sie blockiert nicht.'
            : 'Qualifikationsbedarf entfernt. Es gilt weiter, was die Terminkategorie verlangt.',
        });
      },
      error: (err) => {
        this.bedarfBusy.set(false);
        this.bedarfMeldung.set({
          art: 'fehler',
          text:
            fehlerDetail(err) ??
            'Der Qualifikationsbedarf ließ sich nicht speichern. Bitte erneut versuchen.',
        });
      },
    });
  }

  bedarfVerwerfen(): void {
    if (this.bedarfBusy()) return;
    this.bedarf.set([...this.bedarfGespeichert]);
    this.bedarfMeldung.set(null);
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
    const d = this.daten();
    this.formularMeldung.set(null);
    switch (art) {
      case 'termin':
        this.terminForm.reset({
          scheduled_start: this.zuLocal(d?.scheduled_start ?? null),
          scheduled_end: this.zuLocal(d?.scheduled_end ?? null),
        });
        break;
      case 'status':
        this.statusForm.reset({ to_status: '', reason: '' });
        break;
      case 'zeit':
        this.zeitForm.reset({ time_type: 'ARBEITSZEIT', started_at: '', ended_at: '', note: '' });
        break;
      case 'material':
        this.materialForm.reset({
          description: '', quantity: '', unit: '', note: '', source_article_id: null,
        });
        this.artikelWahl.set(null);
        this.artikelTreffer.set([]);
        this.artikelFehler.set(false);
        this.artikelFetch('');
        break;
      case 'zuweisung':
        this.zuweisungForm.reset({ assignee_user_id: '', role: 'TECHNICIAN' });
        break;
      case 'kategorie':
        this.kategorieForm.reset({ category_id: d?.category?.id ?? '' });
        this.ladeKategorieOpt();
        break;
      case 'gewerk':
        this.gewerkForm.reset({ trade_id: d?.trade?.id ?? '' });
        this.ladeGewerkOpt();
        break;
      case 'ressource':
        this.ressourceForm.reset({ resource_id: '' });
        this.ladeRessourceOpt();
        break;
      case 'kontakt':
        // Die Referenz-Wahl kann einen bestehenden Wert nicht als Chip anzeigen
        // (sie kennt nur die Treffer ihrer Suche). Deshalb startet sie leer; der
        // aktuelle Kontakt steht als Text im Dialog, und das Entfernen ist eine
        // eigene, ausdrückliche Aktion statt „Feld leer lassen".
        this.kontaktForm.reset({
          on_site_contact_party_id: '',
          access_instructions: d?.access_instructions ?? '',
        });
        break;
    }
    this.dialogOffen.set(art);
  }

  private ladeKategorieOpt(): void {
    this.stammSvc.listKategorien().subscribe({
      next: (ks) =>
        this.kategorieOpt.set(ks.map((k) => ({ wert: k.id, label: k.name }))),
      error: () => this.kategorieOpt.set([]),
    });
  }

  /** Aktive Gewerke (company.trade). Ein stillgelegtes Gewerk soll man nicht neu
   *  vergeben können; ein bereits gesetztes bleibt am Einsatz sichtbar. */
  private ladeGewerkOpt(): void {
    this.firmaSvc.listTrades(false).subscribe({
      next: (ts) =>
        this.gewerkOpt.set(ts.map((t: Trade) => ({ wert: t.id, label: t.label }))),
      error: () => this.gewerkOpt.set([]),
    });
  }

  private ladeRessourceOpt(): void {
    const zugeordnet = new Set((this.daten()?.resources ?? []).map((r) => r.id));
    this.stammSvc.listRessourcen().subscribe({
      next: (rs) =>
        this.ressourceOpt.set(
          rs
            .filter((r) => !zugeordnet.has(r.id))
            .map((r) => ({ wert: r.id, label: `${r.name} (${this.resTypeLabel(r.resource_type)})` })),
        ),
      error: () => this.ressourceOpt.set([]),
    });
  }

  dialogSchliessen(): void {
    if (this.dialogLaedt()) return;
    this.dialogOffen.set(null);
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  private nichtBereit(form: Parameters<typeof serverFehlerZuruecksetzen>[0]): boolean {
    if (this.dialogLaedt()) return true;
    serverFehlerZuruecksetzen(form);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(form);
    return form.invalid;
  }

  private nachSchreiben(text: string): void {
    const id = this.daten()?.id;
    this.dialogLaedt.set(false);
    this.dialogOffen.set(null);
    this.meldung.set({ art: 'erfolg', text });
    if (id) this.load(id);
  }

  terminAbsenden(): void {
    const d = this.daten();
    if (!d || this.nichtBereit(this.terminForm)) return;
    const v = this.terminForm.getRawValue();
    // Pflichtfeld laut Validator — aber nicht per `!` behaupten: Ein nicht
    // parsbarer Wert wuerde sonst still als `null` rausgehen und einen 422
    // ausloesen, den im Formular niemand zuordnen kann.
    const start = vonLokalerEingabe(v.scheduled_start);
    if (!start) {
      this.formularMeldung.set('Bitte einen gueltigen Terminbeginn angeben.');
      return;
    }
    this.dialogLaedt.set(true);
    this.svc
      .setSchedule(d.id, { scheduled_start: start, scheduled_end: vonLokalerEingabe(v.scheduled_end) })
      .subscribe({
        next: () => this.nachSchreiben('Termin gesetzt.'),
        error: (err) => {
          this.dialogLaedt.set(false);
          this.formularMeldung.set(apiFehlerZuweisen(err, this.terminForm).formular);
        },
      });
  }

  statusAbsenden(): void {
    const d = this.daten();
    if (!d || this.nichtBereit(this.statusForm)) return;
    const v = this.statusForm.getRawValue();
    this.dialogLaedt.set(true);
    this.svc
      .advanceStatus(d.id, { to_status: v.to_status as ServiceJobStatus, reason: v.reason.trim() || null })
      .subscribe({
        next: () => this.nachSchreiben('Status geändert.'),
        error: (err) => {
          this.dialogLaedt.set(false);
          this.formularMeldung.set(apiFehlerZuweisen(err, this.statusForm).formular);
        },
      });
  }

  zeitAbsenden(): void {
    const d = this.daten();
    if (!d || this.nichtBereit(this.zeitForm)) return;
    const v = this.zeitForm.getRawValue();
    const von = vonLokalerEingabe(v.started_at);
    const bis = vonLokalerEingabe(v.ended_at);
    if (!von || !bis) {
      this.formularMeldung.set('Bitte Beginn und Ende der Zeitbuchung angeben.');
      return;
    }
    const payload: TimeLogInput = {
      time_type: v.time_type,
      started_at: von,
      ended_at: bis,
      note: v.note.trim() || null,
    };
    this.dialogLaedt.set(true);
    this.svc.logTime(d.id, payload).subscribe({
      next: () => this.nachSchreiben('Zeit gebucht.'),
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.zeitForm).formular);
      },
    });
  }

  materialAbsenden(): void {
    const d = this.daten();
    if (!d || this.nichtBereit(this.materialForm)) return;
    const v = this.materialForm.getRawValue();
    const payload: MaterialLogInput = {
      description: v.description.trim(),
      quantity: deZuApiDezimal(v.quantity),
      unit: v.unit.trim(),
      note: v.note.trim() || null,
      source_article_id: v.source_article_id ?? null,
    };
    this.dialogLaedt.set(true);
    this.svc.logMaterial(d.id, payload).subscribe({
      next: () => this.nachSchreiben('Material gebucht.'),
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.materialForm).formular);
      },
    });
  }

  zuweisungAbsenden(): void {
    const d = this.daten();
    if (!d || this.nichtBereit(this.zuweisungForm)) return;
    const v = this.zuweisungForm.getRawValue();
    this.dialogLaedt.set(true);
    this.svc.assign(d.id, { assignee_user_id: v.assignee_user_id, role: v.role }).subscribe({
      next: () => this.nachSchreiben('Mitarbeiter zugewiesen.'),
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.zuweisungForm).formular);
      },
    });
  }

  kategorieAbsenden(): void {
    const d = this.daten();
    if (!d || this.dialogLaedt()) return;
    this.formularMeldung.set(null);
    const catId = this.kategorieForm.getRawValue().category_id || null;
    this.dialogLaedt.set(true);
    this.stammSvc.setJobKategorie(d.id, catId).subscribe({
      next: () => this.nachSchreiben(catId ? 'Kategorie gesetzt.' : 'Kategorie entfernt.'),
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.kategorieForm).formular);
      },
    });
  }

  /** Gewerk setzen/ändern/entfernen (PATCH /einsaetze/{id}).
   *
   * Ein leeres Feld ist hier ein ausdrückliches `null` — also „Gewerk entfernen".
   * Der Server erbt danach NICHT erneut vom Auftrag; sonst käme das gelöschte
   * Gewerk beim nächsten Speichern von selbst zurück.
   */
  gewerkAbsenden(): void {
    const d = this.daten();
    if (!d || this.dialogLaedt()) return;
    this.formularMeldung.set(null);
    const tradeId = this.gewerkForm.getRawValue().trade_id || null;
    this.dialogLaedt.set(true);
    this.svc.update(d.id, { trade_id: tradeId }).subscribe({
      next: () => this.nachSchreiben(tradeId ? 'Gewerk gesetzt.' : 'Gewerk entfernt.'),
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.gewerkForm).formular);
      },
    });
  }

  ressourceAbsenden(): void {
    const d = this.daten();
    if (!d || this.nichtBereit(this.ressourceForm)) return;
    const resId = this.ressourceForm.getRawValue().resource_id;
    this.dialogLaedt.set(true);
    this.stammSvc.assignRessource(d.id, resId).subscribe({
      next: (res) => {
        const text = res.warnings.length
          ? `Ressource zugeordnet. Hinweis: ${res.warnings.join(' ')}`
          : 'Ressource zugeordnet.';
        this.nachSchreiben(text);
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.ressourceForm).formular);
      },
    });
  }

  /** Kontakt/Zutritt nachtragen. Es werden nur tatsächlich gefüllte bzw.
   * geänderte Felder geschickt — ein leeres Kontaktfeld löscht nichts. */
  kontaktAbsenden(): void {
    const d = this.daten();
    if (!d || this.nichtBereit(this.kontaktForm)) return;
    const v = this.kontaktForm.getRawValue();
    const payload: ServiceJobUpdate = {};
    if (v.on_site_contact_party_id) {
      payload.on_site_contact_party_id = v.on_site_contact_party_id;
    }
    if (this.kontaktForm.controls.access_instructions.dirty) {
      payload.access_instructions = v.access_instructions.trim() || null;
    }
    if (Object.keys(payload).length === 0) {
      this.dialogOffen.set(null);
      return;
    }
    this.dialogLaedt.set(true);
    this.svc.update(d.id, payload).subscribe({
      next: () => this.nachSchreiben('Angaben gespeichert.'),
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.kontaktForm).formular);
      },
    });
  }

  /** Ausdrückliches Entfernen des Ansprechpartners (null statt „leer"). */
  kontaktEntfernen(): void {
    const d = this.daten();
    if (!d || this.dialogLaedt()) return;
    this.formularMeldung.set(null);
    this.dialogLaedt.set(true);
    this.svc.update(d.id, { on_site_contact_party_id: null }).subscribe({
      next: () => this.nachSchreiben('Ansprechpartner entfernt.'),
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.kontaktForm).formular);
      },
    });
  }

  /** Zuweisung eines Mitarbeiters aufheben.
   *
   * Gegenstück zu „+ Zuweisung". Bisher ließ sich eine Zuweisung ausschließlich
   * per Drag & Drop in der Plantafel lösen — wer den Einsatz über die Liste
   * öffnete, saß in einer Sackgasse. Der DB-Trigger sperrt das Lösen nach
   * Einsatzabschluss (Historienschutz F-02); das kommt als 422 zurück und wird
   * als Fehlermeldung gezeigt.
   */
  zuweisungEntfernen(assigneeId: string, name: string): void {
    const d = this.daten();
    if (!d || this.aktionBusyId()) return;
    this.aktionBusyId.set(assigneeId);
    this.meldung.set(null);
    this.svc.unassign(d.id, assigneeId).subscribe({
      next: () => {
        this.aktionBusyId.set(null);
        this.meldung.set({ art: 'erfolg', text: `Zuweisung von ${name} aufgehoben.` });
        this.load(d.id);
      },
      error: (err) => {
        this.aktionBusyId.set(null);
        const text = istVerboten(err)
          ? (fehlerDetail(err) ?? 'Keine Berechtigung für diese Aktion.')
          : (fehlerDetail(err) ??
            'Die Zuweisung ließ sich nicht aufheben. Bitte erneut versuchen.');
        this.meldung.set({ art: 'fehler', text });
      },
    });
  }

  ressourceEntfernen(resourceId: string): void {
    const d = this.daten();
    if (!d || this.aktionBusyId()) return;
    this.aktionBusyId.set(resourceId);
    this.meldung.set(null);
    this.stammSvc.unassignRessource(d.id, resourceId).subscribe({
      next: () => {
        this.aktionBusyId.set(null);
        this.meldung.set({ art: 'erfolg', text: 'Ressourcenzuordnung entfernt.' });
        this.load(d.id);
      },
      error: (err) => {
        this.aktionBusyId.set(null);
        const text = istVerboten(err)
          ? (fehlerDetail(err) ?? 'Keine Berechtigung für diese Aktion.')
          : (fehlerDetail(err) ?? 'Die Aktion ist fehlgeschlagen. Bitte erneut versuchen.');
        this.meldung.set({ art: 'fehler', text });
      },
    });
  }

  /**
   * Termin als .ics herunterladen („In den Kalender").
   *
   * Bewusst als Blob durch den HttpClient und NICHT als `<a href>` auf die
   * API-URL: Ein Direktlink trägt weder CSRF-Header noch verlässlich das
   * Session-Cookie, der Download endete auf der Login-Seite.
   */
  kalenderHerunterladen(): void {
    const d = this.daten();
    if (!d || this.icsBusy()) return;
    this.icsBusy.set(true);
    this.meldung.set(null);
    this.kalenderSvc.einsatz(d.id).subscribe({
      next: (blob) => {
        dateiDownloadAusloesen(blob, `einsatz-${d.job_number || 'termin'}.ics`);
        this.icsBusy.set(false);
      },
      error: (err) => {
        this.icsBusy.set(false);
        void this.icsFehlerAnzeigen(err);
      },
    });
  }

  /** Bei responseType 'blob' ist auch der 422-Körper ein Blob — als Text lesen. */
  private async icsFehlerAnzeigen(err: unknown): Promise<void> {
    const detail = (await icsFehlertext(err)) ?? fehlerDetail(err);
    this.meldung.set({
      art: 'fehler',
      text: detail ?? 'Der Kalendereintrag konnte nicht erzeugt werden.',
    });
  }

  /** ISO-Datetime → Wert für <input type="datetime-local"> (lokale Zeit). */
  private zuLocal(iso: string | null): string {
    if (!iso) return '';
    const dt = new Date(iso);
    if (Number.isNaN(dt.getTime())) return '';
    const p = (n: number) => `${n}`.padStart(2, '0');
    return `${dt.getFullYear()}-${p(dt.getMonth() + 1)}-${p(dt.getDate())}T${p(dt.getHours())}:${p(dt.getMinutes())}`;
  }

  // ---- Darstellungshelfer -------------------------------------------------
  statusLabel(s: ServiceJobStatus): string {
    return serviceJobStatusLabel(s);
  }
  statusClass(s: ServiceJobStatus): string {
    return serviceJobStatusClass(s);
  }
  statusLabelStr(s: string | null): string {
    return serviceJobStatusLabelStr(s);
  }
  orderStatusLabel(s: WorkOrderStatus): string {
    return workOrderStatusLabel(s);
  }
  timeTypeLabel(t: string): string {
    return timeTypeLabel(t);
  }
  roleLabel(r: string): string {
    return assignmentRoleLabel(r);
  }
  categoryClass(token: CategoryColorToken): string {
    return categoryColorClass(token);
  }
  resTypeLabel(t: ResourceType): string {
    return resourceTypeLabel(t);
  }
  dt(iso: string | null): string {
    if (!iso) return '—';
    return this.dateFmt.format(new Date(iso));
  }

  /** Zieladresse als eine Zeile: „Straße Hausnr, PLZ Stadt". Fällt auf die Stadt
   *  zurück, wenn (bei älteren Referenzen) keine Adressteile mitgeliefert wurden. */
  adresseZeile(p: PropertyRef): string {
    const strasse = [p.street, p.house_number].filter((t) => !!t && t.trim()).join(' ');
    const ort = [p.postal_code, p.city].filter((t) => !!t && t.trim()).join(' ');
    return [strasse, ort].filter((t) => t).join(', ') || p.city;
  }

  /**
   * Präziser Zielort innerhalb der Liegenschaft: Gebäude und/oder Einheit, mit
   * „ · " verbunden (z. B. „Steglitzer Damm 12 · 3. OG rechts"). Leerer String,
   * wenn weder Gebäude noch Einheit gesetzt sind — dann gilt der Termin fürs
   * Objekt als Ganzes. Als TEXT, nicht nur über Position (WCAG 1.4.1).
   */
  ortDetail(p: PropertyRef): string {
    return [p.building, p.unit].filter((t) => !!t && t.trim()).join(' · ');
  }

  /** Karten-Link für die Navigation zum Einsatzort (öffnet in neuem Tab). Null,
   *  wenn keine brauchbare Adresse vorliegt. */
  mapsUrl(p: PropertyRef): string | null {
    const q = this.adresseZeile(p).trim();
    if (!q) return null;
    return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(q)}`;
  }

  /** Zugewiesene Mitarbeiter als Namensliste (für die „Wer"-Kachel oben). */
  zugewieseneNamen(): string {
    return (this.daten()?.assignments ?? []).map((a) => a.display_name).join(', ');
  }
}
