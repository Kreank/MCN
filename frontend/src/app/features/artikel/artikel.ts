import {
  Component,
  ElementRef,
  computed,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Router, RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Subject, debounceTime, distinctUntilChanged, map } from 'rxjs';
import { ArtikelService } from '../../core/artikel.service';
import { BelegerfassungService } from '../../core/belegerfassung.service';
import { PartyService } from '../../core/party.service';
import { AuthService } from '../../core/auth.service';
import {
  Article,
  ArticleIn,
  ArticleLineType,
  ArticlePage,
  AssemblyIn,
  AssemblyPage,
  LieferantIn,
  PriceUnit,
  TaxCode,
} from '../../core/artikel.model';
import { CostCenter } from '../../core/belegerfassung.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { ReferenzWahl, RefSuche } from '../../shared/formular/referenz-wahl';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import { deZuApiDezimal, dezimalValidator } from '../../shared/formular/dezimal';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';

type Modus = 'artikel' | 'leistungen';
type Meldung = { art: 'erfolg' | 'fehler'; text: string };

/** Eine wählbare Zusatzspalte der Artikelliste. `key` ist ein Feld des
 *  Listen-Items (kommt bereits mit, kein Extra-Request). */
interface SpaltenDef {
  key: keyof Article;
  label: string;
}

/** Zusätzlich zu den Standardspalten (Bezeichnung/Nummer/Typ/Einheit/Preis)
 *  wählbare Spalten. Reihenfolge = Anzeigereihenfolge. */
const VERFUEGBARE_SPALTEN: SpaltenDef[] = [
  { key: 'matchcode', label: 'Matchcode' },
  { key: 'product_group', label: 'Kategorie' },
  { key: 'gtin', label: 'EAN / GTIN' },
  { key: 'manufacturer_name', label: 'Hersteller' },
  { key: 'supplier_name', label: 'Lieferant' },
  { key: 'tax_code', label: 'MwSt.' },
  { key: 'price_unit', label: 'Preiseinheit' },
];

const SPALTEN_STORAGE_PREFIX = 'mcn.artikel.spalten';

/** Kurzbeschriftung der Steuerschlüssel für die Listenanzeige. */
const TAX_CODE_KURZ: Record<string, string> = {
  DE_19: '19 %',
  DE_7: '7 %',
  DE_0: '0 %',
  DE_13B: '§13b',
};

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; modus: 'artikel'; data: ArticlePage }
  | { kind: 'ready'; modus: 'leistungen'; data: AssemblyPage }
  | VerbotenState
  | { kind: 'error' };

/** Steuerschlüssel als Auswahl (leer = ohne Angabe, Server setzt Default). */
const TAX_CODE_OPTIONEN: FeldOption[] = [
  { wert: 'DE_19', label: 'USt 19 %' },
  { wert: 'DE_7', label: 'USt 7 %' },
  { wert: 'DE_0', label: 'Steuerfrei (0 %)' },
  { wert: 'DE_13B', label: '§13b UStG (Reverse Charge)' },
];

/** Preiseinheit: der Preis gilt je 1 / 10 / 100 / 1000 Einheiten. */
const PRICE_UNIT_OPTIONEN: FeldOption[] = [
  { wert: '1', label: 'je 1 Einheit' },
  { wert: '10', label: 'je 10 Einheiten' },
  { wert: '100', label: 'je 100 Einheiten' },
  { wert: '1000', label: 'je 1.000 Einheiten' },
];

/** Ganzzahl-Validator (Lieferzeit in Tagen); leer bleibt gültig. */
function ganzzahlValidator(control: { value: unknown }) {
  const roh = control.value;
  if (roh == null || String(roh).trim() === '') return null;
  return /^\d+$/.test(String(roh).trim()) ? null : { ganzzahl: true };
}

@Component({
  selector: 'app-artikel',
  imports: [RouterLink, ReactiveFormsModule, KeinZugriff, Dialog, Feld, ReferenzWahl],
  templateUrl: './artikel.html',
  styleUrl: './artikel.scss',
  host: {
    '(document:keydown.escape)': 'spaltenMenuSchliessen()',
    '(document:click)': 'aufDokumentKlick($event)',
  },
})
export class Artikel {
  private readonly svc = inject(ArtikelService);
  private readonly accountingSvc = inject(BelegerfassungService);
  private readonly partySvc = inject(PartyService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);
  private readonly router = inject(Router);

  protected readonly pageSize = 20;
  protected readonly modus = signal<Modus>('artikel');

  protected readonly query = signal('');
  protected readonly page = signal(1);
  protected readonly state = signal<ViewState>({ kind: 'loading' });

