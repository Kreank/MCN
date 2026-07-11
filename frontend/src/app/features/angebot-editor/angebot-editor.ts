import { Component, HostListener, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { NgTemplateOutlet } from '@angular/common';
import { CdkDragDrop, DragDropModule, moveItemInArray } from '@angular/cdk/drag-drop';
import {
  FormBuilder,
  FormControl,
  ReactiveFormsModule,
  Validators,
} from '@angular/forms';
import { Observable, Subject, debounceTime, distinctUntilChanged, map } from 'rxjs';
import { BelegService } from '../../core/beleg.service';
import { ArtikelService } from '../../core/artikel.service';
import { StammdatenUebernahmeIn } from '../../core/artikel.model';
import { AuthService } from '../../core/auth.service';
import {
  InvoiceDetail,
  InvoiceUpdate,
  Kalkulation,
  LineKind,
  LineType,
  QuoteDetail,
  QuoteLine,
  QuoteLineInput,
  QuoteStatus,
  QuoteUpdate,
  RubrikInput,
} from '../../core/beleg.model';

/** Der Editor bedient Angebote UND Rechnungen — beide teilen die Positions-/
 * Abschnitts-/Kalkulationslogik; nur Laden/Speichern/Veröffentlichen und die
 * Kopffelder unterscheiden sich. */
type BelegDetail = QuoteDetail | InvoiceDetail;
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerDetail, fehlerState, istVerboten } from '../../shared/http-fehler';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';
import { apiZuDeDezimal, deZuApiDezimal, dezimalValidator } from '../../shared/formular/dezimal';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready' }
  | VerbotenState
  | { kind: 'error' };

type Meldung = { art: 'erfolg' | 'fehler'; text: string };

/** Palette-Modus: Artikelstamm oder Leistungen (Stücklisten). */
type PaletteModus = 'artikel' | 'leistungen';

let uidSeq = 0;
const neueUid = (): string => `u${++uidSeq}`;

/** Ein Abschnitt (Rubrik) im Editor. `uid` ist eine stabile lokale Kennung. */
interface EditorRubrik {
  uid: string;
  title: string;
  description: string | null;
}

/**
 * Eine Position im Editor-Zustand. Alle Dezimalfelder sind API-Punkt-Strings
 * (verlustfrei) oder null. `netAmount`/`taxRatePercent` stammen aus dem letzten
 * Server-Load und dienen nur der Anzeige — der Editor rechnet keine Summen.
 */
interface EditorLine {
  uid: string;
  rubrikUid: string | null;
  line_type: LineType;
  line_kind: LineKind;
  description: string;
  quantity: string | null;
  unit: string | null;
  unit_price: string | null;
  discount_percent: string | null;
  tax_code: string | null;
  unit_cost: string | null;
  markup_percent: string | null;
  sale_price_group_id: string | null;
  source_article_id: string | null;
  source_assembly_id: string | null;
  netAmount: string | null;
  taxRatePercent: string | null;
}

/** Anzeige-Gruppe: ein Abschnitt (oder die Sammelgruppe „Ohne Abschnitt"). */
interface Gruppe {
  rubrik: EditorRubrik | null;
  lines: EditorLine[];
}

/** Gruppe eines echten Abschnitts (rubrik nie null) — für das Sektionen-Template. */
interface RubrikGruppe {
  rubrik: EditorRubrik;
  lines: EditorLine[];
}

/** Zielangabe für Drop/Palette: in welchen Abschnitt an welche Stelle. */
interface DropZiel {
  rubrikUid: string | null;
  index: number;
}

/** Feste DropList-Kennung der Sammelgruppe „Ohne Abschnitt". */
const DL_OHNE = 'dl-ohne';
/** Feste DropList-Kennung der Palette (Quelle, nimmt selbst nichts entgegen). */
const DL_PALETTE = 'dl-palette';
/** Sentinel: „ans Ende der Zielgruppe" (Knopf-/Select-Pfad ohne feste Stelle). */
const ANS_ENDE = Number.MAX_SAFE_INTEGER;

const LINE_TYPE_OPTIONEN: FeldOption[] = [
  { wert: 'MATERIAL', label: 'Material' },
  { wert: 'ARBEITSZEIT', label: 'Arbeitszeit' },
  { wert: 'PAUSCHALE', label: 'Pauschale' },
  { wert: 'FREMDLEISTUNG', label: 'Fremdleistung' },
  { wert: 'FAHRT', label: 'Fahrt' },
  { wert: 'ZUSCHLAG', label: 'Zuschlag' },
  { wert: 'TEXT', label: 'Textzeile' },
  { wert: 'ZWISCHENSUMME', label: 'Zwischensumme' },
];
const LINE_KIND_OPTIONEN: FeldOption[] = [
  { wert: 'NORMAL', label: 'Normalposition (zählt in die Summe)' },
  { wert: 'ALTERNATIV', label: 'Alternativposition (zählt nicht)' },
  { wert: 'BEDARF', label: 'Bedarfsposition (zählt nicht)' },
];
const TAX_CODE_OPTIONEN: FeldOption[] = [
  { wert: 'DE_19', label: 'USt 19 %' },
  { wert: 'DE_7', label: 'USt 7 %' },
  { wert: 'DE_0', label: 'Steuerfrei (0 %)' },
  { wert: 'DE_13B', label: '§13b UStG (Reverse Charge)' },
];
const TEXT_TYPES: LineType[] = ['TEXT', 'ZWISCHENSUMME'];
/** Status, in denen der Beleg noch bearbeitbar ist (sonst read-only). */
const EDITIERBAR: QuoteStatus[] = ['ENTWURF', 'INTERN_GEPRUEFT', 'FREIGEGEBEN'];

/**
 * Angebotseditor — der zentrale Screen. Gliederung in Abschnitte (Rubriken),
 * Positionen per Palette (Artikel/Leistungen) übernehmen, per Tastatur/Knopf
 * umsortieren und zwischen Abschnitten verschieben (kein Drag-Zwang: WCAG 2.5.7),
 * Positionsdetail bearbeiten, unten eine feste Kalkulationsleiste vom Server.
 *
 * Der Editor rechnet KEINE Summen — Netto/Steuer/Brutto und die Kalkulation
 * liefert der Server (nach jedem Speichern neu geladen). Beträge sind Strings.
 */
