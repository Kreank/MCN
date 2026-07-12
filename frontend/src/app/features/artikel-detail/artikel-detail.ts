import { HttpEventType } from '@angular/common/http';
import { Component, DestroyRef, computed, effect, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { map } from 'rxjs';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';
import { Dialog } from '../../shared/dialog/dialog';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { ReferenzWahl, RefSuche } from '../../shared/formular/referenz-wahl';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import {
  apiZuDeEingabe,
  deZuApiDezimal,
  dezimalValidator,
  istDezimalApiWert,
} from '../../shared/formular/dezimal';
import { gtinValidator } from '../../shared/formular/gtin';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';
import { ArtikelService } from '../../core/artikel.service';
import { BelegerfassungService } from '../../core/belegerfassung.service';
import { PartyService } from '../../core/party.service';
import { DateiService } from '../../core/datei.service';
import { AuthService } from '../../core/auth.service';
import {
  ArticleDetail,
  ArticleLineType,
  ArticleUpdateIn,
  HistorieEintrag,
  LieferantIn,
  PriceUnit,
  StammStatus,
  TaxCode,
  VerkaufspreiseUebersicht,
} from '../../core/artikel.model';
import { CostCenter } from '../../core/belegerfassung.model';
import { Datei } from '../../core/datei.model';

type VkState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; data: VerkaufspreiseUebersicht }
  | VerbotenState
  | { kind: 'error' };

type HistorieState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; data: HistorieEintrag[] }
  | VerbotenState
  | { kind: 'error' };

type ViewState =
  { kind: 'loading' } | { kind: 'ready'; data: ArticleDetail } | VerbotenState | { kind: 'error' };

/** Vorschau-Zustand des Artikelbilds. */
type BildState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; datei: Datei | null; url: string | null }
  | { kind: 'error' };

type Meldung = { art: 'erfolg' | 'fehler'; text: string };

const LINE_TYPE_OPTIONEN: FeldOption[] = [
  { wert: 'MATERIAL', label: 'Material' },
  { wert: 'ARBEITSZEIT', label: 'Arbeitszeit' },
  { wert: 'PAUSCHALE', label: 'Pauschale' },
  { wert: 'FREMDLEISTUNG', label: 'Fremdleistung' },
  { wert: 'FAHRT', label: 'Fahrt' },
  { wert: 'ZUSCHLAG', label: 'Zuschlag' },
];

const TAX_CODE_OPTIONEN: FeldOption[] = [
  { wert: 'DE_19', label: 'USt 19 %' },
  { wert: 'DE_7', label: 'USt 7 %' },
  { wert: 'DE_0', label: 'Steuerfrei (0 %)' },
  { wert: 'DE_13B', label: '§13b UStG (Reverse Charge)' },
];

const PRICE_UNIT_OPTIONEN: FeldOption[] = [
  { wert: '1', label: 'je 1 Einheit' },
  { wert: '10', label: 'je 10 Einheiten' },
  { wert: '100', label: 'je 100 Einheiten' },
  { wert: '1000', label: 'je 1.000 Einheiten' },
];

const TAX_CODE_LABEL: Record<string, string> = {
  DE_19: '19 %',
  DE_7: '7 %',
  DE_0: '0 % (steuerfrei)',
  DE_13B: '§13b (Reverse Charge)',
};

/** Deutsche Beschriftung der Audit-Feldnamen (DB-Spalten) für die Historie. */
const FELD_LABEL: Record<string, string> = {
  article_number: 'Artikelnummer',
  description: 'Bezeichnung',
  long_description: 'Langtext',
  unit: 'Einheit',
  line_type: 'Typ',
  list_price: 'Listenpreis',
  gtin: 'GTIN',
  manufacturer_name: 'Hersteller',
  manufacturer_number: 'Hersteller-Nr.',
  manufacturer_type: 'Hersteller-Typ',
  product_group: 'Warengruppe',
  matchcode: 'Matchcode',
  min_order_quantity: 'Mindestbestellmenge',
  quantity_step: 'Mengenstaffel',
  delivery_time_days: 'Lieferzeit (Tage)',
  tax_code: 'Steuerschlüssel',
  price_unit: 'Preiseinheit',
  status: 'Status',
};

