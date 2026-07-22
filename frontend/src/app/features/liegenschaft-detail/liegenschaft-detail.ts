import { Component, computed, effect, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { PropertyService } from '../../core/property.service';
import { PartyService } from '../../core/party.service';
import { ProjektService } from '../../core/projekt.service';
import { AuftragService } from '../../core/auftrag.service';
import { EinsatzService } from '../../core/einsatz.service';
import { WorkOrder, workOrderStatusLabel } from '../../core/auftrag.model';
import { ServiceJob, serviceJobStatusLabel } from '../../core/einsatz.model';
import { AuthService } from '../../core/auth.service';
import {
  CasePriority,
  Project,
  ProjectStatus,
  ServiceCaseCard,
  ServiceCaseStatus,
} from '../../core/projekt.model';
import {
  Building,
  BuildingIn,
  BuildingPatch,
  PartyRoleIn,
  PropertyDetail,
  PropertyRoleCode,
  PropertyStatus,
  PropertyType,
  Unit,
  UnitIn,
  UnitPatch,
  UnitTypeCode,
} from '../../core/property.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { Dateien } from '../../shared/dateien/dateien';
import { Belege, BelegKontext } from '../../shared/belege/belege';
import { Anlagen } from '../anlagen/anlagen';
import { Raumaufmass } from '../raumaufmass/raumaufmass';
import { RaumService } from '../../core/raum.service';
import { Room } from '../../core/raum.model';
import { Belegung } from '../belegung/belegung';
import { Eigentum } from '../eigentum/eigentum';
import { LiegenschaftKopfzeile } from './kopfzeile';
import { Verwaltung } from '../verwaltung/verwaltung';
import { ZielFilter } from '../../core/datei.model';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import { erforderlichGetrimmt, nichtNurLeerraumValidator } from '../../shared/formular/text';
import {
  apiZuDeAnzeige,
  deZuApiDezimal,
  dezimalValidator,
} from '../../shared/formular/dezimal';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';

type ViewState =
  { kind: 'loading' } | { kind: 'ready'; data: PropertyDetail } | VerbotenState | { kind: 'error' };

type Meldung = { art: 'erfolg' | 'fehler'; text: string };

/** Lazy geladene Nebenlisten (Projekte/Vorgänge der Liegenschaft). */
type LazyState<T> =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; items: T[] }
  | VerbotenState
  | { kind: 'error' };

