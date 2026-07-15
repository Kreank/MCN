import { Component, computed, effect, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { map } from 'rxjs';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { PartyService } from '../../core/party.service';
import { AufgabeService } from '../../core/aufgabe.service';
import { FirmaService } from '../../core/firma.service';
import { AuthService } from '../../core/auth.service';
import { AcquisitionSource } from '../../core/firma.model';
import { Task, TaskStatus } from '../../core/aufgabe.model';
import {
  Address,
  AddressIn,
  AddressTypeCode,
  ContactPerson,
  ContactPersonIn,
  ContactPoint,
  ContactPointIn,
  ContactTypeCode,
  PartyAddress,
  PartyDetail,
  PartyStatus,
  PartyType,
} from '../../core/party.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { Dateien } from '../../shared/dateien/dateien';
import { ZielFilter } from '../../core/datei.model';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';
import { Dialog } from '../../shared/dialog/dialog';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { ReferenzWahl, RefSuche } from '../../shared/formular/referenz-wahl';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';

type Meldung = { art: 'erfolg' | 'fehler'; text: string };
type DialogArt = 'kontaktweg' | 'adresse' | 'ansprechpartner';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: PartyDetail }
  | VerbotenState
  | { kind: 'error' };

type LazyState<T> =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; items: T[] }
  | VerbotenState
  | { kind: 'error' };