// Werte aus audit.audit_entry.action. Der Trigger schreibt nur ROW_UPDATE und
// ROW_DELETE — ein Anlegen wird nicht auditiert.
const AKTION_LABEL: Record<string, string> = {
  ROW_UPDATE: 'Geändert',
  ROW_DELETE: 'Gelöscht',
};

/** Ganzzahl-Validator (Lieferzeit in Tagen); leer bleibt gültig. */
function ganzzahlValidator(control: { value: unknown }) {
  const roh = control.value;
  if (roh == null || String(roh).trim() === '') return null;
  return /^\d+$/.test(String(roh).trim()) ? null : { ganzzahl: true };
}

@Component({
  selector: 'app-artikel-detail',
  imports: [
    Mappe,
    RouterLink,
    ReactiveFormsModule,
    KeinZugriff,
    Dialog,
    Bestaetigung,
    Feld,
    ReferenzWahl,
  ],
  templateUrl: './artikel-detail.html',
  styleUrl: './artikel-detail.scss',
})
export class ArtikelDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly svc = inject(ArtikelService);
  private readonly accountingSvc = inject(BelegerfassungService);
  private readonly partySvc = inject(PartyService);
  private readonly dateiSvc = inject(DateiService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly tab = signal('informationen');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  protected readonly tabs: MappeTab[] = [
    { id: 'informationen', label: 'Informationen' },
    { id: 'kalkulation', label: 'Kalkulation' },
    { id: 'historie', label: 'Historie' },
  ];

  protected readonly lineTypeOptionen = LINE_TYPE_OPTIONEN;
  protected readonly taxCodeOptionen = TAX_CODE_OPTIONEN;
  protected readonly priceUnitOptionen = PRICE_UNIT_OPTIONEN;

  protected readonly vk = signal<VkState>({ kind: 'idle' });
  private vkReqId = 0;
  /** Editierbare VK/Einheit-Felder je Gruppe (deutsche Eingabe-Strings). */
  protected readonly vkFelder = signal<Record<string, string>>({});
  /** Ausgewählte Standard-VK-Gruppe (genau eine). */
  protected readonly vkStandard = signal<string>('');
  protected readonly vkSaving = signal(false);
  protected readonly vkMeldung = signal<string | null>(null);

  protected readonly historie = signal<HistorieState>({ kind: 'idle' });
  private historieReqId = 0;

  // --- Artikelbild ---------------------------------------------------------
  protected readonly bild = signal<BildState>({ kind: 'idle' });
  private bildReqId = 0;
  private bildUrl: string | null = null; // Object-URL zum Freigeben
  protected readonly bildLaedt = signal(false);
  protected readonly bildMeldung = signal<string | null>(null);

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  // --- Rechte (nur UI-Sichtbarkeit; der Server setzt sie durch) -----------
  protected readonly darfAendern = computed(() => this.auth.darf('pricing', 'AENDERN'));
  protected readonly darfAnlegen = computed(() => this.auth.darf('pricing', 'ANLEGEN'));
  protected readonly darfKostenstellen = computed(() => this.auth.darf('accounting', 'LESEN'));
  protected readonly darfBildAnlegen = computed(() => this.auth.darf('content', 'ANLEGEN'));
  protected readonly darfBildLoesen = computed(() => this.auth.darf('content', 'AENDERN'));

  protected readonly meldung = signal<Meldung | null>(null);

  // Kostenstellen (nur wenn accounting/LESEN).
  private readonly costCenters = signal<CostCenter[]>([]);
  protected readonly costCenterOptionen = computed<FeldOption[]>(() =>
    this.costCenters().map((c) => ({ wert: c.id, label: `${c.code} — ${c.label}` })),
  );

  /** Lieferantensuche (optional) über den Kontaktstamm. */
  protected readonly lieferantSuche: RefSuche = (q) =>
    this.partySvc
      .list({ page: 1, page_size: 20, q })
      .pipe(map((p) => p.items.map((o) => ({ id: o.id, label: o.display_name }))));

  // --- Artikel bearbeiten (Dialog) -----------------------------------------
  protected readonly bearbeitenOffen = signal(false);
  protected readonly bearbeitenLaedt = signal(false);
  protected readonly bearbeitenMeldung = signal<string | null>(null);
  protected readonly bearbeitenForm = this.fb.group({
    article_number: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    description: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    unit: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    line_type: this.fb.control<ArticleLineType>('MATERIAL', { nonNullable: true }),
    product_group: this.fb.control('', { nonNullable: true }),
    matchcode: this.fb.control('', { nonNullable: true }),
    cost_center_id: this.fb.control('', { nonNullable: true }),
    manufacturer_name: this.fb.control('', { nonNullable: true }),
    manufacturer_number: this.fb.control('', { nonNullable: true }),
    manufacturer_type: this.fb.control('', { nonNullable: true }),
    gtin: this.fb.control('', { nonNullable: true, validators: [gtinValidator] }),
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
    list_price: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
    long_description: this.fb.control('', { nonNullable: true }),
    // Lieferant (optional).
    supplier_party_id: this.fb.control('', { nonNullable: true }),
    supplier_article_number: this.fb.control('', { nonNullable: true }),
    last_purchase_price: this.fb.control('', {
      nonNullable: true,
      validators: [dezimalValidator],
    }),
  });

  // --- Status ändern (Deaktivieren hinter Bestätigung) ---------------------
  protected readonly statusOffen = signal(false);
  protected readonly statusLaedt = signal(false);

  // --- Kopieren (Hero „Kopieren"): neuer Artikel aus diesem ----------------
  protected readonly kopierenOffen = signal(false);
  protected readonly kopierenLaedt = signal(false);
  protected readonly kopierenMeldung = signal<string | null>(null);
  protected readonly kopierenForm = this.fb.group({
    article_number: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
  });

  constructor() {
    if (this.darfKostenstellen()) this.kostenstellenLaden();

    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.tab.set('informationen');
      this.vk.set({ kind: 'idle' });
      this.historie.set({ kind: 'idle' });
      this.bildFreigeben();
      this.bild.set({ kind: 'idle' });
      this.meldung.set(null);
      if (!id) {
        this.state.set({ kind: 'error' });
        return;
      }
      this.load(id);
    });

    // Artikelbild laden, sobald der Artikel steht (einmal je Artikel).
    effect(() => {
      const art = this.daten();
      if (art && this.bild().kind === 'idle') {
        this.ladeBild(art.id);
      }
    });

    // Verkaufspreise erst beim Öffnen des Kalkulations-Tabs nachladen.
    effect(() => {
      const art = this.daten();
      if (this.tab() === 'kalkulation' && art && this.vk().kind === 'idle') {
        this.loadVerkaufspreise(art.id);
      }
    });

    // Historie ebenso lazy nachladen (und nach Änderungen erneut).
    effect(() => {
      const art = this.daten();
      if (this.tab() === 'historie' && art && this.historie().kind === 'idle') {
        this.loadHistorie(art.id);
      }
    });

    this.destroyRef.onDestroy(() => this.bildFreigeben());
  }

  retry(): void {
    const id = this.route.snapshot.paramMap.get('id');
    if (id) this.load(id);
  }

  private load(id: string): void {
    const rid = ++this.reqId;
    this.state.set({ kind: 'loading' });
    this.svc.getArticle(id).subscribe({
      next: (data) => {
        if (rid === this.reqId) this.state.set({ kind: 'ready', data });
      },
      error: (err) => {
        if (rid === this.reqId) this.state.set(fehlerState(err));
      },
    });
  }

  private kostenstellenLaden(): void {
    this.accountingSvc.listCostCenters(false).subscribe({
      next: (c) => this.costCenters.set(c),
      error: () => this.costCenters.set([]),
    });
  }

  retryVk(): void {
    const art = this.daten();
    if (art) this.loadVerkaufspreise(art.id);
  }

  retryHistorie(): void {
    const art = this.daten();
    if (art) this.loadHistorie(art.id);
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  /**
   * Nach einer Änderung (Bearbeiten/Status): frisch geladenen Artikel übernehmen
   * und die abgeleiteten Tabs (Kalkulation/Historie) zum Nachladen verwerfen.
   */
  private nachAenderung(data: ArticleDetail): void {
    this.state.set({ kind: 'ready', data });
    this.vk.set({ kind: 'idle' });
    this.historie.set({ kind: 'idle' });
    if (this.tab() === 'kalkulation') this.loadVerkaufspreise(data.id);
    if (this.tab() === 'historie') this.loadHistorie(data.id);
  }

  // ---- Artikel bearbeiten -------------------------------------------------
  bearbeitenOeffnen(): void {
    const art = this.daten();
    if (!art) return;
    this.bearbeitenMeldung.set(null);
    this.bearbeitenForm.reset({
      article_number: art.article_number,
      description: art.description,
      unit: art.unit,
      line_type: art.line_type,
      product_group: art.product_group ?? '',
      matchcode: art.matchcode ?? '',
      cost_center_id: art.cost_center_id ?? '',
      manufacturer_name: art.manufacturer_name ?? '',
      manufacturer_number: art.manufacturer_number ?? '',
      manufacturer_type: art.manufacturer_type ?? '',
      gtin: art.gtin ?? '',
      min_order_quantity:
        art.min_order_quantity != null ? apiZuDeEingabe(art.min_order_quantity) : '',
      quantity_step: art.quantity_step != null ? apiZuDeEingabe(art.quantity_step) : '',
      price_unit: String(art.price_unit ?? 1),
      delivery_time_days: art.delivery_time_days != null ? String(art.delivery_time_days) : '',
      tax_code: art.tax_code ?? '',
      // list_price hat vier Nachkommastellen; unverändert anzeigen (kein Runden).
      list_price: art.list_price != null ? apiZuDeEingabe(art.list_price) : '',
      long_description: art.long_description ?? '',
      supplier_party_id: art.supplier_party_id ?? '',
      supplier_article_number: art.supplier_article_number ?? '',
      last_purchase_price:
        art.last_purchase_price != null ? apiZuDeEingabe(art.last_purchase_price) : '',
    });
    this.bearbeitenOffen.set(true);
  }

  bearbeitenSchliessen(): void {
    if (this.bearbeitenLaedt()) return;
    this.bearbeitenOffen.set(false);
  }

  bearbeitenAbsenden(): void {
    const art = this.daten();
    if (this.bearbeitenLaedt() || !art) return;
    serverFehlerZuruecksetzen(this.bearbeitenForm);
    this.bearbeitenMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.bearbeitenForm);
    if (this.bearbeitenForm.invalid) return;

    const v = this.bearbeitenForm.getRawValue();
    // Lieferant: eine gewählte Partei verlangt eine Lieferanten-Artikelnummer.
    const supplier = v.supplier_party_id;
    const supplierNr = v.supplier_article_number.trim();
    if (supplier && !supplierNr) {
      this.bearbeitenForm.controls.supplier_article_number.setErrors({ server: true });
      this.bearbeitenMeldung.set(
        'Bitte die Lieferanten-Artikelnummer angeben (Pflicht, wenn ein Lieferant gewählt ist).',
      );
      return;
    }

    const payload: ArticleUpdateIn = {
      article_number: v.article_number.trim(),
      description: v.description.trim(),
      unit: v.unit.trim(),
      line_type: v.line_type,
      product_group: v.product_group.trim() || null,
      matchcode: v.matchcode.trim() || null,
      cost_center_id: v.cost_center_id || null,
      manufacturer_name: v.manufacturer_name.trim() || null,
      manufacturer_number: v.manufacturer_number.trim() || null,
      manufacturer_type: v.manufacturer_type.trim() || null,
      gtin: v.gtin.trim() || null,
      min_order_quantity: deZuApiDezimal(v.min_order_quantity) || null,
      quantity_step: deZuApiDezimal(v.quantity_step) || null,
      price_unit: (Number(v.price_unit) || 1) as PriceUnit,
      delivery_time_days: v.delivery_time_days.trim() ? Number(v.delivery_time_days.trim()) : null,
      tax_code: (v.tax_code || null) as TaxCode | null,
      list_price: deZuApiDezimal(v.list_price) || null,
      long_description: v.long_description.trim() || null,
    };

    const lieferant: LieferantIn | null = supplier
      ? {
          supplier_party_id: supplier,
          supplier_article_number: supplierNr,
          last_purchase_price: deZuApiDezimal(v.last_purchase_price) || null,
          currency: 'EUR',
        }
      : null;

    this.bearbeitenLaedt.set(true);
    this.svc.updateArticle(art.id, payload).subscribe({
      next: (data) => {
        if (!lieferant) {
          this.bearbeitenFertig(data, null);
          return;
        }
        this.svc.setLieferant(art.id, lieferant).subscribe({
          next: (mitLieferant) => this.bearbeitenFertig(mitLieferant, null),
          error: (err) =>
            this.bearbeitenFertig(
              data,
              apiFehlerZuweisen(err, this.bearbeitenForm).formular ??
                'Der Lieferant konnte nicht gesetzt werden.',
            ),
        });
      },
      error: (err) => {
        this.bearbeitenLaedt.set(false);
        this.bearbeitenMeldung.set(apiFehlerZuweisen(err, this.bearbeitenForm).formular);
      },
    });
  }

  private bearbeitenFertig(data: ArticleDetail, lieferantFehler: string | null): void {
    this.bearbeitenLaedt.set(false);
    this.bearbeitenOffen.set(false);
    this.nachAenderung(data);
    this.meldung.set({
      art: lieferantFehler ? 'fehler' : 'erfolg',
      text: lieferantFehler
        ? `Stammdaten gespeichert. Lieferant nicht gesetzt: ${lieferantFehler}`
        : 'Die Artikelstammdaten wurden gespeichert.',
    });
  }

  // ---- Status ändern ------------------------------------------------------
  deaktivierenFragen(): void {
    this.meldung.set(null);
    this.statusOffen.set(true);
  }

  statusAbbrechen(): void {
    if (!this.statusLaedt()) this.statusOffen.set(false);
  }

  deaktivierenBestaetigen(): void {
    if (this.statusLaedt()) return;
    this.statusSetzen('INAKTIV', () => this.statusOffen.set(false));
  }

  aktivieren(): void {
    if (this.statusLaedt()) return;
    this.statusSetzen('AKTIV');
  }

  private statusSetzen(status: StammStatus, danach?: () => void): void {
    const art = this.daten();
    if (!art) return;
    this.statusLaedt.set(true);
    this.svc.setArticleStatus(art.id, status).subscribe({
      next: (data) => {
        this.statusLaedt.set(false);
        danach?.();
        this.nachAenderung(data);
        this.meldung.set({
          art: 'erfolg',
          text:
            status === 'AKTIV'
              ? 'Der Artikel wurde aktiviert.'
              : 'Der Artikel wurde deaktiviert und erscheint nicht mehr in der Suche.',
        });
      },
      error: (err) => {
        this.statusLaedt.set(false);
        danach?.();
        this.meldung.set({
          art: 'fehler',
          text:
            apiFehlerZuweisen(err, this.bearbeitenForm).formular ?? 'Statuswechsel fehlgeschlagen.',
        });
      },
    });
  }

  // ---- Kopieren -----------------------------------------------------------
  kopierenOeffnen(): void {
    const art = this.daten();
    if (!art) return;
    this.kopierenMeldung.set(null);
    serverFehlerZuruecksetzen(this.kopierenForm);
    // Vorschlag: alte Nummer + Suffix, editierbar.
    this.kopierenForm.reset({ article_number: `${art.article_number}-KOPIE` });
    this.kopierenOffen.set(true);
  }

  kopierenSchliessen(): void {
    if (this.kopierenLaedt()) return;
    this.kopierenOffen.set(false);
  }

  kopierenAbsenden(): void {
    const art = this.daten();
    if (this.kopierenLaedt() || !art) return;
    serverFehlerZuruecksetzen(this.kopierenForm);
    this.kopierenMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.kopierenForm);
    if (this.kopierenForm.invalid) return;

    const nummer = this.kopierenForm.controls.article_number.value.trim();
    this.kopierenLaedt.set(true);
    this.svc.copyArticle(art.id, { article_number: nummer }).subscribe({
      next: (neu) => {
        this.kopierenLaedt.set(false);
        this.kopierenOffen.set(false);
        this.meldung.set({
          art: 'erfolg',
          text: `Kopie „${neu.article_number}“ wurde angelegt. Die GTIN wurde nicht übernommen.`,
        });
        // Zum neuen Artikel navigieren (die Mappe lädt frisch über paramMap).
        this.router.navigate(['/artikel', neu.id]);
      },
      error: (err) => {
        this.kopierenLaedt.set(false);
        this.kopierenMeldung.set(apiFehlerZuweisen(err, this.kopierenForm).formular);
      },
    });
  }

  // ---- Artikelbild --------------------------------------------------------
  private bildFreigeben(): void {
    if (this.bildUrl) {
      URL.revokeObjectURL(this.bildUrl);
      this.bildUrl = null;
    }
  }

  private ladeBild(articleId: string): void {
    const rid = ++this.bildReqId;
    this.bildFreigeben();
    this.bild.set({ kind: 'loading' });
    this.bildMeldung.set(null);
    this.dateiSvc.liste({ article_id: articleId }).subscribe({
      next: (liste) => {
        if (rid !== this.bildReqId) return;
        const treffer = liste.items.find((d) => d.link_category === 'ARTIKELBILD') ?? null;
        if (!treffer) {
          this.bild.set({ kind: 'ready', datei: null, url: null });
          return;
        }
        // Inhalt durch die Anwendung holen (Auth-Cookie/CSRF) → Object-URL.
        this.dateiSvc.herunterladen(treffer.file_id, treffer.original_filename).subscribe({
          next: ({ blob }) => {
            if (rid !== this.bildReqId) return;
            this.bildUrl = URL.createObjectURL(blob);
            this.bild.set({ kind: 'ready', datei: treffer, url: this.bildUrl });
          },
          error: () => {
            if (rid === this.bildReqId) this.bild.set({ kind: 'ready', datei: treffer, url: null });
          },
        });
      },
      error: () => {
        if (rid === this.bildReqId) this.bild.set({ kind: 'error' });
      },
    });
  }

  bildFeldGeaendert(event: Event): void {
    const input = event.target as HTMLInputElement;
    const datei = input.files?.[0];
    input.value = ''; // gleiche Datei erneut wählbar
    if (datei) this.bildHochladen(datei);
  }

  private bildHochladen(datei: File): void {
    const art = this.daten();
    if (!art || this.bildLaedt() || !this.darfBildAnlegen()) return;
    this.bildLaedt.set(true);
    this.bildMeldung.set(null);

    // Höchstens ein ARTIKELBILD je Artikel: ein vorhandenes zuerst lösen.
    const b = this.bild();
    const alt = b.kind === 'ready' ? b.datei : null;
    const upload = () => {
      this.dateiSvc.hochladen({ article_id: art.id }, datei, 'ARTIKELBILD').subscribe({
        next: (ev) => {
          if (ev.type === HttpEventType.Response) {
            this.bildLaedt.set(false);
            this.ladeBild(art.id);
          }
        },
        error: (err) => {
          this.bildLaedt.set(false);
          this.bildMeldung.set(fehlerDetail(err) ?? 'Das Bild konnte nicht hochgeladen werden.');
        },
      });
    };

    if (alt) {
      this.dateiSvc.verknuepfungLoesen(alt.link_id).subscribe({
        next: () => upload(),
        error: (err) => {
          this.bildLaedt.set(false);
          this.bildMeldung.set(
            fehlerDetail(err) ?? 'Das bisherige Bild konnte nicht entfernt werden.',
          );
        },
      });
    } else {
      upload();
    }
  }

  bildEntfernen(): void {
    const art = this.daten();
    const b = this.bild();
    const datei = b.kind === 'ready' ? b.datei : null;
    if (!art || !datei || this.bildLaedt() || !this.darfBildLoesen()) return;
    this.bildLaedt.set(true);
    this.bildMeldung.set(null);
    this.dateiSvc.verknuepfungLoesen(datei.link_id).subscribe({
      next: () => {
        this.bildLaedt.set(false);
        this.bildFreigeben();
        this.bild.set({ kind: 'ready', datei: null, url: null });
      },
      error: (err) => {
        this.bildLaedt.set(false);
        this.bildMeldung.set(fehlerDetail(err) ?? 'Das Bild konnte nicht entfernt werden.');
      },
    });
  }

  // ---- Historie -----------------------------------------------------------
  private loadHistorie(id: string): void {
    const rid = ++this.historieReqId;
    this.historie.set({ kind: 'loading' });
    this.svc.articleHistorie(id, 50).subscribe({
      next: (data) => {
        if (rid === this.historieReqId) this.historie.set({ kind: 'ready', data });
      },
      error: (err) => {
        if (rid === this.historieReqId) this.historie.set(fehlerState(err));
      },
    });
  }

  feldLabel(feld: string): string {
    return FELD_LABEL[feld] ?? feld;
  }

  aktionLabel(action: string): string {
    return AKTION_LABEL[action] ?? action;
  }

  historieZeit(iso: string): string {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return new Intl.DateTimeFormat('de-DE', {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(d);
  }

  // ---- Verkaufspreise (Hero-Kalkulation) ----------------------------------
  private loadVerkaufspreise(id: string): void {
    const rid = ++this.vkReqId;
    this.vk.set({ kind: 'loading' });
    this.vkMeldung.set(null);
    this.svc.getVerkaufspreise(id).subscribe({
      next: (data) => {
        if (rid !== this.vkReqId) return;
        this.vkAusDaten(data);
        this.vk.set({ kind: 'ready', data });
      },
      error: (err) => {
        if (rid === this.vkReqId) this.vk.set(fehlerState(err));
      },
    });
  }

  /** Editierfelder + Standardauswahl aus der Server-Übersicht initialisieren. */
  private vkAusDaten(data: VerkaufspreiseUebersicht): void {
    const felder: Record<string, string> = {};
    let standard = '';
    for (const g of data.groups) {
      felder[g.sale_price_group_id] =
        g.effective_sale_price != null ? apiZuDeEingabe(g.effective_sale_price, 2) : '';
      if (g.is_standard) standard = g.sale_price_group_id;
    }
    if (!standard && data.groups.length) standard = data.groups[0].sale_price_group_id;
    this.vkFelder.set(felder);
    this.vkStandard.set(standard);
  }

  /** VK/Einheit-Feld einer Gruppe aktualisieren. */
  vkFeldGeaendert(gruppeId: string, wert: string): void {
    this.vkFelder.update((f) => ({ ...f, [gruppeId]: wert }));
  }

  vkStandardWaehlen(gruppeId: string): void {
    this.vkStandard.set(gruppeId);
  }

  vkFeldWert(gruppeId: string): string {
    return this.vkFelder()[gruppeId] ?? '';
  }

  vkSpeichern(): void {
    const s = this.vk();
    if (this.vkSaving() || s.kind !== 'ready') return;
    const art = this.daten();
    if (!art) return;
    const groups = s.data.groups;
    if (groups.length === 0) return;

    const standard = this.vkStandard();
    if (!standard || !groups.some((g) => g.sale_price_group_id === standard)) {
      this.vkMeldung.set('Bitte genau eine Standard-VK-Gruppe wählen.');
      return;
    }

    // Unlesbare oder mehrdeutige Eingabe („1.500") niemals stumm als Zahl deuten —
    // die VK-Felder haengen an keinem FormControl, also hier selbst pruefen.
    if (
      groups.some((g) => !istDezimalApiWert(deZuApiDezimal(this.vkFeldWert(g.sale_price_group_id))))
    ) {
      this.vkMeldung.set(
        'Ein VK-Preis ist keine eindeutige Zahl. Bitte ohne Tausenderpunkt schreiben (1500) ' +
          'oder mit Komma (1500,00).',
      );
      return;
    }

    // „Unverändert = Formel, geändert = Überschreibung": stimmt der eingegebene
    // Wert exakt mit dem errechneten VK überein, wird KEIN Festpreis gesendet
    // (fixed_price=null) — dann gewinnt die Formel.
    const entries = groups.map((g) => {
      const api = deZuApiDezimal(this.vkFeldWert(g.sale_price_group_id));
      let fixed: string | null;
      if (api === '') {
        fixed = null;
      } else if (
        g.computed_sale_price != null &&
        this.gleicheBetraege(api, g.computed_sale_price)
      ) {
        fixed = null; // entspricht dem Formelwert → Formel gewinnt
      } else {
        fixed = api; // echte Abweichung → Überschreibung
      }
      return {
        sale_price_group_id: g.sale_price_group_id,
        fixed_price: fixed,
        is_standard: g.sale_price_group_id === standard,
      };
    });

    this.vkSaving.set(true);
    this.vkMeldung.set(null);
    this.svc.setVerkaufspreise(art.id, { entries }).subscribe({
      next: (data) => {
        this.vkSaving.set(false);
        this.vkAusDaten(data);
        this.vk.set({ kind: 'ready', data });
        this.meldung.set({ art: 'erfolg', text: 'Die Verkaufspreise wurden gespeichert.' });
      },
      error: (err) => {
        this.vkSaving.set(false);
        this.vkMeldung.set(fehlerDetail(err) ?? 'Die Verkaufspreise ließen sich nicht speichern.');
      },
    });
  }

  /** Zwei Geldbeträge (Punkt-Strings) auf Cent-Ebene vergleichen. */
  private gleicheBetraege(a: string, b: string): boolean {
    return Number(a).toFixed(2) === Number(b).toFixed(2);
  }

  /** Kurzbeschreibung der Formel einer Gruppe (Hero-Stil „(+ 10,00 %)"). */
  gruppenFormel(g: {
    calc_basis: string;
    operator: string;
    percent_change: string | null;
    amount_change: string | null;
  }): string {
    const vorzeichen = g.operator === 'ABSCHLAG' ? '−' : '+';
    const wert =
      g.percent_change != null
        ? `${this.zahl(g.percent_change)} %`
        : g.amount_change != null
          ? this.euro(g.amount_change)
          : '';
    if (!wert) return this.basisLabel(g.calc_basis);
    return `${this.basisLabel(g.calc_basis)} (${vorzeichen} ${wert})`;
  }

  // ---- Darstellungshelfer -------------------------------------------------
  euro(amount: string | null): string {
    if (amount === null) return '—';
    return new Intl.NumberFormat('de-DE', {
      style: 'currency',
      currency: 'EUR',
    }).format(Number(amount));
  }

  zahl(wert: string | null): string {
    if (wert === null) return '—';
    return new Intl.NumberFormat('de-DE', { maximumFractionDigits: 3 }).format(Number(wert));
  }

  basisLabel(b: string | null): string {
    if (b === 'EK') return 'Einkaufspreis';
    if (b === 'LISTENPREIS') return 'Listenpreis';
    return '—';
  }

  taxCodeLabel(code: string | null): string {
    if (!code) return '—';
    return TAX_CODE_LABEL[code] ?? code;
  }

  priceUnitText(pu: number | null): string {
    const n = pu ?? 1;
    return n === 1 ? 'je 1 Einheit' : `je ${new Intl.NumberFormat('de-DE').format(n)} Einheiten`;
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

  statusLabel(s: StammStatus): string {
    return s === 'AKTIV' ? 'Aktiv' : 'Inaktiv';
  }
  statusClass(s: StammStatus): string {
    return s === 'AKTIV' ? 'stamp--positive' : '';
  }
}