@Component({
  selector: 'app-liegenschaft-detail',
  imports: [
    Mappe,
    RouterLink,
    ReactiveFormsModule,
    KeinZugriff,
    Dateien,
    Belege,
    Dialog,
    Feld,
    Anlagen,
    Raumaufmass,
    Belegung,
    Eigentum,
    LiegenschaftKopfzeile,
    Verwaltung,
  ],
  templateUrl: './liegenschaft-detail.html',
  styleUrl: './liegenschaft-detail.scss',
})
export class LiegenschaftDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(PropertyService);
  private readonly parties = inject(PartyService);
  private readonly projektSvc = inject(ProjektService);
  private readonly auftragSvc = inject(AuftragService);
  private readonly einsatzSvc = inject(EinsatzService);
  private readonly fb = inject(FormBuilder);
  private readonly auth = inject(AuthService);
  private readonly raumSvc = inject(RaumService);

  protected readonly tab = signal('uebersicht');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;
  private nebenReqId = 0;

  protected readonly tabs: MappeTab[] = [
    { id: 'uebersicht', label: 'Übersicht' },
    { id: 'struktur', label: 'Struktur' },
    // Direkt hinter der Struktur: Die Anlage ist das technische Herz des Objekts
    // — was hier steht, entscheidet über jeden Einsatz („zentral oder Therme?").
    { id: 'anlagen', label: 'Anlagen' },
    { id: 'raeume', label: 'Räume' },
    { id: 'beteiligte', label: 'Beteiligte' },
    // Die Verwaltung steht NICHT bei den Beteiligten: Sie ist keine Rolle an der
    // Liegenschaft, sondern ein Mandat (`property_party_role` kennt sie gar
    // nicht). Ein eigener Reiter — sonst verwechselt man Auftraggeber und
    // Verwalter, und die Rechnung geht an den Falschen.
    { id: 'verwaltung', label: 'Verwaltung' },
    { id: 'eigentum', label: 'Eigentum' },
    { id: 'belegung', label: 'Belegung' },
    // Projekte und Vorgänge dieser Liegenschaft — die Klammer über alles, was hier
    // an Arbeit läuft. Direkt vor den Dokumenten, die aus ihnen entstehen.
    { id: 'vorgaenge', label: 'Projekte & Vorgänge' },
    { id: 'dokumente', label: 'Dokumente' },
    { id: 'dateien', label: 'Dateien' },
  ];

  protected readonly unitTypOptionen: FeldOption[] = [
    { wert: 'APARTMENT', label: 'Wohnung' },
    { wert: 'COMMERCIAL', label: 'Gewerbe' },
    { wert: 'GARAGE', label: 'Garage' },
    { wert: 'PARKING', label: 'Stellplatz' },
    { wert: 'STORAGE', label: 'Lager' },
    { wert: 'COMMON_AREA', label: 'Gemeinschaft' },
    { wert: 'TECHNICAL_ROOM', label: 'Technikraum' },
    { wert: 'OTHER', label: 'Sonstige' },
  ];

  protected readonly rolleOptionen: FeldOption[] = [
    { wert: 'COMMUNITY_OF_OWNERS', label: 'Eigentümergemeinschaft' },
    { wert: 'PROPERTY_OWNER', label: 'Eigentümer' },
    { wert: 'OPERATOR', label: 'Betreiber' },
    { wert: 'CARETAKER', label: 'Hausmeisterei' },
  ];

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  /** Stabile Zielreferenz fuer den Dateien-Tab (nur bei Objektwechsel neu). */
  protected readonly dateienZiel = computed<ZielFilter>(() => ({
    property_id: this.daten()?.id ?? '',
  }));

  /** Stabiler Beleg-Kontext für den Dokumente-Tab (Belege dieser Liegenschaft). */
  protected readonly belegKontext = computed<BelegKontext>(() => ({
    property_id: this.daten()?.id ?? '',
  }));

  // --- Projekte & Vorgänge der Liegenschaft (lazy) -------------------------
  protected readonly projekteState = signal<LazyState<Project>>({ kind: 'idle' });
  protected readonly vorgaengeState = signal<LazyState<ServiceCaseCard>>({ kind: 'idle' });
  /** Aufträge und Einsätze an diesem Objekt (Befunde C2/C3). */
  protected readonly auftraegeState = signal<LazyState<WorkOrder>>({ kind: 'idle' });
  protected readonly einsaetzeState = signal<LazyState<ServiceJob>>({ kind: 'idle' });

  /**
   * Kontextzeile für die Werkzeuge (Heizlast/Heizkörper): Objektname + Adresse.
   * Reine Anzeige — sie erscheint auf dem kopierten Ergebnis, damit ein
   * Überschlag später noch zuzuordnen ist.
   */
  protected readonly werkzeugKontext = computed(() => {
    const d = this.daten();
    if (!d) return '';
    const strasse = [d.address.street, d.address.house_number].filter(Boolean).join(' ');
    const ort = [d.address.postal_code, d.address.city].filter(Boolean).join(' ');
    return [d.name, strasse, ort].filter(Boolean).join(', ');
  });

  protected readonly einheitenGesamt = computed(() => {
    const d = this.daten();
    if (!d) return 0;
    return d.buildings.reduce((n, b) => n + b.units.length, 0);
  });

  // --- Rechte (nur UI-Sichtbarkeit; der Server setzt sie durch) -----------
  // Gebäude und Einheiten anlegen: `require_scoped` — der Monteur darf das an
  // SEINEM Objekt (er misst nach, was wirklich dasteht). Deshalb `darf`.
  protected readonly darfAnlegen = computed(() => this.auth.darf('property', 'ANLEGEN'));
  /**
   * „Beteiligte(r) zuordnen": `darfAlle`, nicht `darf`.
   *
   * `POST /api/property/properties/{id}/parties` ist fail-closed (`require`) —
   * wer Eigentümer, Betreiber oder Hausmeisterei an ein Objekt hängt, ändert die
   * Stammdatenlage des Hauses, nicht seine eigene Zeile. Ein Konto mit row_scope
   * EIGENE bekommt dort 403.
   */
  protected readonly darfAendern = computed(() => this.auth.darfAlle('property', 'AENDERN'));

  /**
   * Struktur korrigieren (Gebäude/Einheit bearbeiten): `darf`, NICHT `darfAlle`.
   *
   * `PATCH /buildings/{id}` und `PATCH /units/{id}` stehen auf `require_scoped`
   * und lassen Scope EIGENE ausdrücklich zu — `guard_objekt` gibt an fremden
   * Objekten 404. Ein Hausmeister mit Objektsicht darf also an SEINEM Haus
   * korrigieren, und er ist genau die Zielgruppe von Befund I7 (das namenlose
   * Gebäude fällt dem auf, der davorsteht). Mit `darfAlle` bekäme er die
   * Knöpfe nicht zu sehen, obwohl die API sie ihm erlaubt.
   */
  protected readonly darfStrukturAendern = computed(() =>
    this.auth.darf('property', 'AENDERN'),
  );

  // --- Meldung + Dialoge ---------------------------------------------------
  protected readonly meldung = signal<Meldung | null>(null);
  protected readonly dialogLaedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);

  // Gebäude
  protected readonly gebaeudeOffen = signal(false);
  protected readonly gebaeudeForm = this.fb.group({
    building_number: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    name: this.fb.control('', { nonNullable: true }),
  });

  // Einheit (an ein Gebäude gebunden)
  protected readonly einheitOffen = signal(false);
  protected readonly einheitGebaeude = signal<{ id: string; label: string } | null>(null);
  protected readonly einheitForm = this.fb.group({
    unit_type: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    unit_number: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    storey: this.fb.control('', { nonNullable: true }),
  });

  // --- Struktur bearbeiten (AP1 / Befunde I1, I7, I12) ----------------------
  // Bis Migration 0124 war die Objektstruktur eine Einbahnstraße: angelegt =
  // endgültig. Ein ohne Bezeichnung erfasstes Gebäude blieb dauerhaft
  // „Gebäude 1", eine vertippte Einheitsnummer war nicht mehr zu retten. Beide
  // Dialoge sitzen bewusst im Struktur-Reiter selbst — die Korrektur gehört
  // dorthin, wo der Fehler auffällt, nicht in eine eigene Maske.

  /** Gebäude, das gerade bearbeitet wird (null = Dialog zu). */
  protected readonly gebaeudeBearbeiten = signal<Building | null>(null);
  protected readonly gebaeudeEditForm = this.fb.group({
    building_number: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, nichtNurLeerraumValidator],
    }),
    name: this.fb.control('', { nonNullable: true }),
  });

  // --- Räume im Strukturbaum (Befund I13) ---------------------------------
  //
  // Sascha: „Wirkt, als solle der User möglichst viel klicken statt zu
  // arbeiten." Gebäude anlegen, Einheit hinzufügen, Raum erstellen — alles
  // vorhanden, aber über drei bis sieben Reiter verstreut. Der Baum zeigt jetzt
  // alle drei Ebenen, und Räume lassen sich hier anlegen und umbenennen.
  //
  // Der Reiter „Räume" bleibt: Dort steht das **Aufmaß** (Fläche, Höhe,
  // Heizlast, Grundriss). Der Baum beantwortet „was gibt es", das Aufmaß „wie
  // groß ist es" — zwei Fragen, zwei Ansichten.

  protected readonly raeume = signal<Room[]>([]);
  /** Ließen sich die Räume nicht laden? Leer ist nicht dasselbe wie kaputt. */
  protected readonly raeumeFehler = signal(false);
  private raeumeGeladenFuer: string | null = null;

  /** Räume je Einheit — der Baum fragt danach, nicht nach einer flachen Liste. */
  protected readonly raeumeJeEinheit = computed(() => {
    const karte = new Map<string, Room[]>();
    for (const r of this.raeume()) {
      if (!r.unit_id) continue;
      const liste = karte.get(r.unit_id);
      if (liste) liste.push(r);
      else karte.set(r.unit_id, [r]);
    }
    return karte;
  });

  /**
   * Räume, die an KEINER Einheit hängen (`unit_id` ist nullable).
   *
   * Ein Heizungskeller gehört zum Gebäude, nicht zu einer Wohnung. Ohne diese
   * Gruppe verschwänden solche Räume aus dem Baum — sichtbar wären sie nur noch
   * im Aufmaß-Reiter, und niemand wüsste, dass es sie gibt.
   */
  protected readonly raeumeOhneEinheit = computed(() =>
    this.raeume().filter((r) => !r.unit_id),
  );

  /**
   * Dieselben Räume, aber nach Gebäude aufgeteilt.
   *
   * Solange man sie nur ansehen konnte, genügte eine flache Liste. Seit sie
   * hier **je Gebäude** angelegt werden, genügt sie nicht mehr: „Waschküche,
   * Trockenraum, Heizungskeller" in einem Topf beantwortet nicht, in welchem
   * Haus sie stehen — und bei Vorder- und Hinterhaus ist genau das die Frage.
   */
  protected readonly raeumeOhneEinheitJeGebaeude = computed(() => {
    const d = this.daten();
    // Die `id` ist der Track-Key im Template. Nicht das Label: `gebaeudeLabel`
    // fällt auf einen freien Namen zurück, und zwei Häuser einer Hofanlage
    // dürfen beide „Haus" heißen — doppelte Keys sind in `@for` ein
    // Laufzeitfehler. Die `building_id` ist eine UUID und damit eindeutig; der
    // leere String bleibt der Gruppe ohne Gebäude vorbehalten.
    const nach = new Map<string, { id: string; label: string; raeume: Room[] }>();
    for (const r of this.raeumeOhneEinheit()) {
      // Ein Raum kann auch am reinen Grundstück hängen (`building_id` null) —
      // etwa ein freistehender Geräteschuppen. Er bekommt eine eigene Gruppe
      // statt stillschweigend unter einem Gebäude zu landen.
      const key = r.building_id ?? '';
      if (!nach.has(key)) {
        const b = d?.buildings.find((x) => x.id === r.building_id);
        nach.set(key, {
          id: key,
          label: b ? this.gebaeudeLabel(b) : 'Ohne Gebäude (direkt an der Liegenschaft)',
          raeume: [],
        });
      }
      nach.get(key)!.raeume.push(r);
    }
    return [...nach.values()];
  });

  /**
   * Ziel des Anlege-Dialogs: die Einheit, das Gebäude darüber und eine
   * Beschriftung für den Titel.
   *
   * `unit` ist null, wenn der Raum am GEBÄUDE hängt (Heizungskeller,
   * Treppenhaus). Die `buildingId` steht trotzdem daneben: Ein `Unit` trägt
   * keine `building_id` — der Baum weiß aus seiner Verschachtelung, unter
   * welchem Gebäude die Zeile steht, und gibt sie mit.
   */
  protected readonly raumZuEinheit = signal<{
    unit: Unit | null;
    buildingId: string;
    label: string;
  } | null>(null);
  protected readonly raumBearbeiten = signal<Room | null>(null);
  /**
   * Umbenennen bekommt ein EIGENES Formular — nur der Name.
   *
   * Das Anlege-Formular mitzubenutzen war ein Fehler mit Folgen: Es trägt
   * Fläche und Höhe als Pflichtfelder, und beim Befüllen aus dem Raum landeten
   * die rohen API-Dezimalstrings darin („2.500"). Der `dezimalValidator` hält
   * die für mehrdeutig (Tausenderpunkt oder Dezimalpunkt?) — das Formular war
   * ungültig, das Speichern brach ab, und weil der Dialog die beiden Felder gar
   * nicht zeigt, passierte schlicht nichts. Stumm.
   */
  protected readonly raumNameForm = this.fb.group({
    name: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, nichtNurLeerraumValidator],
    }),
  });
  protected readonly raumForm = this.fb.group({
    name: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, nichtNurLeerraumValidator],
    }),
    // Fläche und Höhe sind NICHT optional: Die Datenbank verlangt beide als
    // NOT NULL mit CHECK > 0 (Migration 0086), weil `volume_m3` daraus
    // berechnet wird. Ein Raum „nur mit Namen" ist im Modell nicht vorgesehen.
    // Die Höhe ist mit 2,50 m vorbelegt — der Regelfall im Wohnungsbau; wer
    // eine Altbaudecke misst, überschreibt sie.
    // `erforderlichGetrimmt` statt `Validators.required`: Ein Feld mit einem
    // Leerzeichen gilt für `required` als gefüllt, wird aber zu einem leeren
    // Wert getrimmt — der Server antwortete dann mit einem Pydantic-Fehler
    // statt einer Feldmeldung. Die Meldung bleibt „Dieses Feld ist
    // erforderlich."; die Leerraum-Variante spräche an einem Zahlenfeld
    // unpassend von „Text".
    floor_area_m2: this.fb.control('', {
      nonNullable: true,
      validators: [erforderlichGetrimmt, dezimalValidator],
    }),
    room_height_m: this.fb.control('2,50', {
      nonNullable: true,
      validators: [erforderlichGetrimmt, dezimalValidator],
    }),
  });

  /** Einheit, die gerade bearbeitet wird (null = Dialog zu). */
  protected readonly einheitBearbeiten = signal<Unit | null>(null);
  protected readonly einheitEditForm = this.fb.group({
    unit_type: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    unit_number: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, nichtNurLeerraumValidator],
    }),
    storey: this.fb.control('', { nonNullable: true }),
  });

  // Beteiligte(r)
  protected readonly beteiligtOffen = signal(false);
  protected readonly parteiOptionen = signal<FeldOption[]>([]);
  protected readonly parteienLaedt = signal(false);
  protected readonly beteiligtForm = this.fb.group({
    party_id: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    role: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    valid_from: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    valid_until: this.fb.control('', { nonNullable: true }),
  });

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.tab.set('uebersicht');
      this.meldung.set(null);
      // Nebenlisten beim Objektwechsel zurücksetzen (lazy neu laden beim Öffnen).
      // reqId verwirft in-flight-Antworten des vorigen Objekts — sonst würde
      // eine späte Antwort den frisch zurückgesetzten idle-Zustand überschreiben.
      this.nebenReqId++;
      this.projekteState.set({ kind: 'idle' });
      this.vorgaengeState.set({ kind: 'idle' });
      this.auftraegeState.set({ kind: 'idle' });
      this.einsaetzeState.set({ kind: 'idle' });
      this.raeume.set([]);
      this.raeumeGeladenFuer = null;
      // Auch den Fehlerzustand zurücksetzen: Sonst stünde „Die Räume konnten
      // nicht geladen werden" über dem Baum des NÄCHSTEN Objekts, bei dem noch
      // gar nichts versucht wurde.
      this.raeumeFehler.set(false);
      if (!id) {
        this.state.set({ kind: 'error' });
        return;
      }
      this.load(id);
    });

    // Projekte/Vorgänge/Aufträge/Einsätze erst beim Öffnen des Tabs laden (lazy).
    effect(() => {
      const d = this.daten();
      if (!d) return;
      if (this.tab() !== 'vorgaenge') return;
      if (this.projekteState().kind === 'idle') this.ladeProjekte(d.id);
      if (this.vorgaengeState().kind === 'idle') this.ladeVorgaenge(d.id);
      if (this.auftraegeState().kind === 'idle') this.ladeAuftraege(d.id);
      if (this.einsaetzeState().kind === 'idle') this.ladeEinsaetze(d.id);
    });

    // Räume erst beim Öffnen der Struktur (Befund I13). Wer die Dokumente
    // aufschlägt, braucht keine Raumliste.
    effect(() => {
      const d = this.daten();
      if (!d || this.tab() !== 'struktur') return;
      this.raeumeLaden();
    });
  }

  /**
   * Aufträge und Einsätze an der Liegenschaft (Befunde C2/C3).
   *
   * Sascha wörtlich: „Vorgänge sind mittlerweile eher uninteressant […] Viel
   * wichtiger ist zu sehen, welche Aufträge schon in einer Liegenschaft
   * stattgefunden haben." Der Vorgang ist der optionale Eingangskorb; die
   * Historie eines Objekts steht in seinen Aufträgen und Einsätzen.
   *
   * Beide Listen sind absichtlich unbegrenzt auf 50 gedeckelt wie die
   * bestehenden — an der Objektmappe zählt der Überblick, nicht die
   * Vollständigkeit bis zurück zur Gründung.
   */
  private ladeAuftraege(propertyId: string): void {
    const rid = this.nebenReqId;
    this.auftraegeState.set({ kind: 'loading' });
    this.auftragSvc.list({ page: 1, page_size: 50, property_id: propertyId }).subscribe({
      next: (p) => {
        if (rid === this.nebenReqId) this.auftraegeState.set({ kind: 'ready', items: p.items });
      },
      error: (err) => {
        if (rid === this.nebenReqId) this.auftraegeState.set(fehlerState(err));
      },
    });
  }

  private ladeEinsaetze(propertyId: string): void {
    const rid = this.nebenReqId;
    this.einsaetzeState.set({ kind: 'loading' });
    // Der Filter `property_id` deckt BEIDE Wege ab: freier Termin direkt am
    // Objekt, auftragsgebundener über seinen Auftrag (siehe api/planung.py).
    this.einsatzSvc.list({ page: 1, page_size: 50, property_id: propertyId }).subscribe({
      next: (p) => {
        if (rid === this.nebenReqId) this.einsaetzeState.set({ kind: 'ready', items: p.items });
      },
      error: (err) => {
        if (rid === this.nebenReqId) this.einsaetzeState.set(fehlerState(err));
      },
    });
  }

  private ladeProjekte(propertyId: string): void {
    const rid = this.nebenReqId;
    this.projekteState.set({ kind: 'loading' });
    this.projektSvc.list({ page: 1, page_size: 50, property_id: propertyId }).subscribe({
      next: (p) => {
        if (rid === this.nebenReqId) this.projekteState.set({ kind: 'ready', items: p.items });
      },
      error: (err) => {
        if (rid === this.nebenReqId) this.projekteState.set(fehlerState(err));
      },
    });
  }

  private ladeVorgaenge(propertyId: string): void {
    const rid = this.nebenReqId;
    this.vorgaengeState.set({ kind: 'loading' });
    // include_terminal: an der Objektmappe interessiert die volle Historie, nicht
    // nur die offenen Vorgänge.
    this.projektSvc
      .listServiceCases({ page: 1, page_size: 50, property_id: propertyId, include_terminal: true })
      .subscribe({
        next: (b) => {
          if (rid === this.nebenReqId) this.vorgaengeState.set({ kind: 'ready', items: b.items });
        },
        error: (err) => {
          if (rid === this.nebenReqId) this.vorgaengeState.set(fehlerState(err));
        },
      });
  }

  retry(): void {
    const id = this.route.snapshot.paramMap.get('id');
    if (id) this.load(id);
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
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

  /** Detail neu laden, aktiven Tab beibehalten (nach Schreibaktion). */
  private reload(): void {
    const d = this.daten();
    if (d) this.load(d.id);
  }

  // ---- Gebäude anlegen ----------------------------------------------------
  gebaeudeOeffnen(): void {
    this.gebaeudeForm.reset({ building_number: '', name: '' });
    this.formularMeldung.set(null);
    this.gebaeudeOffen.set(true);
  }

  gebaeudeSchliessen(): void {
    if (this.dialogLaedt()) return;
    this.gebaeudeOffen.set(false);
  }

  gebaeudeAbsenden(): void {
    const d = this.daten();
    if (this.dialogLaedt() || !d) return;
    serverFehlerZuruecksetzen(this.gebaeudeForm);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.gebaeudeForm);
    if (this.gebaeudeForm.invalid) return;

    const v = this.gebaeudeForm.getRawValue();
    const payload: BuildingIn = {
      building_number: v.building_number.trim(),
      name: v.name.trim() || null,
    };
    this.dialogLaedt.set(true);
    this.svc.addBuilding(d.id, payload).subscribe({
      next: (b) => {
        this.dialogLaedt.set(false);
        this.gebaeudeOffen.set(false);
        this.meldung.set({ art: 'erfolg', text: `Gebäude ${b.building_number} wurde angelegt.` });
        this.reload();
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.gebaeudeForm).formular);
      },
    });
  }

  // ---- Einheit anlegen ----------------------------------------------------
  einheitOeffnen(buildingId: string, buildingLabel: string): void {
    this.einheitGebaeude.set({ id: buildingId, label: buildingLabel });
    this.einheitForm.reset({ unit_type: '', unit_number: '', storey: '' });
    this.formularMeldung.set(null);
    this.einheitOffen.set(true);
  }

  einheitSchliessen(): void {
    if (this.dialogLaedt()) return;
    this.einheitOffen.set(false);
  }

  /**
   * Serienanlage light (Befund I11): „Speichern und noch eine".
   *
   * Saschas Beispiel war „EG bis 4. OG, je vier Wohnungen" — heute 20
   * Dialogdurchläufe, bei denen der Einheitstyp 20-mal und die Etage
   * blockweise identisch ist und trotzdem jedes Mal neu gesetzt werden muss,
   * weil der Dialog schließt und ALLE Felder zurücksetzt.
   *
   * Hier bleibt der Dialog offen, Typ und Etage stehen, nur die Nummer wird
   * geleert und bekommt den Fokus. Halbiert den Aufwand, ohne einen
   * Bulk-Endpunkt zu brauchen (der wäre der nächste Schritt, mit Vorschau und
   * Kollisionsbehandlung gegen `UNIQUE (property_id, unit_number)`).
   */
  protected readonly einheitSerie = signal(false);

  einheitAbsendenUndWeiter(): void {
    this.einheitSerie.set(true);
    this.einheitAbsenden();
  }

  einheitAbsenden(): void {
    const geb = this.einheitGebaeude();
    if (this.dialogLaedt() || !geb) return;
    serverFehlerZuruecksetzen(this.einheitForm);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.einheitForm);
    if (this.einheitForm.invalid) return;

    const v = this.einheitForm.getRawValue();
    const payload: UnitIn = {
      unit_type: v.unit_type as UnitTypeCode,
      unit_number: v.unit_number.trim(),
      storey: v.storey.trim() || null,
    };
    this.dialogLaedt.set(true);
    this.svc.addUnit(geb.id, payload).subscribe({
      next: (u) => {
        this.dialogLaedt.set(false);
        this.meldung.set({ art: 'erfolg', text: `Einheit ${u.unit_number} wurde angelegt.` });
        if (this.einheitSerie()) {
          // Typ und Etage bleiben — genau die beiden Angaben, die bei einer
          // Serie konstant sind. Nur die Nummer ist je Einheit verschieden.
          this.einheitSerie.set(false);
          this.einheitForm.controls.unit_number.reset('');
          this.formularMeldung.set(null);
          queueMicrotask(() => this.einheitNummerFokussieren());
        } else {
          this.einheitOffen.set(false);
        }
        this.reload();
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.einheitSerie.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.einheitForm).formular);
      },
    });
  }

  /** Fokus zurück ins Nummernfeld — sonst müsste der Anwender hinklicken. */
  private einheitNummerFokussieren(): void {
    const feld = document.querySelector<HTMLInputElement>('#einheit-nummer input');
    feld?.focus();
  }

  // ---- Beteiligte(r) zuordnen --------------------------------------------
  beteiligtOeffnen(): void {
    this.beteiligtForm.reset({ party_id: '', role: '', valid_from: '', valid_until: '' });
    this.formularMeldung.set(null);
    this.beteiligtOffen.set(true);
    this.parteienLaden();
  }

  beteiligtSchliessen(): void {
    if (this.dialogLaedt()) return;
    this.beteiligtOffen.set(false);
  }

  private parteienLaden(): void {
    // Kontakte fuer die Auswahl laden (Personen und Organisationen). Ein
    // dedizierter Autocomplete-Baustein existiert noch nicht — deshalb eine
    // Select-Auswahl ueber die ersten 100 Kontakte (alphabetisch).
    this.parteienLaedt.set(true);
    this.parties.list({ page: 1, page_size: 100 }).subscribe({
      next: (page) => {
        this.parteienLaedt.set(false);
        this.parteiOptionen.set(
          page.items.map((p) => ({
            wert: p.id,
            label: `${p.display_name} (${p.party_type === 'PERSON' ? 'Person' : 'Organisation'})`,
          })),
        );
      },
      error: () => {
        this.parteienLaedt.set(false);
        this.parteiOptionen.set([]);
      },
    });
  }

  beteiligtAbsenden(): void {
    const d = this.daten();
    if (this.dialogLaedt() || !d) return;
    serverFehlerZuruecksetzen(this.beteiligtForm);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.beteiligtForm);
    if (this.beteiligtForm.invalid) return;

    const v = this.beteiligtForm.getRawValue();
    const payload: PartyRoleIn = {
      party_id: v.party_id,
      role: v.role as PropertyRoleCode,
      valid_from: v.valid_from,
      valid_until: v.valid_until || null,
    };
    this.dialogLaedt.set(true);
    this.svc.addPartyRole(d.id, payload).subscribe({
      next: (r) => {
        this.dialogLaedt.set(false);
        this.beteiligtOffen.set(false);
        this.meldung.set({
          art: 'erfolg',
          text: `${this.roleLabel(r.role)}: ${r.party_display_name} wurde zugeordnet.`,
        });
        this.reload();
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.beteiligtForm).formular);
      },
    });
  }

  // ---- Darstellungshelfer -------------------------------------------------
  typeLabel(t: PropertyType): string {
    switch (t) {
      case 'EINFAMILIENHAUS':
        return 'Einfamilienhaus';
      case 'WEG':
        return 'WEG';
      case 'RENTAL_PROPERTY':
        return 'Mietobjekt';
      case 'COMMERCIAL':
        return 'Gewerbe';
      case 'MIXED':
        return 'Gemischt';
      case 'OTHER':
        return 'Sonstige';
    }
  }

  statusLabel(s: PropertyStatus): string {
    return s === 'ACTIVE' ? 'Aktiv' : 'Inaktiv';
  }

  statusClass(s: PropertyStatus): string {
    return s === 'ACTIVE' ? 'stamp--positive' : '';
  }

  // --- Struktur bearbeiten: öffnen, absenden, schließen ---------------------

  gebaeudeEditOeffnen(b: Building): void {
    this.gebaeudeEditForm.reset({
      building_number: b.building_number,
      // `null` (keine Bezeichnung) wird zum leeren Feld — und ein leer
      // gelassenes Feld schickt beim Absenden wieder `null`. Das Löschen einer
      // Bezeichnung ist damit derselbe Handgriff wie das Nicht-Vergeben.
      name: b.name ?? '',
    });
    this.formularMeldung.set(null);
    this.gebaeudeBearbeiten.set(b);
  }

  gebaeudeEditSchliessen(): void {
    if (!this.dialogLaedt()) this.gebaeudeBearbeiten.set(null);
  }

  gebaeudeEditAbsenden(): void {
    const b = this.gebaeudeBearbeiten();
    if (this.dialogLaedt() || !b) return;
    serverFehlerZuruecksetzen(this.gebaeudeEditForm);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.gebaeudeEditForm);
    if (this.gebaeudeEditForm.invalid) return;

    const v = this.gebaeudeEditForm.getRawValue();
    const payload: BuildingPatch = {
      building_number: v.building_number.trim(),
      name: v.name.trim() || null,
    };
    this.dialogLaedt.set(true);
    this.svc.patchBuilding(b.id, payload).subscribe({
      next: (neu) => {
        this.dialogLaedt.set(false);
        this.gebaeudeBearbeiten.set(null);
        this.meldung.set({
          art: 'erfolg',
          text: `Gebäude ${neu.building_number} wurde geändert.`,
        });
        this.reload();
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.gebaeudeEditForm).formular);
      },
    });
  }

  einheitEditOeffnen(u: Unit): void {
    this.einheitEditForm.reset({
      unit_type: u.unit_type,
      unit_number: u.unit_number,
      storey: u.storey ?? '',
    });
    this.formularMeldung.set(null);
    this.einheitBearbeiten.set(u);
  }

  einheitEditSchliessen(): void {
    if (!this.dialogLaedt()) this.einheitBearbeiten.set(null);
  }

  // --- Räume im Baum (Befund I13) ------------------------------------------

  /** Fläche deutsch: „24.000" aus der API ist 24,0 m² — nicht 24 Tausend. */
  protected flaeche(wert: string | null): string {
    return apiZuDeAnzeige(wert, 1);
  }

  /**
   * Lädt die Räume der Liegenschaft — einmal, beim ersten Blick in die Struktur.
   *
   * Nicht mit der Mappe zusammen: Wer die Dokumente öffnet, braucht keine
   * Raumliste. Und nicht bei jedem Reiterwechsel: `raeumeGeladenFuer` merkt
   * sich das Objekt.
   */
  protected raeumeLaden(): void {
    const d = this.daten();
    if (!d || this.raeumeGeladenFuer === d.id) return;
    this.raeumeGeladenFuer = d.id;
    // `reqId` wie bei den Nachbarlisten: Springt der Nutzer auf ein anderes
    // Objekt, während die Anfrage läuft, trüge die verspätete Antwort sonst
    // die Räume des VORIGEN Objekts in diesen Baum — sichtbar würden vor allem
    // die ohne Einheit, denn die hängen an keiner Zeile, die fehlen könnte.
    const rid = this.nebenReqId;
    this.raumSvc.list(d.id).subscribe({
      next: (r) => {
        if (rid !== this.nebenReqId) return;
        this.raeume.set(r);
        this.raeumeFehler.set(false);
      },
      error: () => {
        if (rid !== this.nebenReqId) return;
        this.raeume.set([]);
        this.raeumeFehler.set(true);
        // Erneut versuchen erlauben: Ohne dieses Zurücksetzen bliebe die dritte
        // Ebene für die ganze Sitzung leer, und der Nutzer hielte das für den
        // Datenstand — und legte Dubletten an.
        this.raeumeGeladenFuer = null;
      },
    });
  }

  private raeumeNeuLaden(): void {
    this.raeumeGeladenFuer = null;
    this.raeumeLaden();
  }

  /** „Raum anlegen — Einheit WE 12" bzw. „… — Vorderhaus" (ohne Einheit). */
  protected readonly raumDialogTitel = computed(() => {
    const z = this.raumZuEinheit();
    if (!z) return 'Raum anlegen';
    return z.unit
      ? `Raum anlegen — Einheit ${z.unit.unit_number}`
      : `Raum anlegen — ${z.label}, ohne Einheit`;
  });

  raumOeffnen(unit: Unit | null, buildingId: string, label: string): void {
    this.raumForm.reset({ name: '', floor_area_m2: '', room_height_m: '2,50' });
    this.formularMeldung.set(null);
    this.raumZuEinheit.set({ unit, buildingId, label });
  }

  raumSchliessen(): void {
    if (!this.dialogLaedt()) this.raumZuEinheit.set(null);
  }

  raumAbsenden(): void {
    const ziel = this.raumZuEinheit();
    const d = this.daten();
    if (!ziel || !d || this.dialogLaedt()) return;
    serverFehlerZuruecksetzen(this.raumForm);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.raumForm);
    if (this.raumForm.invalid) return;

    this.dialogLaedt.set(true);
    const v = this.raumForm.getRawValue();
    this.raumSvc
      .create(d.id, {
        name: v.name.trim(),
        floor_area_m2: deZuApiDezimal(v.floor_area_m2),
        room_height_m: deZuApiDezimal(v.room_height_m),
        building_id: ziel.buildingId,
        unit_id: ziel.unit?.id ?? null,
        // Die Etage der Einheit erbt der Raum — sie ist dieselbe, und sie
        // zweimal einzutippen ist genau die Sorte Arbeit, die dieser Befund
        // abschaffen soll. Ohne Einheit gibt es keine zu erben.
        storey: ziel.unit?.storey ?? null,
      })
      .subscribe({
        next: (r) => {
          this.dialogLaedt.set(false);
          this.raumZuEinheit.set(null);
          this.meldung.set({ art: 'erfolg', text: `Raum „${r.name}" wurde angelegt.` });
          this.raeumeNeuLaden();
        },
        error: (err) => {
          this.dialogLaedt.set(false);
          this.formularMeldung.set(apiFehlerZuweisen(err, this.raumForm).formular);
        },
      });
  }

  raumEditOeffnen(r: Room): void {
    this.raumNameForm.reset({ name: r.name });
    this.formularMeldung.set(null);
    this.raumBearbeiten.set(r);
  }

  raumEditSchliessen(): void {
    if (!this.dialogLaedt()) this.raumBearbeiten.set(null);
  }

  raumEditAbsenden(): void {
    const r = this.raumBearbeiten();
    if (!r || this.dialogLaedt()) return;
    serverFehlerZuruecksetzen(this.raumNameForm);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.raumNameForm);
    if (this.raumNameForm.invalid) return;

    this.dialogLaedt.set(true);
    this.raumSvc
      .update(r.id, { name: this.raumNameForm.getRawValue().name.trim() })
      .subscribe({
        next: (neu) => {
          this.dialogLaedt.set(false);
          this.raumBearbeiten.set(null);
          this.meldung.set({ art: 'erfolg', text: `Raum heißt jetzt „${neu.name}".` });
          this.raeumeNeuLaden();
        },
        error: (err) => {
          this.dialogLaedt.set(false);
          this.formularMeldung.set(apiFehlerZuweisen(err, this.raumNameForm).formular);
        },
      });
  }

  einheitEditAbsenden(): void {
    const u = this.einheitBearbeiten();
    if (this.dialogLaedt() || !u) return;
    serverFehlerZuruecksetzen(this.einheitEditForm);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.einheitEditForm);
    if (this.einheitEditForm.invalid) return;

    const v = this.einheitEditForm.getRawValue();
    const payload: UnitPatch = {
      unit_type: v.unit_type as UnitTypeCode,
      unit_number: v.unit_number.trim(),
      // Leeres Feld = „nicht erfasst". Die DB verbietet den Leerstring
      // (CHECK unit_storey_nicht_leer), NULL ist der richtige Wert dafür.
      storey: v.storey.trim() || null,
    };
    this.dialogLaedt.set(true);
    this.svc.patchUnit(u.id, payload).subscribe({
      next: (neu) => {
        this.dialogLaedt.set(false);
        this.einheitBearbeiten.set(null);
        this.meldung.set({ art: 'erfolg', text: `Einheit ${neu.unit_number} wurde geändert.` });
        this.reload();
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.einheitEditForm).formular);
      },
    });
  }

  auftragStatusLabel(s: string): string {
    return workOrderStatusLabel(s as never);
  }
  einsatzStatusLabel(s: string): string {
    return serviceJobStatusLabel(s as never);
  }
  /** Planbeginn eines Einsatzes, oder „ohne Termin". */
  einsatzZeit(iso: string | null): string {
    return iso ? new Date(iso).toLocaleString('de-DE', {
      day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    }) : 'ohne Termin';
  }

  /** Anzeigename eines Gebäudes — ohne Bezeichnung greift die Nummer. */
  gebaeudeLabel(b: Building): string {
    return b.name || `Gebäude ${b.building_number}`;
  }

  unitTypeLabel(t: string): string {
    const map: Record<string, string> = {
      APARTMENT: 'Wohnung',
      COMMERCIAL: 'Gewerbe',
      GARAGE: 'Garage',
      PARKING: 'Stellplatz',
      STORAGE: 'Lager',
      COMMON_AREA: 'Gemeinschaft',
      TECHNICAL_ROOM: 'Technikraum',
      OTHER: 'Sonstige',
    };
    return map[t] ?? t;
  }

  roleLabel(r: PropertyRoleCode): string {
    const map: Record<PropertyRoleCode, string> = {
      COMMUNITY_OF_OWNERS: 'Eigentümergemeinschaft',
      PROPERTY_OWNER: 'Eigentümer',
      OPERATOR: 'Betreiber',
      CARETAKER: 'Hausmeisterei',
    };
    return map[r] ?? r;
  }

  // ---- Projekte & Vorgänge ------------------------------------------------
  projektStatusLabel(s: ProjectStatus): string {
    return s === 'OPEN' ? 'Offen' : 'Geschlossen';
  }
  projektStatusClass(s: ProjectStatus): string {
    return s === 'OPEN' ? 'stamp--positive' : '';
  }

  caseStatusLabel(s: ServiceCaseStatus): string {
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
  caseStatusClass(s: ServiceCaseStatus): string {
    if (s === 'ABGESCHLOSSEN') return 'stamp--positive';
    if (s === 'ABGELEHNT') return 'stamp--warn';
    return '';
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
}
