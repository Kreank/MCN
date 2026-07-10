import { Component, computed, effect, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';
import { Dialog } from '../../shared/dialog/dialog';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { Feld } from '../../shared/formular/feld';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import {
  apiZuDeDezimal,
  deZuApiDezimal,
  dezimalValidator,
} from '../../shared/formular/dezimal';
import { gtinValidator } from '../../shared/formular/gtin';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';
import { ArtikelService } from '../../core/artikel.service';
import { AuthService } from '../../core/auth.service';
import {
  ArticleDetail,
  ArticleKalkulation,
  ArticleLineType,
  ArticleSalePriceIn,
  ArticleUpdateIn,
  HistorieEintrag,
  SalePriceGroup,
  StammStatus,
} from '../../core/artikel.model';
import { FeldOption } from '../../shared/formular/feld';

type KalkState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; data: ArticleKalkulation }
  | VerbotenState
  | { kind: 'error' };

type HistorieState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; data: HistorieEintrag[] }
  | VerbotenState
  | { kind: 'error' };

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: ArticleDetail }
  | VerbotenState
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
  product_group: 'Warengruppe',
  status: 'Status',
};

// Werte aus audit.audit_entry.action. Der Trigger schreibt nur ROW_UPDATE und
// ROW_DELETE — ein Anlegen wird nicht auditiert.
const AKTION_LABEL: Record<string, string> = {
  ROW_UPDATE: 'Geändert',
  ROW_DELETE: 'Gelöscht',
};