@Component({
  selector: 'app-angebot-editor',
  imports: [
    RouterLink,
    ReactiveFormsModule,
    DragDropModule,
    NgTemplateOutlet,
    KeinZugriff,
    Dialog,
    Feld,
    Bestaetigung,
  ],
  templateUrl: './angebot-editor.html',
  styleUrl: './angebot-editor.scss',
})
export class AngebotEditor {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(BelegService);
  private readonly artikelSvc = inject(ArtikelService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  protected readonly lineTypeOptionen = LINE_TYPE_OPTIONEN;
  protected readonly lineKindOptionen = LINE_KIND_OPTIONEN;
  protected readonly taxCodeOptionen = TAX_CODE_OPTIONEN;

  // --- Geladener Beleg & Zustand ------------------------------------------
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly quote = signal<BelegDetail | null>(null);
  private reqId = 0;
  private quoteId = '';
  /** Belegart aus der Route (data.belegArt); steuert Laden/Speichern/Kopf. */
  protected readonly istRechnung =
    this.route.snapshot.data['belegArt'] === 'rechnung';
  protected readonly belegWort = this.istRechnung ? 'Rechnung' : 'Angebot';

  // --- Editier-Zustand -----------------------------------------------------
  protected readonly rubriken = signal<EditorRubrik[]>([]);
  protected readonly lines = signal<EditorLine[]>([]);
  protected readonly dirty = signal(false);
  protected readonly meldung = signal<Meldung | null>(null);
  /** Screenreader-Ansage für Verschiebe-/Struktur-Aktionen. */
  protected readonly ansage = signal('');

  /** Kopf-Formular. Angebot: Titel + Angebots-/Gültig-bis-Datum. Rechnung: kein
   * Titel (Identität über Typ+Nummer), stattdessen Rechnungs-/Fälligkeitsdatum.
   * Titel ist nur beim Angebot Pflicht (Validator in `titelPflichtSetzen`). */
  protected readonly kopfForm = this.fb.group({
    title: this.fb.control('', { nonNullable: true }),
    quote_date: this.fb.control('', { nonNullable: true }),
    valid_until_date: this.fb.control('', { nonNullable: true }),
    invoice_date: this.fb.control('', { nonNullable: true }),
    due_date: this.fb.control('', { nonNullable: true }),
  });

  // --- Rechte / read-only --------------------------------------------------
  // Angebot versenden verlangt VERSENDEN; Rechnung veröffentlichen verlangt
  // FREIGEBEN (so erzwingt es der Server) — der Button spiegelt das jeweils richtige Recht.
  protected readonly darfVersenden = computed(() =>
    this.istRechnung
      ? this.auth.darf('invoicing', 'FREIGEBEN')
      : this.auth.darf('invoicing', 'VERSENDEN'),
  );
  protected readonly readonly = computed(() => {
    const q = this.quote();
    // EDITIERBAR enthält ENTWURF (editierbar für beide Belegarten) und NICHT
    // VERSENDET/VEROEFFENTLICHT — passt für Angebot und Rechnung gleichermaßen.
    return !q || !(EDITIERBAR as readonly string[]).includes(q.status);
  });

  // --- Kalkulation (Server) ------------------------------------------------
  protected readonly kalk = signal<Kalkulation | null>(null);
  protected readonly kalkVerborgen = signal(false); // 403 auf pricing/LESEN

  // --- Palette -------------------------------------------------------------
  protected readonly paletteModus = signal<PaletteModus>('artikel');
  protected readonly paletteTreffer = signal<{ id: string; zeile1: string; zeile2: string }[]>([]);
  protected readonly paletteLaedt = signal(false);
  protected readonly paletteFehler = signal(false);
  /** Zielabschnitt für „Übernehmen" (uid oder null = Ohne Abschnitt). */
  protected readonly zielRubrik = signal<string | null>(null);
  private readonly paletteSuche$ = new Subject<string>();
  private paletteReq = 0;

  // --- Positionsdetail-Dialog ---------------------------------------------
  protected readonly posOffen = signal(false);
  private posUid: string | null = null;
  /** Quell-Artikel der gerade bearbeiteten Position (source_article_id) — nur
   *  dann lässt sich der Stamm übernehmen. Kopie-Positionen ohne Quelle nicht. */
  protected readonly posQuelleArtikelId = signal<string | null>(null);

  // --- Stammdaten in den Artikelstamm übernehmen (eigener Vorgang) ---------
  protected readonly darfPricingAendern = computed(() => this.auth.darf('pricing', 'AENDERN'));
  protected readonly stammOffen = signal(false);
  protected readonly stammLaedt = signal(false);
  /** Beim Positions-Speichern erfasste Übernahme-Daten — die Bestätigung und
   *  der Server-Call greifen darauf zu, nachdem die Position bereits (lokal)
   *  gespeichert ist. */
  private stammPending: { articleId: string; payload: StammdatenUebernahmeIn } | null = null;
  protected readonly posForm = this.fb.group({
    description: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    line_type: this.fb.control<LineType>('MATERIAL', { nonNullable: true }),
    line_kind: this.fb.control<LineKind>('NORMAL', { nonNullable: true }),
    quantity: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
    unit: this.fb.control('', { nonNullable: true }),
    unit_price: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
    unit_cost: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
    discount_percent: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
    tax_code: this.fb.control('DE_19', { nonNullable: true }),
    // Transientes Häkchen: gehört NUR zum Dialog, wird bei jedem Öffnen auf
    // false gesetzt und nie in den EditorLine-/Beleg-Zustand übernommen. Es löst
    // eine einmalige, ausdrückliche Stamm-Übernahme aus — nicht bei jedem Speichern.
    stamm_uebernehmen: this.fb.control(false, { nonNullable: true }),
  });
  protected readonly posFormMeldung = signal<string | null>(null);

  // --- Abschnitt-Dialog ----------------------------------------------------
  protected readonly rubrikOffen = signal(false);
  private rubrikUid: string | null = null; // null = neuer Abschnitt
  protected readonly rubrikForm = this.fb.group({
    title: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    description: this.fb.control('', { nonNullable: true }),
  });

  // --- Speichern / Versenden ----------------------------------------------
  protected readonly saving = signal(false);
  protected readonly versendenOffen = signal(false);
  protected readonly versendenLaedt = signal(false);

  private readonly euroFmt = new Intl.NumberFormat('de-DE', {
    style: 'currency',
    currency: 'EUR',
  });

  /** Gruppen der echten Abschnitte, in Abschnittsreihenfolge. */
  protected readonly rubrikGruppen = computed<RubrikGruppe[]>(() => {
    const lines = this.lines();
    return this.rubriken().map((r) => ({
      rubrik: r,
      lines: lines.filter((l) => l.rubrikUid === r.uid),
    }));
  });

  /** Sammelgruppe „Ohne Abschnitt". */
  protected readonly ohneGruppe = computed<Gruppe>(() => ({
    rubrik: null,
    lines: this.lines().filter((l) => l.rubrikUid === null),
  }));

  /** Anzeige-Gruppen: Abschnitte in Reihenfolge, „Ohne Abschnitt" zuletzt. */
  protected readonly gruppen = computed<Gruppe[]>(() => [
    ...this.rubrikGruppen(),
    this.ohneGruppe(),
  ]);

  /** DropList-Kennung einer Positionsliste (je Abschnitt bzw. „Ohne Abschnitt"). */
  protected posListId(rubrikUid: string | null): string {
    return rubrikUid === null ? DL_OHNE : `dl-${rubrikUid}`;
  }

  /**
   * Alle Positions-DropLists, mit denen jede Liste (und die Palette) verbunden
   * ist. Reihenfolge: Abschnitte, dann „Ohne Abschnitt".
   */
  protected readonly posListIds = computed<string[]>(() => [
    ...this.rubriken().map((r) => this.posListId(r.uid)),
    DL_OHNE,
  ]);

  /** DropList-Kennung der Palette (für das Template). */
  protected readonly paletteListId = DL_PALETTE;

  /** Palette nimmt selbst keine Positionen entgegen (Quelle, nicht Ziel). */
  protected readonly niePredicate = (): boolean => false;

  /** Zielabschnitt-Auswahl für die Palette. */
  protected readonly zielOptionen = computed<FeldOption[]>(() => [
    { wert: '', label: 'Ohne Abschnitt' },
    ...this.rubriken().map((r, i) => ({ wert: r.uid, label: `${i + 1}. ${r.title}` })),
  ]);

  /**
   * Live-Vorschau des Aufschlags im Positionsdialog (Server bestätigt beim
   * Speichern). Methode statt `computed`: liest FormControl-Werte (keine
   * Signale) und muss je Change-Detection neu ausgewertet werden.
   */
  markupVorschau(): string | null {
    const ekRoh = this.posForm.controls.unit_cost.value;
    const vkRoh = this.posForm.controls.unit_price.value;
    const ek = Number(deZuApiDezimal(ekRoh));
    const vk = Number(deZuApiDezimal(vkRoh));
    if (!Number.isFinite(ek) || !Number.isFinite(vk) || ek <= 0 || !vkRoh || !ekRoh) return null;
    const pct = ((vk - ek) / ek) * 100;
    return new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1 }).format(pct);
  }

  /** Ob die aktuell im Dialog gewählte Positionsart eine Textzeile ist. */
  posIstText(): boolean {
    return TEXT_TYPES.includes(this.posForm.controls.line_type.value);
  }

  constructor() {
    // Titel ist nur beim Angebot ein Pflichtfeld (Rechnung hat keinen Titel).
    if (!this.istRechnung) {
      this.kopfForm.controls.title.addValidators(Validators.required);
    }

    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      if (!id) {
        this.state.set({ kind: 'error' });
        return;
      }
      this.quoteId = id;
      this.load(id);
    });

    this.paletteSuche$
      .pipe(debounceTime(300), distinctUntilChanged(), takeUntilDestroyed())
      .subscribe((q) => this.paletteFetch(q));

    // Text-/Nicht-Text-Umschaltung im Positionsdialog: Pflicht-Validatoren
    // (Menge/Preis) nur für Nicht-Textzeilen. FormControl-Werte sind keine
    // Signale, daher über valueChanges statt über einen effect().
    this.posForm.controls.line_type.valueChanges
      .pipe(takeUntilDestroyed())
      .subscribe((lt) => {
        const text = TEXT_TYPES.includes(lt);
        const q = this.posForm.controls.quantity;
        const p = this.posForm.controls.unit_price;
        const req = text ? [dezimalValidator] : [Validators.required, dezimalValidator];
        q.setValidators(req);
        p.setValidators(req);
        q.updateValueAndValidity({ emitEvent: false });
        p.updateValueAndValidity({ emitEvent: false });
      });

    // Kopffeld-Änderungen (Titel/Daten) als ungespeichert markieren. `uebernehmen`
    // setzt dirty nach dem reset() wieder zurück.
    this.kopfForm.valueChanges
      .pipe(takeUntilDestroyed())
      .subscribe(() => this.markiereGeaendert());
  }

  @HostListener('window:beforeunload', ['$event'])
  onBeforeUnload(e: BeforeUnloadEvent): void {
    if (this.dirty()) {
      e.preventDefault();
      e.returnValue = '';
    }
  }

  retry(): void {
    if (this.quoteId) this.load(this.quoteId);
  }

  // ======================= Laden ==========================================
  private load(id: string): void {
    const rid = ++this.reqId;
    this.state.set({ kind: 'loading' });
    const laden$: Observable<BelegDetail> = this.istRechnung
      ? this.svc.getInvoice(id)
      : this.svc.get(id);
    laden$.subscribe({
      next: (data) => {
        if (rid !== this.reqId) return;
        this.uebernehmen(data);
        this.state.set({ kind: 'ready' });
        this.kalkulationLaden();
        // Palette einmal vorbefüllen (erste Treffer), sofern bearbeitbar.
        if (!this.readonly()) this.paletteFetch('');
      },
      error: (err) => {
        if (rid === this.reqId) this.state.set(fehlerState(err));
      },
    });
  }

  /** Server-Antwort in den Editier-Zustand übertragen (dirty zurücksetzen). */
  private uebernehmen(data: BelegDetail): void {
    this.quote.set(data);
    if (this.istRechnung) {
      const inv = data as InvoiceDetail;
      this.kopfForm.reset({
        title: '', quote_date: '', valid_until_date: '',
        invoice_date: inv.invoice_date ?? '',
        due_date: inv.due_date ?? '',
      });
    } else {
      const q = data as QuoteDetail;
      this.kopfForm.reset({
        title: q.title ?? '',
        quote_date: q.quote_date ?? '',
        valid_until_date: q.valid_until_date ?? '',
        invoice_date: '', due_date: '',
      });
    }
    // Abschnitte in Anzeigereihenfolge; Nummer (1-basiert) → lokale uid.
    const rubriken = [...data.rubriken]
      .sort((a, b) => a.position_number - b.position_number)
      .map<EditorRubrik>((r) => ({
        uid: neueUid(),
        title: r.title,
        description: r.description,
      }));
    // position_number der Rubrik → uid (für die Zuordnung der Zeilen).
    const nummerZuUid = new Map<number, string>();
    [...data.rubriken]
      .sort((a, b) => a.position_number - b.position_number)
      .forEach((r, i) => nummerZuUid.set(r.position_number, rubriken[i].uid));

    const lines = [...data.lines]
      .sort((a, b) => a.position_number - b.position_number)
      .map<EditorLine>((l) => this.zuEditorLine(l, nummerZuUid));

    this.rubriken.set(rubriken);
    this.lines.set(lines);
    this.zielRubrik.set(null);
    // Kopffelder sperren, wenn der Beleg eingefroren ist (read-only).
    if ((EDITIERBAR as readonly string[]).includes(data.status)) {
      this.kopfForm.enable({ emitEvent: false });
    } else {
      this.kopfForm.disable({ emitEvent: false });
    }
    this.dirty.set(false);
  }

  private zuEditorLine(l: QuoteLine, nummerZuUid: Map<number, string>): EditorLine {
    return {
      uid: neueUid(),
      rubrikUid: l.rubrik != null ? nummerZuUid.get(l.rubrik) ?? null : null,
      line_type: l.line_type,
      line_kind: l.line_kind,
      description: l.description,
      quantity: l.quantity,
      unit: l.unit,
      unit_price: l.unit_price,
      discount_percent: l.discount_percent,
      tax_code: l.tax_code,
      unit_cost: l.unit_cost,
      markup_percent: l.markup_percent,
      sale_price_group_id: null,
      source_article_id: l.source_article_id,
      source_assembly_id: l.source_assembly_id,
      netAmount: l.net_amount,
      taxRatePercent: l.tax_rate_percent,
    };
  }

  private kalkulationLaden(): void {
    const kalk$ = this.istRechnung
      ? this.svc.invoiceKalkulation(this.quoteId)
      : this.svc.kalkulation(this.quoteId);
    kalk$.subscribe({
      next: (k) => {
        this.kalk.set(k);
        this.kalkVerborgen.set(false);
      },
      error: (err) => {
        this.kalk.set(null);
        // 403 = fehlendes pricing/LESEN → Leiste bewusst ausblenden (kein Fehler).
        this.kalkVerborgen.set(istVerboten(err));
      },
    });
  }

  private markiereGeaendert(): void {
    if (!this.dirty()) this.dirty.set(true);
  }

  // ======================= Abschnitte =====================================
  rubrikNeu(): void {
    this.rubrikUid = null;
    this.rubrikForm.reset({ title: '', description: '' });
    this.rubrikOffen.set(true);
  }

  rubrikBearbeiten(r: EditorRubrik): void {
    this.rubrikUid = r.uid;
    this.rubrikForm.reset({ title: r.title, description: r.description ?? '' });
    this.rubrikOffen.set(true);
  }

  rubrikDialogSchliessen(): void {
    this.rubrikOffen.set(false);
  }

  rubrikSpeichern(): void {
    felderAlsBeruehrtMarkieren(this.rubrikForm);
    if (this.rubrikForm.invalid) return;
    const title = this.rubrikForm.controls.title.value.trim();
    const description = this.rubrikForm.controls.description.value.trim() || null;
    if (this.rubrikUid === null) {
      const uid = neueUid();
      this.rubriken.update((rs) => [...rs, { uid, title, description }]);
      this.ansage.set(`Abschnitt „${title}" hinzugefügt.`);
    } else {
      const uid = this.rubrikUid;
      this.rubriken.update((rs) =>
        rs.map((r) => (r.uid === uid ? { ...r, title, description } : r)),
      );
      this.ansage.set(`Abschnitt „${title}" geändert.`);
    }
    this.markiereGeaendert();
    this.rubrikOffen.set(false);
  }

  rubrikEntfernen(r: EditorRubrik): void {
    // Positionen des Abschnitts wandern nach „Ohne Abschnitt" (kein Datenverlust).
    this.lines.update((ls) =>
      ls.map((l) => (l.rubrikUid === r.uid ? { ...l, rubrikUid: null } : l)),
    );
    this.rubriken.update((rs) => rs.filter((x) => x.uid !== r.uid));
    // Zielabschnitt der Palette nicht auf einen gelöschten Abschnitt zeigen lassen
    // (sonst landeten neu übernommene Positionen in keiner Gruppe).
    if (this.zielRubrik() === r.uid) this.zielRubrik.set(null);
    this.ansage.set(`Abschnitt „${r.title}" entfernt, Positionen nach „Ohne Abschnitt" verschoben.`);
    this.markiereGeaendert();
  }

  rubrikVerschieben(r: EditorRubrik, richtung: -1 | 1): void {
    const i = this.rubriken().findIndex((x) => x.uid === r.uid);
    const j = i + richtung;
    if (i < 0 || j < 0 || j >= this.rubriken().length) return;
    this.rubrikBewegen(i, j);
    this.ansage.set(`Abschnitt „${r.title}" nach ${richtung < 0 ? 'oben' : 'unten'} verschoben.`);
  }

  /**
   * Kernfunktion Abschnitt-Umsortierung: verschiebt die Rubrik von `von` nach
   * `nach` (Positionen wandern automatisch mit, weil die Gruppen aus der
   * Rubrik-Reihenfolge abgeleitet werden). Wird von Knopf UND Drag genutzt.
   */
  private rubrikBewegen(von: number, nach: number): void {
    const rs = [...this.rubriken()];
    if (von < 0 || nach < 0 || von >= rs.length || nach >= rs.length || von === nach) return;
    moveItemInArray(rs, von, nach);
    this.rubriken.set(rs);
    this.markiereGeaendert();
  }

  /** Drop-Handler: Abschnitt per Ziehen umsortieren (gleicher Pfad wie ▲/▼). */
  sektionDrop(event: CdkDragDrop<EditorRubrik[]>): void {
    if (this.readonly() || event.previousIndex === event.currentIndex) return;
    this.rubrikBewegen(event.previousIndex, event.currentIndex);
    const moved = this.rubriken()[event.currentIndex];
    this.ansage.set(`Abschnitt „${moved?.title ?? ''}" an Stelle ${event.currentIndex + 1} verschoben.`);
  }

  // ======================= Positionen: verschieben ========================
  /** Position innerhalb ihres Abschnitts nach oben/unten. */
  zeileVerschieben(line: EditorLine, richtung: -1 | 1): void {
    const gruppe = this.lines().filter((l) => l.rubrikUid === line.rubrikUid);
    const pos = gruppe.findIndex((l) => l.uid === line.uid);
    const ziel = pos + richtung;
    if (pos < 0 || ziel < 0 || ziel >= gruppe.length) return;
    this.lineEinordnen(line.uid, line.rubrikUid, ziel);
    this.ansage.set(`Position nach ${richtung < 0 ? 'oben' : 'unten'} verschoben.`);
  }

  /** Position in einen anderen Abschnitt verschieben (per Select, ans Ende). */
  zeileAbschnittWechseln(line: EditorLine, wert: string): void {
    const ziel = wert || null;
    if (ziel === line.rubrikUid) return;
    this.lineEinordnen(line.uid, ziel, ANS_ENDE);
    const name = ziel === null ? 'Ohne Abschnitt' : this.rubrikName(ziel);
    this.ansage.set(`Position nach „${name}" verschoben.`);
  }

  /** Drop-Handler: Position innerhalb/zwischen Abschnitten bzw. aus der Palette. */
  positionDrop(event: CdkDragDrop<Gruppe>): void {
    if (this.readonly()) return;
    const zielRubrikUid = event.container.data.rubrik?.uid ?? null;
    const zielIndex = event.currentIndex;
    const name = zielRubrikUid === null ? 'Ohne Abschnitt' : this.rubrikName(zielRubrikUid);

    // Aus der Palette gezogen: Artikel/Leistung an die Zielstelle übernehmen.
    if (event.previousContainer.id === DL_PALETTE) {
      this.uebernehmenAusPalette(event.item.data as string, { rubrikUid: zielRubrikUid, index: zielIndex });
      return;
    }

    // Umsortierung: gleiche Liste ohne Positionswechsel = nichts zu tun.
    if (event.previousContainer === event.container && event.previousIndex === event.currentIndex) return;
    const line = event.item.data as EditorLine;
    this.lineEinordnen(line.uid, zielRubrikUid, zielIndex);
    this.ansage.set(`Position nach „${name}", Stelle ${zielIndex + 1} verschoben.`);
  }

  /**
   * Kernfunktion Positions-Umsortierung: nimmt die Position `uid` aus dem
   * flachen Zeilen-Array und setzt sie so wieder ein, dass sie in der Zielgruppe
   * `zielRubrikUid` an Position `zielIndex` steht. `zielIndex >= Gruppengröße`
   * hängt ans Ende. Knöpfe, Select UND Drag laufen ausschließlich hierüber —
   * ein einziger Zustandspfad.
   */
  private lineEinordnen(uid: string, zielRubrikUid: string | null, zielIndex: number): void {
    const alle = this.lines();
    const moving = alle.find((l) => l.uid === uid);
    if (!moving) return;
    const rest = alle.filter((l) => l.uid !== uid);
    const at = this.flatEinfuegeIndex(rest, zielRubrikUid, zielIndex);
    rest.splice(at, 0, { ...moving, rubrikUid: zielRubrikUid });
    this.lines.set(rest);
    this.markiereGeaendert();
  }

  /** Wie {@link lineEinordnen}, aber für eine noch nicht enthaltene neue Zeile. */
  private lineEinsetzen(line: EditorLine, zielRubrikUid: string | null, zielIndex: number): void {
    const rest = [...this.lines()];
    const at = this.flatEinfuegeIndex(rest, zielRubrikUid, zielIndex);
    rest.splice(at, 0, { ...line, rubrikUid: zielRubrikUid });
    this.lines.set(rest);
    this.markiereGeaendert();
  }

  /**
   * Übersetzt „Stelle `zielIndex` in Gruppe `zielRubrikUid`" in einen Index im
   * flachen Zeilen-Array `arr` (das die zu setzende Zeile NICHT enthält).
   * Ist die Zielgruppe leer, wird ans Array-Ende gehängt (Gruppenreihenfolge
   * ergibt sich beim Speichern ohnehin aus der Rubrik-Reihenfolge).
   */
  private flatEinfuegeIndex(arr: EditorLine[], zielRubrikUid: string | null, zielIndex: number): number {
    const gruppenIdx: number[] = [];
    arr.forEach((l, i) => {
      if (l.rubrikUid === zielRubrikUid) gruppenIdx.push(i);
    });
    if (gruppenIdx.length === 0) return arr.length;
    if (zielIndex <= 0) return gruppenIdx[0];
    if (zielIndex >= gruppenIdx.length) return gruppenIdx[gruppenIdx.length - 1] + 1;
    return gruppenIdx[zielIndex];
  }

  zeileEntfernen(line: EditorLine): void {
    this.lines.update((ls) => ls.filter((l) => l.uid !== line.uid));
    this.ansage.set('Position entfernt.');
    this.markiereGeaendert();
  }

  private rubrikName(uid: string): string {
    return this.rubriken().find((r) => r.uid === uid)?.title ?? 'Abschnitt';
  }

  // ======================= Positionsdetail ================================
  zeileOeffnen(line: EditorLine): void {
    this.posUid = line.uid;
    this.posQuelleArtikelId.set(line.source_article_id);
    this.posFormMeldung.set(null);
    this.posForm.reset({
      description: line.description,
      line_type: line.line_type,
      line_kind: line.line_kind,
      quantity: line.quantity != null ? apiZuDeDezimal(line.quantity) : '',
      unit: line.unit ?? '',
      unit_price: line.unit_price != null ? apiZuDeDezimal(line.unit_price, 2) : '',
      unit_cost: line.unit_cost != null ? apiZuDeDezimal(line.unit_cost, 2) : '',
      discount_percent: line.discount_percent != null ? apiZuDeDezimal(line.discount_percent) : '',
      tax_code: line.tax_code ?? 'DE_19',
      // Häkchen bei jedem Öffnen zurücksetzen — es ist transient und persistiert nie.
      stamm_uebernehmen: false,
    });
    this.posOffen.set(true);
  }

  posDialogSchliessen(): void {
    this.posOffen.set(false);
  }

  /**
   * Ob im Positionsdialog das Häkchen „Änderungen auch in den Artikelstamm
   * übernehmen" angeboten wird: nur bei einer Position mit Quell-Artikel (Kopie
   * eines Stammartikels) UND dem Recht pricing/AENDERN — nicht für Textzeilen.
   */
  zeigeStammHaken(): boolean {
    return (
      !!this.posQuelleArtikelId() && this.darfPricingAendern() && !this.posIstText()
    );
  }

  stammUebernehmenAbbrechen(): void {
    if (this.stammLaedt()) return;
    // Nur die Stamm-Übernahme wird verworfen — die Position ist bereits
    // gespeichert und bleibt erhalten.
    this.stammPending = null;
    this.stammOffen.set(false);
    this.ansage.set('Position übernommen. Der Artikelstamm wurde nicht geändert.');
  }

  stammUebernehmenBestaetigen(): void {
    const pending = this.stammPending;
    if (this.stammLaedt() || !pending) return;
    this.stammLaedt.set(true);
    this.artikelSvc.stammdatenUebernehmen(pending.articleId, pending.payload).subscribe({
      next: () => {
        this.stammLaedt.set(false);
        this.stammOffen.set(false);
        this.stammPending = null;
        this.meldung.set({
          art: 'erfolg',
          text: 'Position übernommen; die Werte wurden in den Artikelstamm gespeichert. Bestehende Belege bleiben unverändert.',
        });
      },
      error: (err) => {
        this.stammLaedt.set(false);
        this.stammOffen.set(false);
        this.stammPending = null;
        // Die Position ist bereits gespeichert und bleibt erhalten — nur die
        // Stamm-Übernahme ist gescheitert. Ehrlicher Fehler, keine Erfolgsmeldung.
        this.meldung.set({
          art: 'fehler',
          text:
            this.fehlerText(err, 'Die Werte konnten nicht in den Artikelstamm übernommen werden.') +
            ' Die Positionsänderung bleibt im Editor erhalten (mit „Speichern" sichern).',
        });
      },
    });
  }

  posSpeichern(): void {
    if (this.posUid === null) return;
    serverFehlerZuruecksetzen(this.posForm);
    this.posFormMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.posForm);
    if (this.posForm.invalid) return;
    const v = this.posForm.getRawValue();
    const text = TEXT_TYPES.includes(v.line_type);
    const kind = v.line_kind;
    if (text && kind !== 'NORMAL') {
      this.posFormMeldung.set(
        'Text- und Zwischensummenzeilen tragen keinen Betrag und können nicht Alternativ/Bedarf sein.',
      );
      return;
    }
    const uid = this.posUid;
    this.lines.update((ls) =>
      ls.map((l) => {
        if (l.uid !== uid) return l;
        if (text) {
          return {
            ...l,
            line_type: v.line_type,
            line_kind: 'NORMAL',
            description: v.description.trim(),
            quantity: null,
            unit: null,
            unit_price: null,
            discount_percent: null,
            tax_code: null,
            unit_cost: null,
            markup_percent: null,
            netAmount: null,
          };
        }
        return {
          ...l,
          line_type: v.line_type,
          line_kind: kind,
          description: v.description.trim(),
          quantity: deZuApiDezimal(v.quantity) || null,
          unit: v.unit.trim() || null,
          unit_price: deZuApiDezimal(v.unit_price) || null,
          discount_percent: deZuApiDezimal(v.discount_percent) || null,
          tax_code: v.tax_code,
          unit_cost: deZuApiDezimal(v.unit_cost) || null,
          // markup wird vom Server neu abgeleitet; lokal verwerfen.
          markup_percent: null,
          netAmount: null,
        };
      }),
    );
    this.markiereGeaendert();

    // Die Position ist jetzt (lokal) gespeichert — reine Kopie-Semantik, es
    // wurde NICHT in den Artikelstamm geschrieben. Nur wenn das transiente
    // Häkchen gesetzt war, folgt als eigener, ausdrücklicher Vorgang die
    // Stamm-Übernahme (hinter einer Bestätigung). Der EK wird bewusst nicht
    // mitgegeben.
    const artikelId = this.posQuelleArtikelId();
    if (!text && v.stamm_uebernehmen && artikelId && this.darfPricingAendern()) {
      this.stammPending = {
        articleId: artikelId,
        payload: {
          description: v.description.trim() || null,
          unit: v.unit.trim() || null,
          verkaufspreis: deZuApiDezimal(v.unit_price) || null,
        },
      };
      this.posOffen.set(false);
      this.ansage.set('Position übernommen. Übernahme in den Artikelstamm bestätigen …');
      this.stammOffen.set(true);
      return;
    }

    this.posOffen.set(false);
    this.ansage.set('Position übernommen.');
  }

  // ======================= Palette ========================================
  setPaletteModus(m: PaletteModus): void {
    if (this.paletteModus() === m) return;
    this.paletteModus.set(m);
    this.paletteTreffer.set([]);
    this.paletteSuche$.next('');
    this.paletteFetch('');
  }

  onPaletteSuche(wert: string): void {
    this.paletteSuche$.next(wert.trim());
  }

  onZielRubrik(wert: string): void {
    this.zielRubrik.set(wert || null);
  }

  private paletteFetch(q: string): void {
    const rid = ++this.paletteReq;
    this.paletteLaedt.set(true);
    this.paletteFehler.set(false);
    if (this.paletteModus() === 'artikel') {
      this.artikelSvc.listArticles({ page: 1, page_size: 20, q }).subscribe({
        next: (p) => {
          if (rid !== this.paletteReq) return;
          this.paletteLaedt.set(false);
          this.paletteTreffer.set(
            p.items.map((a) => ({
              id: a.id,
              zeile1: a.description,
              zeile2: `${a.article_number} · ${a.unit}`,
            })),
          );
        },
        error: (err) => this.paletteError(rid, err),
      });
    } else {
      this.artikelSvc.listAssemblies({ page: 1, page_size: 20, q }).subscribe({
        next: (p) => {
          if (rid !== this.paletteReq) return;
          this.paletteLaedt.set(false);
          this.paletteTreffer.set(
            p.items.map((a) => ({
              id: a.id,
              zeile1: a.name,
              zeile2: `${a.assembly_number} · ${a.unit}`,
            })),
          );
        },
        error: (err) => this.paletteError(rid, err),
      });
    }
  }

  private paletteError(rid: number, err: unknown): void {
    if (rid !== this.paletteReq) return;
    this.paletteLaedt.set(false);
    this.paletteTreffer.set([]);
    this.paletteFehler.set(true);
  }

  /**
   * Palette-Treffer als neue Position übernehmen. Ohne `ziel` (Knopf) landet die
   * Position im aktuell gewählten Zielabschnitt ans Ende; mit `ziel` (Drag) an
   * der Drop-Stelle.
   */
  uebernehmenAusPalette(id: string, ziel?: DropZiel): void {
    if (this.paletteModus() === 'artikel') {
      this.artikelUebernehmen(id, ziel);
    } else {
      this.assemblyUebernehmen(id, ziel);
    }
  }

  private artikelUebernehmen(id: string, ziel?: DropZiel): void {
    const zielRubrik = ziel ? ziel.rubrikUid : this.zielRubrik();
    const zielIndex = ziel ? ziel.index : ANS_ENDE;
    const zielName = zielRubrik === null ? 'Ohne Abschnitt' : this.rubrikName(zielRubrik);
    // Zuerst Basisdaten aus der Liste, dann Kalkulation (EK/VK) nachladen.
    this.artikelSvc.getArticle(id).subscribe({
      next: (art) => {
        const line: EditorLine = {
          uid: neueUid(),
          rubrikUid: zielRubrik,
          line_type: art.line_type as LineType,
          line_kind: 'NORMAL',
          description: art.description,
          quantity: '1',
          unit: art.unit,
          unit_price: null,
          discount_percent: null,
          tax_code: 'DE_19',
          unit_cost: null,
          markup_percent: null,
          sale_price_group_id: null,
          source_article_id: art.id,
          source_assembly_id: null,
          netAmount: null,
          taxRatePercent: null,
        };
        this.lineEinsetzen(line, zielRubrik, zielIndex);
        this.ansage.set(`Artikel „${art.description}" nach „${zielName}" übernommen.`);
        // EK/VK aus der Kalkulation ergänzen (best effort — 403 bleibt ohne EK/VK).
        this.artikelSvc.getKalkulation(id).subscribe({
          next: (k) => {
            const standard = k.variants.find((v) => v.is_standard) ?? k.variants[0];
            this.lines.update((ls) =>
              ls.map((l) =>
                l.uid === line.uid
                  ? {
                      ...l,
                      unit_cost: k.ek,
                      unit_price: standard?.sale_price ?? l.unit_price,
                    }
                  : l,
              ),
            );
          },
          error: () => {
            /* Kalkulation nicht abrufbar → Preise trägt der Nutzer nach. */
          },
        });
      },
      error: (err) => {
        this.meldung.set({ art: 'fehler', text: this.fehlerText(err, 'Artikel konnte nicht geladen werden.') });
      },
    });
  }

  private assemblyUebernehmen(id: string, ziel?: DropZiel): void {
    const zielRubrik = ziel ? ziel.rubrikUid : this.zielRubrik();
    const zielIndex = ziel ? ziel.index : ANS_ENDE;
    const zielName = zielRubrik === null ? 'Ohne Abschnitt' : this.rubrikName(zielRubrik);
    this.artikelSvc.getAssembly(id).subscribe({
      next: (asm) => {
        const line: EditorLine = {
          uid: neueUid(),
          rubrikUid: zielRubrik,
          line_type: 'PAUSCHALE',
          line_kind: 'NORMAL',
          description: asm.name,
          quantity: '1',
          unit: asm.unit,
          unit_price: null,
          discount_percent: null,
          tax_code: 'DE_19',
          unit_cost: null,
          markup_percent: null,
          sale_price_group_id: null,
          source_article_id: null,
          source_assembly_id: asm.id,
          netAmount: null,
          taxRatePercent: null,
        };
        this.lineEinsetzen(line, zielRubrik, zielIndex);
        this.ansage.set(`Leistung „${asm.name}" nach „${zielName}" übernommen. Bitte Einzelpreis ergänzen.`);
      },
      error: (err) => {
        this.meldung.set({ art: 'fehler', text: this.fehlerText(err, 'Leistung konnte nicht geladen werden.') });
      },
    });
  }

  /** Freie Textzeile bzw. leere Position in den Zielabschnitt einfügen. */
  freieZeile(): void {
    const line: EditorLine = {
      uid: neueUid(),
      rubrikUid: this.zielRubrik(),
      line_type: 'MATERIAL',
      line_kind: 'NORMAL',
      description: 'Neue Position',
      quantity: '1',
      unit: null,
      unit_price: null,
      discount_percent: null,
      tax_code: 'DE_19',
      unit_cost: null,
      markup_percent: null,
      sale_price_group_id: null,
      source_article_id: null,
      source_assembly_id: null,
      netAmount: null,
      taxRatePercent: null,
    };
    this.lines.update((ls) => [...ls, line]);
    this.markiereGeaendert();
    this.zeileOeffnen(line);
  }

  // ======================= Speichern ======================================
  private payloadBauen(): QuoteUpdate | InvoiceUpdate {
    const rubriken: RubrikInput[] = this.rubriken().map((r) => ({
      title: r.title,
      description: r.description,
    }));
    const uidZuNummer = new Map<string, number>();
    this.rubriken().forEach((r, i) => uidZuNummer.set(r.uid, i + 1));

    // Zeilen in Anzeigereihenfolge (Abschnitte, dann Ohne Abschnitt) flatten.
    const lines: QuoteLineInput[] = [];
    for (const g of this.gruppen()) {
      for (const l of g.lines) {
        const text = TEXT_TYPES.includes(l.line_type);
        const rubrik = l.rubrikUid != null ? uidZuNummer.get(l.rubrikUid) ?? null : null;
        if (text) {
          lines.push({ line_type: l.line_type, description: l.description, rubrik });
        } else {
          lines.push({
            line_type: l.line_type,
            description: l.description,
            line_kind: l.line_kind,
            rubrik,
            quantity: l.quantity,
            unit: l.unit,
            unit_price: l.unit_price,
            discount_percent: l.discount_percent,
            tax_code: l.tax_code,
            unit_cost: l.unit_cost,
            sale_price_group_id: l.sale_price_group_id,
            source_article_id: l.source_article_id,
            source_assembly_id: l.source_assembly_id,
          });
        }
      }
    }

    const kopf = this.kopfForm.getRawValue();
    if (this.istRechnung) {
      return {
        invoice_date: kopf.invoice_date || null,
        due_date: kopf.due_date || null,
        rubriken,
        lines,
      };
    }
    return {
      title: kopf.title.trim(),
      quote_date: kopf.quote_date || null,
      valid_until_date: kopf.valid_until_date || null,
      rubriken,
      lines,
    };
  }

  speichern(): void {
    if (this.readonly() || this.saving()) return;
    serverFehlerZuruecksetzen(this.kopfForm);
    felderAlsBeruehrtMarkieren(this.kopfForm);
    if (this.kopfForm.invalid) {
      this.meldung.set({ art: 'fehler', text: 'Bitte den Titel des Angebots ausfüllen.' });
      return;
    }
    this.saving.set(true);
    this.meldung.set(null);
    const speichern$: Observable<BelegDetail> = this.istRechnung
      ? this.svc.updateInvoice(this.quoteId, this.payloadBauen() as InvoiceUpdate)
      : this.svc.updateQuote(this.quoteId, this.payloadBauen() as QuoteUpdate);
    speichern$.subscribe({
      next: (data) => {
        this.saving.set(false);
        this.uebernehmen(data);
        this.kalkulationLaden();
        this.meldung.set({
          art: 'erfolg',
          text: `Gespeichert. Netto ${this.euro(data.net_total)}, brutto ${this.euro(data.gross_total)} (vom Server berechnet).`,
        });
      },
      error: (err) => {
        this.saving.set(false);
        this.meldung.set({
          art: 'fehler',
          text: apiFehlerZuweisen(err, this.kopfForm).formular ?? 'Speichern fehlgeschlagen.',
        });
      },
    });
  }

  // ======================= Versenden ======================================
  protected readonly kannVersenden = computed(
    () => !this.readonly() && !this.dirty() && this.darfVersenden(),
  );

  versendenFragen(): void {
    this.meldung.set(null);
    this.versendenOffen.set(true);
  }

  versendenAbbrechen(): void {
    if (!this.versendenLaedt()) this.versendenOffen.set(false);
  }

  versendenBestaetigen(): void {
    if (this.versendenLaedt()) return;
    this.versendenLaedt.set(true);
    // Angebot: versenden (→ VERSENDET). Rechnung: veröffentlichen (→ VEROEFFENTLICHT).
    const aktion$: Observable<BelegDetail> = this.istRechnung
      ? this.svc.publishInvoice(this.quoteId)
      : this.svc.sendQuote(this.quoteId);
    aktion$.subscribe({
      next: (data) => {
        this.versendenLaedt.set(false);
        this.versendenOffen.set(false);
        this.uebernehmen(data);
        const nummer = this.istRechnung
          ? (data as InvoiceDetail).invoice_number
          : (data as QuoteDetail).quote_number;
        this.meldung.set({
          art: 'erfolg',
          text: this.istRechnung
            ? `Rechnung veröffentlicht. Belegnummer ${nummer ?? '—'} wurde vergeben.`
            : `Angebot versendet. Belegnummer ${nummer ?? '—'} wurde vergeben.`,
        });
      },
      error: (err) => {
        this.versendenLaedt.set(false);
        this.versendenOffen.set(false);
        this.meldung.set({ art: 'fehler', text: this.fehlerText(err, 'Versenden fehlgeschlagen.') });
      },
    });
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  private fehlerText(err: unknown, fallback: string): string {
    if (istVerboten(err)) return fehlerDetail(err) ?? 'Keine Berechtigung für diese Aktion.';
    return fehlerDetail(err) ?? fallback;
  }

  // ======================= Darstellungshelfer =============================
  /** Kopf-Überschrift: Angebotstitel bzw. „Rechnung" (Rechnungen haben keinen Titel). */
  kopfTitel(): string {
    const q = this.quote();
    if (!q) return '';
    return this.istRechnung ? this.belegWort : (q as QuoteDetail).title || 'Ohne Titel';
  }
  /** Belegnummer (Angebot: quote_number, Rechnung: invoice_number) oder „Entwurf". */
  belegNummer(): string {
    const q = this.quote();
    if (!q) return 'Entwurf';
    const nr = this.istRechnung
      ? (q as InvoiceDetail).invoice_number
      : (q as QuoteDetail).quote_number;
    return nr ?? 'Entwurf';
  }

  euro(v: string | null): string {
    if (v === null) return '—';
    const n = Number(v);
    if (!Number.isFinite(n)) return '—';
    return this.euroFmt.format(n);
  }

  menge(qty: string | null, unit: string | null): string {
    if (qty === null) return '—';
    const n = Number(qty);
    const f = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 3 }).format(n);
    return unit ? `${f} ${unit}` : f;
  }

  prozent(v: string | null): string {
    if (v === null) return '—';
    const n = Number(v);
    if (!Number.isFinite(n)) return '—';
    return `${new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1 }).format(n)} %`;
  }

  stunden(v: string | null): string {
    if (v == null) return '—';
    const n = Number(v);
    if (!Number.isFinite(n) || n === 0) return '—';
    return `${new Intl.NumberFormat('de-DE', { maximumFractionDigits: 2 }).format(n)} h`;
  }

  lineTypeLabel(t: LineType): string {
    return LINE_TYPE_OPTIONEN.find((o) => o.wert === t)?.label ?? t;
  }

  lineKindLabel(k: LineKind): string {
    return k === 'ALTERNATIV' ? 'Alternative' : k === 'BEDARF' ? 'Bedarf' : '';
  }

  istText(t: LineType): boolean {
    return TEXT_TYPES.includes(t);
  }

  istNebenposition(k: LineKind): boolean {
    return k === 'ALTERNATIV' || k === 'BEDARF';
  }

  /** True, wenn ein (String-)Betrag echt größer null ist. */
  hatBetrag(v: string | null): boolean {
    return Number(v) > 0;
  }

  statusLabel(s: string): string {
    const map: Record<string, string> = {
      ENTWURF: 'Entwurf',
      INTERN_GEPRUEFT: 'Intern geprüft',
      FREIGEGEBEN: 'Freigegeben',
      VERSENDET: 'Versendet',
      ANGENOMMEN: 'Angenommen',
      ABGELEHNT: 'Abgelehnt',
      ABGELAUFEN: 'Abgelaufen',
      ERSETZT: 'Ersetzt',
      VEROEFFENTLICHT: 'Veröffentlicht',
    };
    return map[s] ?? s;
  }

  statusClass(s: string): string {
    if (s === 'ANGENOMMEN' || s === 'VEROEFFENTLICHT') return 'stamp--positive';
    if (s === 'ABGELEHNT' || s === 'ABGELAUFEN' || s === 'ERSETZT') return 'stamp--negativ';
    if (s === 'VERSENDET') return 'stamp--warn';
    return '';
  }
}