  protected readonly skeletons = Array.from({ length: 6 });

  protected readonly lineTypOptionen: FeldOption[] = [
    { wert: 'MATERIAL', label: 'Material' },
    { wert: 'ARBEITSZEIT', label: 'Arbeitszeit' },
    { wert: 'PAUSCHALE', label: 'Pauschale' },
    { wert: 'FREMDLEISTUNG', label: 'Fremdleistung' },
    { wert: 'FAHRT', label: 'Fahrt' },
    { wert: 'ZUSCHLAG', label: 'Zuschlag' },
  ];
  protected readonly taxCodeOptionen = TAX_CODE_OPTIONEN;
  protected readonly priceUnitOptionen = PRICE_UNIT_OPTIONEN;

  // --- Rechte (nur UI-Sichtbarkeit; der Server setzt sie durch) -----------
  protected readonly darfAnlegen = computed(() => this.auth.darf('pricing', 'ANLEGEN'));
  /** Kostenstellen liegen im Modul `accounting` — reine pricing-Rollen dürfen
   *  sie nicht lesen. Dann bleibt das Dropdown ausgeblendet (kein 403). */
  protected readonly darfKostenstellen = computed(() =>
    this.auth.darf('accounting', 'LESEN'),
  );

  // --- Meldung + Dialoge ---------------------------------------------------
  protected readonly meldung = signal<Meldung | null>(null);
  protected readonly neuLaedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);
  /** Erfolg innerhalb des offenen Dialogs („Speichern und neu"), aria-live. */
  protected readonly neuErfolg = signal<string | null>(null);

  private readonly artikelFormEl =
    viewChild<ElementRef<HTMLElement>>('artikelFormEl');

  // Kostenstellen (nur geladen, wenn accounting/LESEN).
  private readonly costCenters = signal<CostCenter[]>([]);
  protected readonly costCenterOptionen = computed<FeldOption[]>(() =>
    this.costCenters().map((c) => ({ wert: c.id, label: `${c.code} — ${c.label}` })),
  );

  /** Lieferantensuche (optional) über den Kontaktstamm (identity.party). */
  protected readonly lieferantSuche: RefSuche = (q) =>
    this.partySvc
      .list({ page: 1, page_size: 20, q })
      .pipe(map((p) => p.items.map((o) => ({ id: o.id, label: o.display_name }))));

  protected readonly artikelOffen = signal(false);
  protected readonly artikelForm = this.fb.group({
    // Kein Validators.required: leer heisst „die DB vergibt". Die Nummer wird
    // erst im Schreibmoment gezogen, deshalb kollidieren gleichzeitige
    // Erfasser nicht.
    article_number: this.fb.control('', { nonNullable: true }),
    gtin: this.fb.control('', { nonNullable: true }),
    description: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    unit: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    product_group: this.fb.control('', { nonNullable: true }),
    matchcode: this.fb.control('', { nonNullable: true }),
    line_type: this.fb.control<ArticleLineType>('MATERIAL', { nonNullable: true }),
    cost_center_id: this.fb.control('', { nonNullable: true }),
    manufacturer_name: this.fb.control('', { nonNullable: true }),
    manufacturer_number: this.fb.control('', { nonNullable: true }),
    manufacturer_type: this.fb.control('', { nonNullable: true }),
    min_order_quantity: this.fb.control('', {
      nonNullable: true,
      validators: [dezimalValidator],
    }),
    quantity_step: this.fb.control('', {
      nonNullable: true,
      validators: [dezimalValidator],
    }),
    price_unit: this.fb.control('1', { nonNullable: true }),
    delivery_time_days: this.fb.control('', {
      nonNullable: true,
      validators: [ganzzahlValidator],
    }),
    tax_code: this.fb.control('', { nonNullable: true }),
    list_price: this.fb.control('', {
      nonNullable: true,
      validators: [dezimalValidator],
    }),
    long_description: this.fb.control('', { nonNullable: true }),
    // Lieferant (optional): wird nach dem Anlegen als eigener Vorgang gesetzt.
    supplier_party_id: this.fb.control('', { nonNullable: true }),
    supplier_article_number: this.fb.control('', { nonNullable: true }),
    last_purchase_price: this.fb.control('', {
      nonNullable: true,
      validators: [dezimalValidator],
    }),
  });

  protected readonly leistungOffen = signal(false);
  protected readonly leistungForm = this.fb.group({
    // Siehe artikelForm.article_number: leer heisst „die DB vergibt".
    assembly_number: this.fb.control('', { nonNullable: true }),
    internal_name: this.fb.control('', { nonNullable: true }),
    name: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    unit: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    description: this.fb.control('', { nonNullable: true }),
  });

  private readonly searchInput$ = new Subject<string>();
  private reqId = 0;

  protected readonly totalPages = computed(() => {
    const s = this.state();
    if (s.kind !== 'ready') return 1;
    return Math.max(1, Math.ceil(s.data.total / s.data.page_size));
  });

  protected readonly resultSummary = computed(() => {
    const s = this.state();
    const wort = this.modus() === 'artikel' ? 'Artikel' : 'Leistungen';
    if (s.kind === 'loading') return `${wort} werden geladen.`;
    if (s.kind === 'forbidden') return 'Keine Berechtigung für Artikel und Leistungen.';
    if (s.kind === 'error') return `${wort} konnten nicht geladen werden.`;
    const t = s.data.total;
    if (t === 0) return `Keine ${wort} gefunden.`;
    return `${t} Einträge gefunden, Seite ${s.data.page} von ${this.totalPages()}.`;
  });

  // --- Spaltenwahl (nur Artikel; im localStorage je Nutzer gemerkt) --------
  protected readonly verfuegbareSpalten = VERFUEGBARE_SPALTEN;
  protected readonly spaltenOffen = signal(false);
  /** Schlüssel der aktuell eingeblendeten Zusatzspalten. */
  protected readonly gewaehlteSpalten = signal<string[]>(this.spaltenLaden());
  /** Wählmenü-Wrapper — für Klick-außerhalb-schließt. */
  private readonly spaltenWrapEl =
    viewChild<ElementRef<HTMLElement>>('spaltenWrapEl');

  /** Eingeblendete Spalten in definierter Reihenfolge (für das Rendern). */
  protected readonly aktiveSpalten = computed<SpaltenDef[]>(() => {
    const gewaehlt = new Set(this.gewaehlteSpalten());
    return VERFUEGBARE_SPALTEN.filter((s) => gewaehlt.has(s.key));
  });

  private spaltenStorageKey(): string {
    const uid = this.auth.user()?.id ?? 'anon';
    return `${SPALTEN_STORAGE_PREFIX}.${uid}`;
  }

  private spaltenLaden(): string[] {
    try {
      const roh = localStorage.getItem(this.spaltenStorageKey());
      if (!roh) return [];
      const gelesen = JSON.parse(roh);
      if (!Array.isArray(gelesen)) return [];
      // Nur bekannte Schlüssel übernehmen (Stand kann veraltet sein).
      const bekannt = new Set(VERFUEGBARE_SPALTEN.map((s) => s.key as string));
      return gelesen.filter((k) => typeof k === 'string' && bekannt.has(k));
    } catch {
      return [];
    }
  }

  private spaltenSpeichern(keys: string[]): void {
    try {
      localStorage.setItem(this.spaltenStorageKey(), JSON.stringify(keys));
    } catch {
      // localStorage nicht verfügbar/voll — Auswahl gilt nur für diese Sitzung.
    }
  }

  spaltenMenuToggle(): void {
    this.spaltenOffen.update((o) => !o);
  }

  spaltenMenuSchliessen(): void {
    if (this.spaltenOffen()) this.spaltenOffen.set(false);
  }

  spalteAktiv(key: string): boolean {
    return this.gewaehlteSpalten().includes(key);
  }

  spalteUmschalten(key: string): void {
    this.gewaehlteSpalten.update((aktuell) => {
      const neu = aktuell.includes(key)
        ? aktuell.filter((k) => k !== key)
        : [...aktuell, key];
      this.spaltenSpeichern(neu);
      return neu;
    });
  }

  /** Klick außerhalb des Wählmenüs schließt es (Menü bleibt bei Klick darin). */
  aufDokumentKlick(event: MouseEvent): void {
    if (!this.spaltenOffen()) return;
    const wrap = this.spaltenWrapEl()?.nativeElement;
    if (wrap && !wrap.contains(event.target as Node)) {
      this.spaltenOffen.set(false);
    }
  }

  /** Anzeigewert einer Zusatzspalte für ein Listen-Item. */
  spaltenWert(a: Article, key: string): string {
    if (key === 'tax_code') {
      return a.tax_code ? TAX_CODE_KURZ[a.tax_code] ?? a.tax_code : '—';
    }
    if (key === 'price_unit') {
      const n = a.price_unit ?? 1;
      return n === 1
        ? 'je 1'
        : `je ${new Intl.NumberFormat('de-DE').format(n)}`;
    }
    const wert = (a as unknown as Record<string, unknown>)[key];
    return wert == null || wert === '' ? '—' : String(wert);
  }

  constructor() {
    this.searchInput$
      .pipe(debounceTime(300), distinctUntilChanged(), takeUntilDestroyed())
      .subscribe((v) => {
        this.query.set(v);
        this.page.set(1);
        this.fetch();
      });
    this.fetch();
    if (this.darfKostenstellen()) this.kostenstellenLaden();
  }

  onSearch(value: string): void {
    this.searchInput$.next(value);
  }

  selectModus(value: Modus): void {
    if (this.modus() === value) return;
    this.modus.set(value);
    this.query.set('');
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

  private kostenstellenLaden(): void {
    // best effort: nur aktive Kostenstellen; scheitert es, bleibt die Liste leer.
    this.accountingSvc.listCostCenters(false).subscribe({
      next: (c) => this.costCenters.set(c),
      error: () => this.costCenters.set([]),
    });
  }

  // ---- Artikel anlegen ----------------------------------------------------
  private artikelFormLeeren(): void {
    this.artikelForm.reset({
      article_number: '',
      gtin: '',
      description: '',
      unit: '',
      product_group: '',
      matchcode: '',
      line_type: 'MATERIAL',
      cost_center_id: '',
      manufacturer_name: '',
      manufacturer_number: '',
      manufacturer_type: '',
      min_order_quantity: '',
      quantity_step: '',
      price_unit: '1',
      delivery_time_days: '',
      tax_code: '',
      list_price: '',
      long_description: '',
      supplier_party_id: '',
      supplier_article_number: '',
      last_purchase_price: '',
    });
  }

  artikelOeffnen(): void {
    this.artikelFormLeeren();
    this.formularMeldung.set(null);
    this.neuErfolg.set(null);
    this.artikelOffen.set(true);
  }

  artikelSchliessen(): void {
    if (this.neuLaedt()) return;
    this.artikelOffen.set(false);
  }

  /** „Speichern": anlegen und Dialog schließen (Liste zeigt den neuen Artikel). */
  artikelAbsenden(): void {
    this.artikelSpeichern(false);
  }

  /** „Speichern und neu": anlegen, Formular leeren, Dialog offen lassen. */
  artikelSpeichernUndNeu(): void {
    this.artikelSpeichern(true);
  }

  private artikelSpeichern(undNeu: boolean): void {
    if (this.neuLaedt()) return;
    serverFehlerZuruecksetzen(this.artikelForm);
    this.formularMeldung.set(null);
    this.neuErfolg.set(null);
    felderAlsBeruehrtMarkieren(this.artikelForm);
    if (this.artikelForm.invalid) return;

    const v = this.artikelForm.getRawValue();
    const payload: ArticleIn = {
      article_number: v.article_number.trim() || null,
      gtin: v.gtin.trim() || null,
      description: v.description.trim(),
      unit: v.unit.trim(),
      line_type: v.line_type,
      product_group: v.product_group.trim() || null,
      matchcode: v.matchcode.trim() || null,
      manufacturer_name: v.manufacturer_name.trim() || null,
      manufacturer_number: v.manufacturer_number.trim() || null,
      manufacturer_type: v.manufacturer_type.trim() || null,
      cost_center_id: v.cost_center_id || null,
      min_order_quantity: deZuApiDezimal(v.min_order_quantity) || null,
      quantity_step: deZuApiDezimal(v.quantity_step) || null,
      delivery_time_days: v.delivery_time_days.trim()
        ? Number(v.delivery_time_days.trim())
        : null,
      price_unit: (Number(v.price_unit) || 1) as PriceUnit,
      tax_code: (v.tax_code || null) as TaxCode | null,
      list_price: deZuApiDezimal(v.list_price) || null,
      long_description: v.long_description.trim() || null,
    };

    // Optionaler Lieferant: nur wenn eine Partei gewählt UND eine Lieferanten-
    // Artikelnummer gesetzt ist (beides ist Pflicht des Lieferant-Endpunkts).
    const lieferant: LieferantIn | null =
      v.supplier_party_id && v.supplier_article_number.trim()
        ? {
            supplier_party_id: v.supplier_party_id,
            supplier_article_number: v.supplier_article_number.trim(),
            last_purchase_price: deZuApiDezimal(v.last_purchase_price) || null,
            currency: 'EUR',
          }
        : null;

    this.neuLaedt.set(true);
    this.svc.createArticle(payload).subscribe({
      next: (art) => {
        if (!lieferant) {
          this.nachAnlage(art.article_number, art.description, undNeu, null);
          return;
        }
        // Lieferant als Folge-Vorgang setzen (eigener Endpunkt).
        this.svc.setLieferant(art.id, lieferant).subscribe({
          next: () =>
            this.nachAnlage(art.article_number, art.description, undNeu, null),
          error: (err) =>
            this.nachAnlage(
              art.article_number,
              art.description,
              undNeu,
              apiFehlerZuweisen(err, this.artikelForm).formular ??
                'Der Lieferant konnte nicht gesetzt werden.',
            ),
        });
      },
      error: (err) => {
        this.neuLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.artikelForm).formular);
      },
    });
  }

  /** Nach erfolgreichem Anlegen (Lieferant ggf. mit Teilfehler). */
  private nachAnlage(
    nummer: string,
    beschreibung: string,
    undNeu: boolean,
    lieferantFehler: string | null,
  ): void {
    this.neuLaedt.set(false);
    const basis = `Artikel ${nummer} „${beschreibung}“ wurde angelegt.`;
    const text = lieferantFehler ? `${basis} Hinweis: ${lieferantFehler}` : basis;

    if (undNeu) {
      // Formular leeren, Dialog offen lassen, Fokus auf Artikelname.
      this.artikelFormLeeren();
      this.neuErfolg.set(text);
      queueMicrotask(() => {
        const el = this.artikelFormEl()?.nativeElement;
        const erstes = el?.querySelector<HTMLInputElement>('input, textarea, select');
        erstes?.focus();
      });
    } else {
      this.artikelOffen.set(false);
      this.meldung.set({ art: 'erfolg', text });
    }
    this.modus.set('artikel');
    this.query.set('');
    this.page.set(1);
    this.fetch();
  }

  // ---- Leistung anlegen ---------------------------------------------------
  leistungOeffnen(): void {
    this.leistungForm.reset({
      assembly_number: '',
      internal_name: '',
      name: '',
      unit: '',
      description: '',
    });
    this.formularMeldung.set(null);
    this.leistungOffen.set(true);
  }

  leistungSchliessen(): void {
    if (this.neuLaedt()) return;
    this.leistungOffen.set(false);
  }

  leistungAbsenden(): void {
    if (this.neuLaedt()) return;
    serverFehlerZuruecksetzen(this.leistungForm);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.leistungForm);
    if (this.leistungForm.invalid) return;

    const v = this.leistungForm.getRawValue();
    const payload: AssemblyIn = {
      assembly_number: v.assembly_number.trim() || null,
      internal_name: v.internal_name.trim() || null,
      name: v.name.trim(),
      unit: v.unit.trim(),
      description: v.description.trim() || null,
    };

    this.neuLaedt.set(true);
    this.svc.createAssembly(payload).subscribe({
      next: (a) => {
        this.neuLaedt.set(false);
        this.leistungOffen.set(false);
        // Direkt in die neue Leistung: dort wird die Stückliste gefüllt. Wer in
        // der Liste landete, musste die eben angelegte Leistung erst wiederfinden.
        this.router.navigate(['/leistungen', a.id]);
      },
      error: (err) => {
        this.neuLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.leistungForm).formular);
      },
    });
  }

  private fetch(): void {
    const id = ++this.reqId;
    const modus = this.modus();
    this.state.set({ kind: 'loading' });
    const query = { page: this.page(), page_size: this.pageSize, q: this.query() };
    if (modus === 'artikel') {
      this.svc.listArticles(query).subscribe({
        next: (data) => {
          if (id === this.reqId) this.state.set({ kind: 'ready', modus, data });
        },
        error: (err) => {
          if (id === this.reqId) this.state.set(fehlerState(err));
        },
      });
    } else {
      this.svc.listAssemblies(query).subscribe({
        next: (data) => {
          if (id === this.reqId) this.state.set({ kind: 'ready', modus, data });
        },
        error: (err) => {
          if (id === this.reqId) this.state.set(fehlerState(err));
        },
      });
    }
  }

  // ---- Darstellungshelfer -------------------------------------------------
  euro(amount: string | null): string {
    if (amount === null) return '—';
    return new Intl.NumberFormat('de-DE', {
      style: 'currency',
      currency: 'EUR',
    }).format(Number(amount));
  }

  lineTypeLabel(t: ArticleLineType): string {
    const map: Record<ArticleLineType, string> = {
      MATERIAL: 'Material',
      ARBEITSZEIT: 'Arbeitszeit',
      PAUSCHALE: 'Pauschale',
      FREMDLEISTUNG: 'Fremdleistung',
      FAHRT: 'Fahrt',
      ZUSCHLAG: 'Zuschlag',
    };
    return map[t] ?? t;
  }
}