@Component({
  selector: 'app-artikel-detail',
  imports: [Mappe, RouterLink, ReactiveFormsModule, KeinZugriff, Dialog, Bestaetigung, Feld],
  templateUrl: './artikel-detail.html',
  styleUrl: './artikel-detail.scss',
})
export class ArtikelDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(ArtikelService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  protected readonly tab = signal('informationen');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  protected readonly tabs: MappeTab[] = [
    { id: 'informationen', label: 'Informationen' },
    { id: 'kalkulation', label: 'Kalkulation' },
    { id: 'historie', label: 'Historie' },
  ];

  protected readonly lineTypeOptionen = LINE_TYPE_OPTIONEN;

  protected readonly kalk = signal<KalkState>({ kind: 'idle' });
  private kalkReqId = 0;

  protected readonly historie = signal<HistorieState>({ kind: 'idle' });
  private historieReqId = 0;

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  // --- Rechte (nur UI-Sichtbarkeit; der Server setzt sie durch) -----------
  protected readonly darfAendern = computed(() => this.auth.darf('pricing', 'AENDERN'));

  protected readonly meldung = signal<Meldung | null>(null);

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
    manufacturer_name: this.fb.control('', { nonNullable: true }),
    manufacturer_number: this.fb.control('', { nonNullable: true }),
    gtin: this.fb.control('', { nonNullable: true, validators: [gtinValidator] }),
    list_price: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
    long_description: this.fb.control('', { nonNullable: true }),
  });

  // --- Status ändern (Deaktivieren hinter Bestätigung) ---------------------
  protected readonly statusOffen = signal(false);
  protected readonly statusLaedt = signal(false);

  // --- VK-Preis setzen (Dialog): Festpreis ODER Formelgruppe (XOR) ---------
  protected readonly vkOffen = signal(false);
  protected readonly vkLaedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);
  /** VK-Kalkulationsgruppen als Auswahl (Formel-Modus); leer bis geladen. */
  protected readonly preisgruppen = signal<FeldOption[]>([]);
  protected readonly preisgruppenGeladen = signal(false);
  protected readonly preisArten: FeldOption[] = [
    { wert: 'festpreis', label: 'Festpreis' },
    { wert: 'formel', label: 'Kalkulationsgruppe (Formel)' },
  ];
  protected readonly vkForm = this.fb.group({
    label: this.fb.control('Standard', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    modus: this.fb.control<'festpreis' | 'formel'>('festpreis', { nonNullable: true }),
    fixed_price: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, dezimalValidator],
    }),
    sale_price_group_id: this.fb.control('', { nonNullable: true }),
    is_standard: this.fb.control(true, { nonNullable: true }),
  });

  constructor() {
    // Modus-Umschaltung: die Pflicht-Validatoren wandern auf das aktive Feld,
    // damit immer genau eines von Festpreis / Formelgruppe gefüllt sein muss
    // (der Server erzwingt das XOR ohnehin).
    this.vkForm.controls.modus.valueChanges
      .pipe(takeUntilDestroyed())
      .subscribe((m) => this.vkValidatoren(m));

    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.tab.set('informationen');
      this.kalk.set({ kind: 'idle' });
      this.historie.set({ kind: 'idle' });
      this.meldung.set(null);
      if (!id) {
        this.state.set({ kind: 'error' });
        return;
      }
      this.load(id);
    });

    // Kalkulation erst beim Öffnen des Tabs nachladen (einmal je Artikel).
    effect(() => {
      const art = this.daten();
      if (this.tab() === 'kalkulation' && art && this.kalk().kind === 'idle') {
        this.loadKalk(art.id);
      }
    });

    // Historie ebenso lazy nachladen (und nach Änderungen erneut).
    effect(() => {
      const art = this.daten();
      if (this.tab() === 'historie' && art && this.historie().kind === 'idle') {
        this.loadHistorie(art.id);
      }
    });
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

  retryKalk(): void {
    const art = this.daten();
    if (art) this.loadKalk(art.id);
  }

  retryHistorie(): void {
    const art = this.daten();
    if (art) this.loadHistorie(art.id);
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  /**
   * Nach einer Änderung (Bearbeiten/Status): den frisch geladenen Artikel
   * übernehmen und die abgeleiteten Tabs (Kalkulation/Historie) zum Nachladen
   * verwerfen — die effects laden sie beim nächsten Öffnen neu.
   */
  private nachAenderung(data: ArticleDetail): void {
    this.state.set({ kind: 'ready', data });
    this.kalk.set({ kind: 'idle' });
    this.historie.set({ kind: 'idle' });
    if (this.tab() === 'kalkulation') this.loadKalk(data.id);
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
      manufacturer_name: art.manufacturer_name ?? '',
      manufacturer_number: art.manufacturer_number ?? '',
      gtin: art.gtin ?? '',
      // list_price hat vier Nachkommastellen; unverändert anzeigen (kein Runden).
      list_price: art.list_price != null ? apiZuDeDezimal(art.list_price) : '',
      long_description: art.long_description ?? '',
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
    const payload: ArticleUpdateIn = {
      article_number: v.article_number.trim(),
      description: v.description.trim(),
      unit: v.unit.trim(),
      line_type: v.line_type,
      product_group: v.product_group.trim() || null,
      manufacturer_name: v.manufacturer_name.trim() || null,
      manufacturer_number: v.manufacturer_number.trim() || null,
      gtin: v.gtin.trim() || null,
      list_price: deZuApiDezimal(v.list_price) || null,
      long_description: v.long_description.trim() || null,
    };

    this.bearbeitenLaedt.set(true);
    this.svc.updateArticle(art.id, payload).subscribe({
      next: (data) => {
        this.bearbeitenLaedt.set(false);
        this.bearbeitenOffen.set(false);
        this.nachAenderung(data);
        this.meldung.set({ art: 'erfolg', text: 'Die Artikelstammdaten wurden gespeichert.' });
      },
      error: (err) => {
        this.bearbeitenLaedt.set(false);
        this.bearbeitenMeldung.set(apiFehlerZuweisen(err, this.bearbeitenForm).formular);
      },
    });
  }

  // ---- Status ändern ------------------------------------------------------
  /** Deaktivieren blendet den Artikel aus der Suche/dem Angebot aus → Nachfrage. */
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

  /** Reaktivieren ist folgenlos umkehrbar → direkt, ohne Nachfrage. */
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
          text: apiFehlerZuweisen(err, this.bearbeitenForm).formular ?? 'Statuswechsel fehlgeschlagen.',
        });
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

  // ---- VK-Preis setzen ----------------------------------------------------
  private vkValidatoren(modus: 'festpreis' | 'formel'): void {
    const preis = this.vkForm.controls.fixed_price;
    const gruppe = this.vkForm.controls.sale_price_group_id;
    if (modus === 'festpreis') {
      preis.setValidators([Validators.required, dezimalValidator]);
      gruppe.clearValidators();
      gruppe.setValue('');
    } else {
      preis.setValidators([dezimalValidator]);
      preis.setValue('');
      gruppe.setValidators([Validators.required]);
    }
    preis.updateValueAndValidity();
    gruppe.updateValueAndValidity();
  }

  vkOeffnen(): void {
    this.vkForm.reset({
      label: 'Standard',
      modus: 'festpreis',
      fixed_price: '',
      sale_price_group_id: '',
      is_standard: true,
    });
    this.vkValidatoren('festpreis');
    this.formularMeldung.set(null);
    this.vkOffen.set(true);
    if (!this.preisgruppenGeladen()) this.ladePreisgruppen();
  }

  vkSchliessen(): void {
    if (this.vkLaedt()) return;
    this.vkOffen.set(false);
  }

  private ladePreisgruppen(): void {
    this.preisgruppenGeladen.set(true);
    this.svc.listSalePriceGroups().subscribe({
      next: (gruppen) =>
        this.preisgruppen.set(
          gruppen.map((g) => ({ wert: g.id, label: `${g.name} · ${this.gruppenFormel(g)}` })),
        ),
      error: () => this.preisgruppen.set([]),
    });
  }

  /** Kurzbeschreibung der Formel einer VK-Gruppe (nur zur Anzeige). */
  private gruppenFormel(g: SalePriceGroup): string {
    const change =
      g.percent_change !== null
        ? `${Number(g.percent_change)} %`
        : this.euro(g.amount_change);
    return `${this.basisLabel(g.calc_basis)} ${this.operatorLabel(g.operator)} ${change}`;
  }

  vkAbsenden(): void {
    const art = this.daten();
    if (this.vkLaedt() || !art) return;
    serverFehlerZuruecksetzen(this.vkForm);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.vkForm);
    if (this.vkForm.invalid) return;

    const v = this.vkForm.getRawValue();
    const payload: ArticleSalePriceIn =
      v.modus === 'festpreis'
        ? {
            label: v.label.trim() || 'Standard',
            fixed_price: deZuApiDezimal(v.fixed_price),
            is_standard: v.is_standard,
          }
        : {
            label: v.label.trim() || 'Standard',
            sale_price_group_id: v.sale_price_group_id,
            is_standard: v.is_standard,
          };

    this.vkLaedt.set(true);
    this.svc.setSalePrice(art.id, payload).subscribe({
      next: () => {
        this.vkLaedt.set(false);
        this.vkOffen.set(false);
        this.meldung.set({ art: 'erfolg', text: 'Der Verkaufspreis wurde gesetzt.' });
        // Kalkulation neu berechnen lassen.
        this.loadKalk(art.id);
      },
      error: (err) => {
        this.vkLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.vkForm).formular);
      },
    });
  }

  private loadKalk(id: string): void {
    const rid = ++this.kalkReqId;
    this.kalk.set({ kind: 'loading' });
    this.svc.getKalkulation(id).subscribe({
      next: (data) => {
        if (rid === this.kalkReqId) this.kalk.set({ kind: 'ready', data });
      },
      error: (err) => {
        if (rid === this.kalkReqId) this.kalk.set(fehlerState(err));
      },
    });
  }

  euro(amount: string | null): string {
    if (amount === null) return '—';
    return new Intl.NumberFormat('de-DE', {
      style: 'currency',
      currency: 'EUR',
    }).format(Number(amount));
  }

  basisLabel(b: string | null): string {
    if (b === 'EK') return 'Einkaufspreis';
    if (b === 'LISTENPREIS') return 'Listenpreis';
    return '—';
  }
  operatorLabel(o: string | null): string {
    if (o === 'AUFSCHLAG') return 'Aufschlag';
    if (o === 'ABSCHLAG') return 'Abschlag';
    return '';
  }
  formelText(v: {
    kind: string;
    basis_kind: string | null;
    operator: string | null;
    percent_change: string | null;
    amount_change: string | null;
  }): string {
    if (v.kind === 'FESTPREIS') return 'Festpreis';
    const change = v.percent_change !== null ? `${Number(v.percent_change)} %` : this.euro(v.amount_change);
    return `${this.basisLabel(v.basis_kind)} ${this.operatorLabel(v.operator)} ${change}`;
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
