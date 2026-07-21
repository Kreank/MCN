import {
  Component,
  DestroyRef,
  WritableSignal,
  computed,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Router, RouterLink } from '@angular/router';
import {
  FormBuilder,
  FormControl,
  ReactiveFormsModule,
  Validators,
} from '@angular/forms';
import { Subject, debounceTime, distinctUntilChanged } from 'rxjs';
import { PartyService } from '../../core/party.service';
import { AuthService } from '../../core/auth.service';
import {
  AdresseIn,
  KontaktwegeIn,
  OrganizationIn,
  OrganizationTypeCode,
  Party,
  PartyPage,
  PartyStatus,
  PartyType,
  PersonIn,
} from '../../core/party.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import { erforderlichGetrimmt } from '../../shared/formular/text';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: PartyPage }
  | VerbotenState
  | { kind: 'error' };

type Segment = { value: PartyType | null; label: string };
// folgeOrgId: nach erfolgreicher Org-Anlage die neue Party-ID — die Meldung
// bietet dann „… und Ansprechpartner hinzufügen" an (Kontakte-9). Nur bei
// Organisationen gesetzt, nie bei Personen.
type Meldung = { art: 'erfolg' | 'fehler'; text: string; folgeOrgId?: string };

@Component({
  selector: 'app-kontakte',
  imports: [RouterLink, ReactiveFormsModule, KeinZugriff, Dialog, Feld],
  templateUrl: './kontakte.html',
  styleUrl: './kontakte.scss',
})
export class Kontakte {
  private readonly svc = inject(PartyService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);
  private readonly router = inject(Router);

  protected readonly pageSize = 20;
  protected readonly segments: Segment[] = [
    { value: null, label: 'Alle' },
    { value: 'PERSON', label: 'Personen' },
    { value: 'ORGANIZATION', label: 'Organisationen' },
  ];

  protected readonly orgTypen: FeldOption[] = [
    { wert: 'PROPERTY_MANAGEMENT', label: 'Hausverwaltung' },
    { wert: 'WEG', label: 'WEG' },
    { wert: 'COMPANY', label: 'Unternehmen' },
    { wert: 'AUTHORITY', label: 'Behörde' },
    { wert: 'INSURER', label: 'Versicherung' },
    { wert: 'OTHER', label: 'Sonstige' },
  ];

  protected readonly query = signal('');
  protected readonly partyType = signal<PartyType | null>(null);

  /**
   * Mitarbeiter in der Kontaktliste zeigen? Vorgabe: **nein** (Befund F1).
   *
   * Sascha: „Ich lege meine Mitarbeiter ja nicht wie einen Kunden an!" Die
   * Kontaktliste ist das Kundenregister; Beschäftigte gehören dort nicht
   * standardmäßig hinein — sie haben eine andere Rechtsgrundlage, andere
   * Zwecke und andere Löschfristen.
   *
   * Ein Schalter statt eines harten Ausschlusses: Ein Monteur kann durchaus
   * auch privat Kunde sein, und dann soll er auffindbar bleiben.
   */
  protected readonly mitarbeiterZeigen = signal(false);

  mitarbeiterUmschalten(): void {
    this.mitarbeiterZeigen.update((v) => !v);
    this.page.set(1);
    this.fetch();
  }
  protected readonly page = signal(1);
  protected readonly state = signal<ViewState>({ kind: 'loading' });

  // --- Mehrfachauswahl (Kontakte-7) ---------------------------------------
  // Set der ausgewählten Party-IDs. Bewusste Entscheidung: Die Auswahl ist
  // SEITEN-GEBUNDEN und wird bei jedem Laden (Suche, Filter, Blättern,
  // Neuanlage) in fetch() zurückgesetzt. Grund: Die Liste ist serverseitig
  // paginiert; wir haben nur die aktuell sichtbaren Zeilen im Speicher. Eine
  // Auswahl über Seiten hinweg wäre unsichtbar, nicht mehr manuell auflösbar
  // und der CSV-Export würde Zeilen mitnehmen, die niemand mehr sieht — ein
  // A11y- und Vertrauensbruch. „Was du siehst, ist was du auswählst" hält die
  // Kopf-Checkbox „alle auf dieser Seite" kohärent und ehrlich.
  protected readonly selectedIds = signal<ReadonlySet<string>>(new Set());
  // Kurze Bestätigung nach einer Gruppenaktion (aria-live).
  protected readonly aktionMeldung = signal<string | null>(null);

  // Fuer das Laden-Skelett.
  protected readonly skeletons = Array.from({ length: 6 });

