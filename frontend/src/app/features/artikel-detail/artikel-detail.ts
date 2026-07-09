import { Component, computed, effect, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld } from '../../shared/formular/feld';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import { deZuApiDezimal, dezimalValidator } from '../../shared/formular/dezimal';
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

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: ArticleDetail }
  | VerbotenState
  | { kind: 'error' };

type Meldung = { art: 'erfolg' | 'fehler'; text: string };

@Component({
  selector: 'app-artikel-detail',
  imports: [Mappe, RouterLink, ReactiveFormsModule, KeinZugriff, Dialog, Feld],
  templateUrl: './artikel-detail.html',
  styleUrl: './artikel-detail.scss',
})
export class ArtikelDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(ArtikelService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  protected readonly tab = signal('stammdaten');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  protected readonly tabs: MappeTab[] = [
    { id: 'stammdaten', label: 'Stammdaten' },
    { id: 'preis', label: 'Preis' },
    { id: 'kalkulation', label: 'Kalkulation' },
  ];

  protected readonly kalk = signal<KalkState>({ kind: 'idle' });
  private kalkReqId = 0;

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  // --- Rechte (nur UI-Sichtbarkeit; der Server setzt sie durch) -----------
  protected readonly darfAendern = computed(() => this.auth.darf('pricing', 'AENDERN'));

  // --- VK-Preis setzen (Dialog): Festpreis ODER Formelgruppe (XOR) ---------
  protected readonly meldung = signal<Meldung | null>(null);
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
      this.tab.set('stammdaten');
      this.kalk.set({ kind: 'idle' });
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

  meldungSchliessen(): void {
    this.meldung.set(null);
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