@Component({
  selector: 'app-kontakt-detail',
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
  ],
  templateUrl: './kontakt-detail.html',
  styleUrl: './kontakt-detail.scss',
})
export class KontaktDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly svc = inject(PartyService);
  private readonly aufgabeSvc = inject(AufgabeService);
  private readonly firmaSvc = inject(FirmaService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  protected readonly darfAnlegen = computed(() => this.auth.darf('identity', 'ANLEGEN'));
  protected readonly darfAendern = computed(() => this.auth.darf('identity', 'AENDERN'));

  protected readonly tab = signal('stammdaten');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly contactPointsState = signal<LazyState<ContactPoint>>({ kind: 'idle' });
  protected readonly addressesState = signal<LazyState<PartyAddress>>({ kind: 'idle' });
  protected readonly contactPersonsState = signal<LazyState<ContactPerson>>({ kind: 'idle' });
  protected readonly tasksState = signal<LazyState<Task>>({ kind: 'idle' });
  private reqId = 0;
  private aufgabenReqId = 0;
  // Generations-Guards: die Kontaktwege/Adressen speisen die dauersichtbare
  // Kopf-Karte und werden bei jedem Kontaktwechsel eager geladen. Ohne Guard
  // könnte eine verspätete Antwort des vorigen Kontakts die Karte mit den
  // FALSCHEN Kontaktdaten stehen lassen (man riefe die falsche Person an).
  private kontaktwegeReqId = 0;
  private adressenReqId = 0;

  // Aufgaben-Tab: Segment-Filter wie die Hauptliste (Alle/Offen/Erledigt).
  protected readonly aufgabenSegmente: { value: TaskStatus | null; label: string }[] = [
    { value: null, label: 'Alle' },
    { value: 'OFFEN', label: 'Offen' },
    { value: 'ERLEDIGT', label: 'Erledigt' },
  ];
  protected readonly aufgabenStatus = signal<TaskStatus | null>(null);

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  protected readonly istOrganisation = computed(
    () => this.daten()?.party_type === 'ORGANIZATION',
  );

  /** Aktive Akquisekanäle für die Auswahl am Kontakt. */
  protected readonly akquiseQuellen = signal<AcquisitionSource[]>([]);
  protected readonly darfQuelleAendern = computed(() => this.auth.darf('identity', 'AENDERN'));
  protected readonly quelleLaedt = signal(false);

  /** Auswahloptionen: aktive Kanäle plus der aktuell gesetzte, falls dieser
   * zwischenzeitlich deaktiviert wurde (sonst fiele er wortlos aus der Liste). */
  protected readonly quelleOptionen = computed<AcquisitionSource[]>(() => {
    const aktive = this.akquiseQuellen();
    const aktuell = this.daten()?.acquisition_source;
    if (aktuell && !aktive.some((q) => q.id === aktuell.id)) {
      return [
        ...aktive,
        { ...aktuell, label: `${aktuell.label} (inaktiv)`, active: false, sort_order: 0 },
      ];
    }
    return aktive;
  });

  /** Ansprechpartner-Tab nur bei Organisationen (bei Personen sinnlos). */
  protected readonly tabs = computed<MappeTab[]>(() => {
    const basis: MappeTab[] = [
      { id: 'stammdaten', label: 'Stammdaten' },
      { id: 'objektadressen', label: 'Objektadressen' },
    ];
    if (this.istOrganisation()) {
      basis.push({ id: 'ansprechpartner', label: 'Ansprechpartner' });
    }
    basis.push(
      { id: 'aufgaben', label: 'Aufgaben' },
      // Kontakte-1: Der frühere „Dokumente"-Platzhalter ist entfernt. Ein zweiter
      // Datei-Bereich am selben party_id waere reine Redundanz mit doppeltem
      // Upload-Weg; der Dateien-Tab (app-dateien) ist die EINE Ablage — er zeigt
      // jede Datei mit ihrem Kategorie-Stempel (Dokument/Vertrag/Beleg …).
      { id: 'dateien', label: 'Dokumente & Dateien' },
      { id: 'logbuch', label: 'Logbuch' },
    );
    return basis;
  });

  /** Stabile Zielreferenz fuer den Dateien-Tab (nur bei Kontaktwechsel neu). */
  protected readonly dateienZiel = computed<ZielFilter>(() => ({
    party_id: this.daten()?.id ?? '',
  }));

  // --- Kontaktkarte (Kontakte-2): tab-unabhaengig im Mappen-Kopf -----------
  /** Primaerer Kommunikationsweg fuer die Kopf-Karte (sonst der erste aktive). */
  protected readonly kartenKontakt = computed<ContactPoint | null>(() => {
    const s = this.contactPointsState();
    if (s.kind !== 'ready') return null;
    return s.items.find((c) => c.is_primary) ?? s.items[0] ?? null;
  });
  /** Primaere Adresse fuer die Kopf-Karte (sonst die erste vorhandene). */
  protected readonly kartenAdresse = computed<PartyAddress | null>(() => {
    const s = this.addressesState();
    if (s.kind !== 'ready') return null;
    return s.items.find((a) => a.is_primary) ?? s.items[0] ?? null;
  });

  // --- Schreibaktionen (Dialoge) ------------------------------------------
  protected readonly meldung = signal<Meldung | null>(null);
  protected readonly dialogOffen = signal<DialogArt | null>(null);
  protected readonly dialogLaedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);

  // Bestaetigung (Deaktivieren/Entfernen)
  protected readonly bestaetigung = signal<
    | { art: 'kontaktweg'; id: string; text: string }
    | { art: 'ansprechpartner'; id: string; text: string }
    | null
  >(null);
  protected readonly bestaetigungLaedt = signal(false);

  protected readonly kontaktTypen: FeldOption[] = [
    { wert: 'EMAIL', label: 'E-Mail' },
    { wert: 'PHONE', label: 'Telefon' },
    { wert: 'MOBILE', label: 'Mobil' },
    { wert: 'FAX', label: 'Fax' },
    { wert: 'PORTAL', label: 'Portal' },
  ];
  protected readonly adressTypen: FeldOption[] = [
    { wert: 'BUSINESS', label: 'Geschäftsadresse' },
    { wert: 'POSTAL', label: 'Postanschrift' },
    { wert: 'BILLING', label: 'Rechnungsadresse' },
    { wert: 'PRIVATE', label: 'Privatadresse' },
  ];
  protected readonly quellen: FeldOption[] = [
    { wert: 'bestehend', label: 'Bestehende Person zuordnen' },
    { wert: 'neu', label: 'Neue Person anlegen' },
  ];

  /** Personensuche fuer die Zuordnung eines bestehenden Ansprechpartners. */
  protected readonly personSuche: RefSuche = (q) =>
    this.svc.list({ page: 1, page_size: 20, q, party_type: 'PERSON' }).pipe(
      map((p) => p.items.map((x) => ({ id: x.id, label: x.display_name }))),
    );

  protected readonly kontaktwegForm = this.fb.group({
    contact_type: this.fb.control('EMAIL', { nonNullable: true, validators: [Validators.required] }),
    value: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    label: this.fb.control('', { nonNullable: true }),
    is_primary: this.fb.control(false, { nonNullable: true }),
  });
  protected readonly adresseForm = this.fb.group({
    address_type: this.fb.control('BUSINESS', { nonNullable: true, validators: [Validators.required] }),
    street: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    house_number: this.fb.control('', { nonNullable: true }),
    postal_code: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    city: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    address_addition: this.fb.control('', { nonNullable: true }),
    is_primary: this.fb.control(true, { nonNullable: true }),
  });
  protected readonly ansprechForm = this.fb.group({
    quelle: this.fb.control('bestehend', { nonNullable: true, validators: [Validators.required] }),
    person_party_id: this.fb.control('', { nonNullable: true }),
    first_name: this.fb.control('', { nonNullable: true }),
    last_name: this.fb.control('', { nonNullable: true }),
    salutation: this.fb.control('', { nonNullable: true }),
    title: this.fb.control('', { nonNullable: true }),
  });

  protected readonly ansprechNeu = computed(() => this.ansprechForm.controls.quelle.value === 'neu');

  constructor() {
    // Aktive Akquisekanäle einmal laden (für die Quelle-Auswahl im Stammdaten-Tab).
    this.firmaSvc.listAcquisitionSources(false).subscribe({
      next: (q) => this.akquiseQuellen.set(q),
      error: () => this.akquiseQuellen.set([]),
    });

    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.tab.set('stammdaten');
      this.contactPointsState.set({ kind: 'idle' });
      this.addressesState.set({ kind: 'idle' });
      this.contactPersonsState.set({ kind: 'idle' });
      this.tasksState.set({ kind: 'idle' });
      this.aufgabenStatus.set(null);
      this.meldung.set(null);
      if (!id) {
        this.state.set({ kind: 'error' });
        return;
      }
      this.load(id);
    });

    // Kontaktwege und Adressen laden wir tab-unabhaengig, sobald der Kontakt da
    // ist: der Stammdaten-/Objektadressen-Tab UND die Kopf-Karte (Kontakte-2)
    // brauchen sie. Ansprechpartner und Aufgaben bleiben je Tab lazy.
    effect(() => {
      const d = this.daten();
      if (!d) return;
      const t = this.tab();
      if (this.contactPointsState().kind === 'idle') {
        this.loadContactPoints(d.id);
      }
      if (this.addressesState().kind === 'idle') {
        this.loadAddresses(d.id);
      }
      if (
        t === 'ansprechpartner' &&
        this.istOrganisation() &&
        this.contactPersonsState().kind === 'idle'
      ) {
        this.loadContactPersons(d.id);
      }
      if (t === 'aufgaben' && this.tasksState().kind === 'idle') {
        this.loadTasks(d.id);
      }
    });
  }

  /**
   * Aufgaben dieses Kontakts laden (Filter party_id). Recht workflow/LESEN; ein
   * Monteur (row_scope EIGENE) sieht nur die ihm zugewiesenen — fail-closed
   * korrekt. reqId-Guard gegen Races bei schnellem Segmentwechsel.
   */
  private loadTasks(partyId: string): void {
    const rid = ++this.aufgabenReqId;
    this.tasksState.set({ kind: 'loading' });
    this.aufgabeSvc
      .list({ page: 1, page_size: 50, party_id: partyId, status: this.aufgabenStatus() })
      .subscribe({
        next: (d) => {
          if (rid === this.aufgabenReqId) this.tasksState.set({ kind: 'ready', items: d.items });
        },
        error: (err) => {
          if (rid === this.aufgabenReqId) this.tasksState.set(fehlerState(err));
        },
      });
  }

  aufgabenSegmentWaehlen(value: TaskStatus | null): void {
    if (this.aufgabenStatus() === value) return;
    this.aufgabenStatus.set(value);
    const d = this.daten();
    if (d) this.loadTasks(d.id);
  }

  private loadContactPoints(partyId: string): void {
    const rid = ++this.kontaktwegeReqId;
    this.contactPointsState.set({ kind: 'loading' });
    this.svc.listContactPoints(partyId).subscribe({
      next: (items) => {
        if (rid === this.kontaktwegeReqId) this.contactPointsState.set({ kind: 'ready', items });
      },
      error: (err) => {
        if (rid === this.kontaktwegeReqId) this.contactPointsState.set(fehlerState(err));
      },
    });
  }

  private loadAddresses(partyId: string): void {
    const rid = ++this.adressenReqId;
    this.addressesState.set({ kind: 'loading' });
    this.svc.listAddresses(partyId).subscribe({
      next: (items) => {
        if (rid === this.adressenReqId) this.addressesState.set({ kind: 'ready', items });
      },
      error: (err) => {
        if (rid === this.adressenReqId) this.addressesState.set(fehlerState(err));
      },
    });
  }

  private loadContactPersons(partyId: string): void {
    this.contactPersonsState.set({ kind: 'loading' });
    this.svc.listContactPersons(partyId).subscribe({
      next: (items) => this.contactPersonsState.set({ kind: 'ready', items }),
      error: (err) => this.contactPersonsState.set(fehlerState(err)),
    });
  }

  // --- Dialoge öffnen/schließen -------------------------------------------
  dialogOeffnen(art: DialogArt): void {
    this.formularMeldung.set(null);
    switch (art) {
      case 'kontaktweg':
        this.kontaktwegForm.reset({ contact_type: 'EMAIL', value: '', label: '', is_primary: false });
        break;
      case 'adresse':
        this.adresseForm.reset({
          address_type: 'BUSINESS', street: '', house_number: '',
          postal_code: '', city: '', address_addition: '', is_primary: true,
        });
        break;
      case 'ansprechpartner':
        this.ansprechForm.reset({
          quelle: 'bestehend', person_party_id: '', first_name: '',
          last_name: '', salutation: '', title: '',
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

  private nichtBereit(form: Parameters<typeof serverFehlerZuruecksetzen>[0]): boolean {
    if (this.dialogLaedt()) return true;
    serverFehlerZuruecksetzen(form);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(form);
    return form.invalid;
  }

  // --- Kommunikationsweg ---------------------------------------------------
  kontaktwegAbsenden(): void {
    const d = this.daten();
    if (!d || this.nichtBereit(this.kontaktwegForm)) return;
    const v = this.kontaktwegForm.getRawValue();
    const payload: ContactPointIn = {
      contact_type: v.contact_type as ContactTypeCode,
      value: v.value.trim(),
      label: v.label.trim() || null,
      is_primary: v.is_primary,
    };
    this.dialogLaedt.set(true);
    this.svc.createContactPoint(d.id, payload).subscribe({
      next: () => {
        this.dialogLaedt.set(false);
        this.dialogOffen.set(null);
        this.meldung.set({ art: 'erfolg', text: 'Kommunikationsweg hinzugefügt.' });
        this.loadContactPoints(d.id);
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.kontaktwegForm).formular);
      },
    });
  }

  // --- Adresse -------------------------------------------------------------
  adresseAbsenden(): void {
    const d = this.daten();
    if (!d || this.nichtBereit(this.adresseForm)) return;
    const v = this.adresseForm.getRawValue();
    const payload: AddressIn = {
      address_type: v.address_type as AddressTypeCode,
      street: v.street.trim(),
      postal_code: v.postal_code.trim(),
      city: v.city.trim(),
      house_number: v.house_number.trim() || null,
      address_addition: v.address_addition.trim() || null,
      is_primary: v.is_primary,
    };
    this.dialogLaedt.set(true);
    this.svc.createAddress(d.id, payload).subscribe({
      next: () => {
        this.dialogLaedt.set(false);
        this.dialogOffen.set(null);
        this.meldung.set({ art: 'erfolg', text: 'Adresse hinzugefügt.' });
        this.loadAddresses(d.id);
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.adresseForm).formular);
      },
    });
  }

  // --- Ansprechpartner -----------------------------------------------------
  ansprechAbsenden(): void {
    const d = this.daten();
    if (!d) return;
    // Feldpflichten je nach Quelle setzen, bevor validiert wird.
    const neu = this.ansprechNeu();
    const c = this.ansprechForm.controls;
    c.person_party_id.setValidators(neu ? [] : [Validators.required]);
    c.first_name.setValidators(neu ? [Validators.required] : []);
    c.last_name.setValidators(neu ? [Validators.required] : []);
    c.person_party_id.updateValueAndValidity();
    c.first_name.updateValueAndValidity();
    c.last_name.updateValueAndValidity();
    if (this.nichtBereit(this.ansprechForm)) return;
    const v = this.ansprechForm.getRawValue();
    const payload: ContactPersonIn = neu
      ? {
          first_name: v.first_name.trim(),
          last_name: v.last_name.trim(),
          salutation: v.salutation.trim() || null,
          title: v.title.trim() || null,
        }
      : { person_party_id: v.person_party_id };
    this.dialogLaedt.set(true);
    this.svc.createContactPerson(d.id, payload).subscribe({
      next: () => {
        this.dialogLaedt.set(false);
        this.dialogOffen.set(null);
        this.meldung.set({ art: 'erfolg', text: 'Ansprechpartner zugeordnet.' });
        this.loadContactPersons(d.id);
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.ansprechForm).formular);
      },
    });
  }

  // --- Deaktivieren / Entfernen (mit Bestätigung) --------------------------
  kontaktwegDeaktivierenFragen(cp: ContactPoint): void {
    this.bestaetigung.set({
      art: 'kontaktweg',
      id: cp.id,
      text: `Der Kommunikationsweg „${cp.value}“ wird als beendet markiert und aus der aktiven Liste entfernt.`,
    });
  }

  ansprechEntfernenFragen(ap: ContactPerson): void {
    this.bestaetigung.set({
      art: 'ansprechpartner',
      id: ap.relationship_id,
      text: `„${ap.display_name}“ wird als Ansprechpartner beendet. Die Person selbst bleibt als Kontakt erhalten.`,
    });
  }

  bestaetigungAbbrechen(): void {
    if (this.bestaetigungLaedt()) return;
    this.bestaetigung.set(null);
  }

  bestaetigungAusfuehren(): void {
    const b = this.bestaetigung();
    const d = this.daten();
    if (!b || !d) return;
    this.bestaetigungLaedt.set(true);
    const fertig = (text: string) => {
      this.bestaetigungLaedt.set(false);
      this.bestaetigung.set(null);
      this.meldung.set({ art: 'erfolg', text });
    };
    const fehler = (err: unknown) => {
      this.bestaetigungLaedt.set(false);
      this.bestaetigung.set(null);
      const m = (err as { error?: { detail?: string } })?.error?.detail;
      this.meldung.set({ art: 'fehler', text: m || 'Die Aktion ist fehlgeschlagen.' });
    };
    if (b.art === 'kontaktweg') {
      this.svc.deactivateContactPoint(d.id, b.id).subscribe({
        next: () => {
          fertig('Kommunikationsweg beendet.');
          this.loadContactPoints(d.id);
        },
        error: fehler,
      });
    } else {
      this.svc.removeContactPerson(d.id, b.id).subscribe({
        next: () => {
          fertig('Ansprechpartner entfernt.');
          this.loadContactPersons(d.id);
        },
        error: fehler,
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
        if (rid !== this.reqId) return;
        this.state.set({ kind: 'ready', data });
        // Kontakte-9: Aus der Org-Anlage übergeleitet („… und Ansprechpartner
        // hinzufügen") — direkt auf dem Ansprechpartner-Tab den Dialog öffnen.
        // Nur bei Organisationen sinnvoll (Personen haben keinen AP-Tab).
        if (
          data.party_type === 'ORGANIZATION' &&
          this.route.snapshot.queryParamMap.get('neu') === 'ansprechpartner'
        ) {
          this.tab.set('ansprechpartner');
          this.dialogOeffnen('ansprechpartner');
          // Query-Param entfernen, sonst öffnet sich der Dialog bei Reload/Zurück
          // erneut. replaceUrl: kein zusätzlicher History-Eintrag.
          this.router.navigate([], {
            relativeTo: this.route,
            queryParams: {},
            replaceUrl: true,
          });
        }
      },
      error: (err) => {
        if (rid === this.reqId) this.state.set(fehlerState(err));
      },
    });
  }

  /** Akquisekanal des Kontakts setzen/ändern (leerer Wert löst ihn). */
  quelleSetzen(sourceId: string): void {
    const d = this.daten();
    if (!d || this.quelleLaedt() || !this.darfQuelleAendern()) return;
    const ziel = sourceId || null;
    if ((d.acquisition_source?.id ?? null) === ziel) return;
    this.quelleLaedt.set(true);
    this.svc.setAcquisitionSource(d.id, ziel).subscribe({
      next: (data) => {
        this.quelleLaedt.set(false);
        if (this.state().kind === 'ready') this.state.set({ kind: 'ready', data });
      },
      error: (err) => {
        this.quelleLaedt.set(false);
        this.meldung.set({ art: 'fehler', text: fehlerDetail(err) ?? 'Der Akquisekanal konnte nicht gesetzt werden.' });
      },
    });
  }

  // ---- Darstellungshelfer -------------------------------------------------
  typeLabel(t: PartyType): string {
    return t === 'PERSON' ? 'Person' : 'Organisation';
  }

  statusLabel(s: PartyStatus): string {
    switch (s) {
      case 'ACTIVE':
        return 'Aktiv';
      case 'INACTIVE':
        return 'Inaktiv';
      case 'MERGED':
        return 'Zusammengeführt';
    }
  }

  statusClass(s: PartyStatus): string {
    switch (s) {
      case 'ACTIVE':
        return 'stamp--positive';
      case 'MERGED':
        return 'stamp--warn';
      default:
        return '';
    }
  }

  orgTypeLabel(t: string): string {
    const map: Record<string, string> = {
      PROPERTY_MANAGEMENT: 'Hausverwaltung',
      WEG: 'WEG',
      COMPANY: 'Firma',
      AUTHORITY: 'Behörde',
      INSURER: 'Versicherer',
      OTHER: 'Sonstige',
    };
    return map[t] ?? t;
  }

  contactTypeLabel(t: ContactTypeCode): string {
    const map: Record<ContactTypeCode, string> = {
      EMAIL: 'E-Mail',
      PHONE: 'Telefon',
      MOBILE: 'Mobil',
      FAX: 'Fax',
      PORTAL: 'Portal',
    };
    return map[t] ?? t;
  }

  addressTypeLabel(t: AddressTypeCode): string {
    const map: Record<AddressTypeCode, string> = {
      BUSINESS: 'Geschäftsadresse',
      POSTAL: 'Postanschrift',
      BILLING: 'Rechnungsadresse',
      PRIVATE: 'Privatadresse',
    };
    return map[t] ?? t;
  }

  adresseZeile(a: Address): string {
    const strasse = [a.street, a.house_number].filter(Boolean).join(' ');
    const ort = [a.postal_code, a.city].filter(Boolean).join(' ');
    return [strasse, a.address_addition, ort].filter(Boolean).join(', ');
  }

  /**
   * `mailto:`/`tel:`-Ziel fuer die Kopf-Karte, oder null wenn der Typ nicht
   * verlinkbar ist (PORTAL). Telefonnummern werden fuer das `tel:`-Schema auf
   * Ziffern und ein fuehrendes „+" reduziert (Anzeige bleibt der Rohwert).
   */
  kontaktHref(cp: ContactPoint): string | null {
    switch (cp.contact_type) {
      case 'EMAIL':
        return `mailto:${cp.value.trim()}`;
      case 'PHONE':
      case 'MOBILE':
      case 'FAX': {
        const tel = cp.value.replace(/[^\d+]/g, '');
        return tel ? `tel:${tel}` : null;
      }
      default:
        return null;
    }
  }

  /** Kompaktes Chip-Label „N Vorgänge" (Kontakte-8, korrekter Singular). */
  vorgangLabel(n: number): string {
    return `${n} ${n === 1 ? 'Vorgang' : 'Vorgänge'}`;
  }

  /** Ausführlicher Titel für den Chip (a11y: Text statt reiner Zahl/Farbe). */
  vorgangTitel(n: number): string {
    return n === 1
      ? 'Diese Person hat 1 Vorgang gemeldet.'
      : `Diese Person hat ${n} Vorgänge gemeldet.`;
  }

  // ---- Aufgaben-Darstellung ----------------------------------------------
  taskStatusLabel(s: TaskStatus): string {
    switch (s) {
      case 'OFFEN':
        return 'Offen';
      case 'ERLEDIGT':
        return 'Erledigt';
      case 'VERWORFEN':
        return 'Verworfen';
    }
  }

  taskStatusClass(s: TaskStatus): string {
    if (s === 'ERLEDIGT') return 'stamp--positive';
    if (s === 'VERWORFEN') return 'stamp--warn';
    return '';
  }

  private readonly datumFormat = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  });

  /** ISO-Datum (YYYY-MM-DD) als de-DE. Lokale Konstruktion vermeidet TZ-Versatz. */
  datumDe(iso: string | null): string {
    if (!iso) return '';
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
    if (!m) return iso;
    const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
    return isNaN(d.getTime()) ? iso : this.datumFormat.format(d);
  }
}