  // --- Rechte (nur UI-Sichtbarkeit; der Server setzt sie durch) -----------
  protected readonly darfAnlegen = computed(() => this.auth.darf('identity', 'ANLEGEN'));

  // --- Meldung (Erfolg/Fehler) --------------------------------------------
  protected readonly meldung = signal<Meldung | null>(null);

  // --- Anlage-Dialoge ------------------------------------------------------
  protected readonly personOffen = signal(false);
  protected readonly orgOffen = signal(false);
  protected readonly neuLaedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);

  protected readonly personForm = this.fb.group({
    salutation: this.fb.control('', { nonNullable: true }),
    title: this.fb.control('', { nonNullable: true }),
    // Vorname OHNE `required` (Befund B1, Migration 0125): Am Telefon fällt er
    // oft nicht, und ein erfundenes „X" ist schlechter als gar keiner — es
    // sieht aus wie ein Wert, landet in Anrede und Anschreiben und macht jede
    // spätere Dublettensuche unschärfer. Der Nachname bleibt Pflicht (B3).
    first_name: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.maxLength(200)],
    }),
    last_name: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(200)],
    }),
    birth_date: this.fb.control('', { nonNullable: true }),
    // --- Kontaktwege und Adresse gleich mit (Befund F1) ---------------------
    // Alle optional. Der Sinn ist nicht, mehr abzufragen, sondern den Vorgang
    // nicht zu zerreißen: Wer beim Anlegen die Nummer und die Anschrift zur
    // Hand hat, soll sie eintragen können, statt danach durch zwei Reiter der
    // Kontaktmappe zu wandern.
    phone: this.fb.control('', { nonNullable: true }),
    email: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.email],
    }),
    street: this.fb.control('', { nonNullable: true }),
    house_number: this.fb.control('', { nonNullable: true }),
    postal_code: this.fb.control('', { nonNullable: true }),
    city: this.fb.control('', { nonNullable: true }),
  });

  protected readonly orgForm = this.fb.group({
    legal_name: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(300)],
    }),
    organization_type: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    display_name: this.fb.control('', { nonNullable: true }),
    legal_form: this.fb.control('', { nonNullable: true }),
    registration_number: this.fb.control('', { nonNullable: true }),
    tax_number: this.fb.control('', { nonNullable: true }),
    vat_id: this.fb.control('', { nonNullable: true }),
    // Wie bei der Person (F1) — eine Firma hat fast immer beides.
    phone: this.fb.control('', { nonNullable: true }),
    email: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.email],
    }),
    street: this.fb.control('', { nonNullable: true }),
    house_number: this.fb.control('', { nonNullable: true }),
    postal_code: this.fb.control('', { nonNullable: true }),
    city: this.fb.control('', { nonNullable: true }),
  });

  /**
   * Die Adresse ist ganz oder gar nicht: Eine Anschrift ohne Ort ist keine.
   *
   * Umgesetzt als **bedingte Pflicht** statt als Prüfung beim Absenden. Der
   * Unterschied ist nicht kosmetisch: Eine Meldung im Banner oben stünde weit
   * entfernt von den Adressfeldern, die jetzt ganz unten in einem deutlich
   * längeren Dialog liegen — wer unten „Anlegen" drückt, sähe nichts. Feldnah
   * bekommt jedes fehlende Feld sein eigenes `aria-invalid` und seine eigene
   * Meldung (WCAG 3.3.1: der Nutzer sieht, WAS zu beheben ist), und es
   * verhält sich wie jede andere Validierung in diesem Formular.
   *
   * Solange alle vier Felder leer sind, ist keines Pflicht — wer nur den Namen
   * erfassen will, wird nicht angemeckert.
   */
  private adressPflichtVerdrahten(
    c: {
      street: FormControl<string>;
      house_number: FormControl<string>;
      postal_code: FormControl<string>;
      city: FormControl<string>;
    },
    anzeige: WritableSignal<boolean>,
  ): void {
    const alle = [c.street, c.house_number, c.postal_code, c.city];
    const pflicht = [c.street, c.postal_code, c.city];
    for (const feld of alle) {
      feld.valueChanges.pipe(takeUntilDestroyed(this.destroyRef)).subscribe(() => {
        const etwasGetippt = alle.some((f) => f.value.trim() !== '');
        // Das Signal treibt `[pflicht]` im Template: Stern und `aria-required`
        // müssen dem tatsächlichen Zustand folgen, sonst sehen drei
        // Pflichtfelder aus wie die optionalen darüber.
        anzeige.set(etwasGetippt);
        for (const p of pflicht) {
          const hat = p.hasValidator(erforderlichGetrimmt);
          if (etwasGetippt && !hat) p.addValidators(erforderlichGetrimmt);
          else if (!etwasGetippt && hat) p.removeValidators(erforderlichGetrimmt);
          else continue;
          // `emitEvent: false` — sonst löst die Neubewertung wieder
          // valueChanges aus und wir drehen uns im Kreis.
          p.updateValueAndValidity({ emitEvent: false });
        }
      });
    }
  }

  /** Ob die Adressfelder gerade Pflicht sind (steuert Stern + aria-required). */
  protected readonly personAdressPflicht = signal(false);
  protected readonly orgAdressPflicht = signal(false);

  /** Adressblock fürs Payload; `null`, wenn nichts eingetragen wurde. */
  private adressBlock(v: {
    street: string;
    house_number: string;
    postal_code: string;
    city: string;
  }): AdresseIn | null {
    const street = v.street.trim();
    const postal_code = v.postal_code.trim();
    const city = v.city.trim();
    const house_number = v.house_number.trim();
    if (!street && !postal_code && !city && !house_number) return null;
    return { street, postal_code, city, house_number: house_number || null };
  }

  /** Kontaktwege-Block; `null`, wenn weder Telefon noch E-Mail gesetzt sind. */
  private kontaktBlock(v: { phone: string; email: string }): KontaktwegeIn | null {
    const phone = v.phone.trim();
    const email = v.email.trim();
    if (!phone && !email) return null;
    return { phone: phone || null, email: email || null };
  }

  private readonly searchInput$ = new Subject<string>();
  private reqId = 0;

  protected readonly totalPages = computed(() => {
    const s = this.state();
    if (s.kind !== 'ready') return 1;
    return Math.max(1, Math.ceil(s.data.total / s.data.page_size));
  });

  protected readonly resultSummary = computed(() => {
    const s = this.state();
    if (s.kind === 'loading') return 'Kontakte werden geladen.';
    if (s.kind === 'forbidden') return 'Keine Berechtigung für die Kontakte.';
    if (s.kind === 'error') return 'Kontakte konnten nicht geladen werden.';
    const t = s.data.total;
    if (t === 0) return 'Keine Kontakte gefunden.';
    return `${t} ${t === 1 ? 'Kontakt' : 'Kontakte'} gefunden, Seite ${s.data.page} von ${this.totalPages()}.`;
  });

  // Aktuell sichtbare Zeilen (leer, solange nicht „ready").
  protected readonly pageItems = computed<Party[]>(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data.items : [];
  });

  protected readonly selectedCount = computed(() => this.selectedIds().size);

  // Kopf-Checkbox „alle auf dieser Seite": angehakt, wenn jede sichtbare Zeile
  // ausgewählt ist. Bei leerer Seite bleibt sie leer.
  protected readonly allOnPageSelected = computed(() => {
    const items = this.pageItems();
    if (items.length === 0) return false;
    const sel = this.selectedIds();
    return items.every((p) => sel.has(p.id));
  });

  // Teilauswahl → indeterminate-Zustand der Kopf-Checkbox.
  protected readonly someOnPageSelected = computed(() => {
    const items = this.pageItems();
    if (items.length === 0) return false;
    const sel = this.selectedIds();
    return items.some((p) => sel.has(p.id)) && !this.allOnPageSelected();
  });

  private readonly destroyRef = inject(DestroyRef);

  constructor() {
    // Adressfelder: ganz oder gar nicht (siehe `adressPflichtVerdrahten`).
    this.adressPflichtVerdrahten(this.personForm.controls, this.personAdressPflicht);
    this.adressPflichtVerdrahten(this.orgForm.controls, this.orgAdressPflicht);

    this.searchInput$
      .pipe(debounceTime(300), distinctUntilChanged(), takeUntilDestroyed())
      .subscribe((v) => {
        this.query.set(v);
        this.page.set(1);
        this.fetch();
      });
    this.fetch();
  }

  onSearch(value: string): void {
    this.searchInput$.next(value);
  }

  selectSegment(value: PartyType | null): void {
    if (this.partyType() === value) return;
    this.partyType.set(value);
    this.page.set(1);
    this.fetch();
  }

  prev(): void {
    if (this.page() <= 1) return;
    this.page.update((p) => p - 1);
    this.fetch();
  }

  next(): void {
    if (this.page() >= this.totalPages()) return;
    this.page.update((p) => p + 1);
    this.fetch();
  }

  retry(): void {
    this.fetch();
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  /**
   * Kontakte-9: In die frisch angelegte Organisation navigieren und dort direkt
   * den Ansprechpartner-Dialog öffnen (Query-Param wird in kontakt-detail
   * ausgewertet). Der Anlageweg selbst lebt im kontakt-detail — kein neuer
   * Endpunkt, kein zweiter Dialog hier.
   */
  zumAnsprechpartner(orgId: string): void {
    this.meldung.set(null);
    this.router.navigate(['/kontakte', orgId], {
      queryParams: { neu: 'ansprechpartner' },
    });
  }

  // ---- Mehrfachauswahl: Aktionen -----------------------------------------
  isSelected(id: string): boolean {
    return this.selectedIds().has(id);
  }

  toggle(id: string): void {
    this.selectedIds.update((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    this.aktionMeldung.set(null);
  }

  toggleAllOnPage(): void {
    const items = this.pageItems();
    if (items.length === 0) return;
    const alleRaus = this.allOnPageSelected();
    this.selectedIds.update((prev) => {
      const next = new Set(prev);
      for (const p of items) {
        if (alleRaus) next.delete(p.id);
        else next.add(p.id);
      }
      return next;
    });
    this.aktionMeldung.set(null);
  }

  auswahlAufheben(): void {
    this.selectedIds.set(new Set());
    this.aktionMeldung.set(null);
  }

  /**
   * Kontakte-7: Die aktuell ausgewählten (sichtbaren) Kontakte clientseitig als
   * CSV herunterladen. Kein Server-Call. Spalten sind genau die Felder, die die
   * Listenzeile trägt — Kennung, Anzeigename, Typ, Status. Kein erfundenes Feld
   * (Kontaktweg/Kundennummer liegen erst im Detail, nicht in der Liste).
   * Trennzeichen „;" und UTF-8-BOM, damit deutsches Excel sauber öffnet.
   */
  alsCsvExportieren(): void {
    const gewaehlt = this.pageItems().filter((p) => this.selectedIds().has(p.id));
    if (gewaehlt.length === 0) return;

    const kopf = ['Kennung', 'Anzeigename', 'Typ', 'Status'];
    const zeilen = gewaehlt.map((p) => [
      this.shortId(p),
      p.display_name,
      this.typeLabel(p.party_type),
      this.statusLabel(p.status),
    ]);
    const csv = [kopf, ...zeilen]
      .map((cols) => cols.map((c) => this.csvZelle(c)).join(';'))
      .join('\r\n');

    const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `kontakte-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);

    this.aktionMeldung.set(
      `${gewaehlt.length} ${gewaehlt.length === 1 ? 'Kontakt' : 'Kontakte'} als CSV-Datei exportiert.`,
    );
  }

  // CSV-Feld nach RFC 4180: bei ; " oder Zeilenumbruch in Anführungszeichen
  // setzen und enthaltene " verdoppeln.
  private csvZelle(wert: string): string {
    // CSV-Formel-Injection entschärfen: Excel/LibreOffice werten eine Zelle, die
    // mit = + - @ (auch Tab/CR) beginnt, als Formel aus — auch innerhalb von
    // Anführungszeichen. Nutzerkontrollierte Werte (z. B. Kontaktname) könnten so
    // zur lebenden Formel/DDE werden; führendes Apostroph neutralisiert das.
    let z = wert;
    if (/^[=+\-@\t\r]/.test(z)) {
      z = "'" + z;
    }
    if (/[";\r\n]/.test(z)) {
      return '"' + z.replace(/"/g, '""') + '"';
    }
    return z;
  }

  private fetch(): void {
    const id = ++this.reqId;
    // Seiten-gebundene Auswahl: bei jedem Laden zurücksetzen (siehe selectedIds).
    this.selectedIds.set(new Set());
    this.aktionMeldung.set(null);
    this.state.set({ kind: 'loading' });
    this.svc
      .list({
        page: this.page(),
        page_size: this.pageSize,
        q: this.query(),
        party_type: this.partyType(),
        mitarbeiter_zeigen: this.mitarbeiterZeigen(),
      })
      .subscribe({
        next: (data) => {
          if (id === this.reqId) this.state.set({ kind: 'ready', data });
        },
        error: (err) => {
          if (id === this.reqId) this.state.set(fehlerState(err));
        },
      });
  }

  // ---- Anlegen: Person ----------------------------------------------------
  personOeffnen(): void {
    this.personForm.reset({
      salutation: '',
      title: '',
      first_name: '',
      last_name: '',
      birth_date: '',
      phone: '',
      email: '',
      street: '',
      house_number: '',
      postal_code: '',
      city: '',
    });
    this.formularMeldung.set(null);
    this.personOffen.set(true);
  }

  personSchliessen(): void {
    if (this.neuLaedt()) return;
    this.personOffen.set(false);
  }

  personAbsenden(): void {
    if (this.neuLaedt()) return;
    serverFehlerZuruecksetzen(this.personForm);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.personForm);
    if (this.personForm.invalid) return;

    const v = this.personForm.getRawValue();
    const payload: PersonIn = {
      // Leerer Vorname wird zu null — die DB verbietet den Leerstring
      // (person_first_name_nicht_leer), „nicht erhoben" ist NULL.
      first_name: v.first_name.trim() || null,
      last_name: v.last_name.trim(),
      salutation: v.salutation.trim() || null,
      title: v.title.trim() || null,
      birth_date: v.birth_date || null,
      kontakt: this.kontaktBlock(v),
      adresse: this.adressBlock(v),
    };

    this.neuLaedt.set(true);
    this.svc.createPerson(payload).subscribe({
      next: (party) => {
        this.neuLaedt.set(false);
        this.personOffen.set(false);
        // Befund F2: Bisher wurde nur die Liste neu geladen — und `fetch()`
        // setzt sogar die Auswahl zurück. Wer eine Person anlegte, musste sie
        // anschließend in der Liste WIEDERFINDEN, um Telefonnummer und Adresse
        // nachzutragen. Jetzt geht es direkt in die Mappe, wo beides liegt.
        // Dasselbe Muster nutzt die Organisation längst (`zumAnsprechpartner`).
        this.router.navigate(['/kontakte', party.id]);
      },
      error: (err) => {
        this.neuLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.personForm).formular);
      },
    });
  }

  // ---- Anlegen: Organisation ---------------------------------------------
  orgOeffnen(): void {
    this.orgForm.reset({
      legal_name: '',
      organization_type: '',
      display_name: '',
      legal_form: '',
      registration_number: '',
      tax_number: '',
      vat_id: '',
      phone: '',
      email: '',
      street: '',
      house_number: '',
      postal_code: '',
      city: '',
    });
    this.formularMeldung.set(null);
    this.orgOffen.set(true);
  }

  orgSchliessen(): void {
    if (this.neuLaedt()) return;
    this.orgOffen.set(false);
  }

  orgAbsenden(): void {
    if (this.neuLaedt()) return;
    serverFehlerZuruecksetzen(this.orgForm);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.orgForm);
    if (this.orgForm.invalid) return;

    const v = this.orgForm.getRawValue();
    const payload: OrganizationIn = {
      legal_name: v.legal_name.trim(),
      organization_type: v.organization_type as OrganizationTypeCode,
      display_name: v.display_name.trim() || null,
      legal_form: v.legal_form.trim() || null,
      registration_number: v.registration_number.trim() || null,
      tax_number: v.tax_number.trim() || null,
      vat_id: v.vat_id.trim() || null,
      kontakt: this.kontaktBlock(v),
      // Der Server setzt hier BUSINESS als Vorgabe (Geschäftsanschrift).
      adresse: this.adressBlock(v),
    };

    this.neuLaedt.set(true);
    this.svc.createOrganization(payload).subscribe({
      next: (party) => {
        this.neuLaedt.set(false);
        this.orgOffen.set(false);
        // Bewusst ANDERS als bei der Person (F2): Dort geht es direkt in die
        // Mappe, weil dort Telefon und Adresse nachzutragen sind. Bei einer
        // Organisation ist der nächste Schritt fast immer ein Ansprechpartner
        // — deshalb bleibt hier die Meldung mit der Überleitung stehen, statt
        // ungefragt zu navigieren. Zwei Wege, zwei Absichten.
        this.meldung.set({
          art: 'erfolg',
          text: `Organisation „${party.display_name}“ wurde angelegt.`,
          // Kontakte-9: Überleitung in den Ansprechpartner-Fluss anbieten.
          folgeOrgId: party.id,
        });
        this.page.set(1);
        this.fetch();
      },
      error: (err) => {
        this.neuLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.orgForm).formular);
      },
    });
  }

  // ---- Darstellungshelfer -------------------------------------------------
  monogram(p: Party): string {
    const parts = p.display_name.trim().split(/\s+/).filter(Boolean);
    if (parts.length === 0) return '–';
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }

  shortId(p: Party): string {
    return p.id.replace(/-/g, '').slice(0, 8).toUpperCase();
  }

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
}
