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
  StammStatus,
} from '../../core/artikel.model';

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

  // --- VK-Festpreis setzen (Dialog) ---------------------------------------
  protected readonly meldung = signal<Meldung | null>(null);
  protected readonly vkOffen = signal(false);
  protected readonly vkLaedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);
  protected readonly vkForm = this.fb.group({
    label: this.fb.control('Standard', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    fixed_price: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, dezimalValidator],
    }),
    is_standard: this.fb.control(true, { nonNullable: true }),
  });

  constructor() {
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

  // ---- VK-Festpreis setzen ------------------------------------------------
  vkOeffnen(): void {
    this.vkForm.reset({ label: 'Standard', fixed_price: '', is_standard: true });
    this.formularMeldung.set(null);
    this.vkOffen.set(true);
  }

  vkSchliessen(): void {
    if (this.vkLaedt()) return;
    this.vkOffen.set(false);
  }

  vkAbsenden(): void {
    const art = this.daten();
    if (this.vkLaedt() || !art) return;
    serverFehlerZuruecksetzen(this.vkForm);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.vkForm);
    if (this.vkForm.invalid) return;

    const v = this.vkForm.getRawValue();
    const payload: ArticleSalePriceIn = {
      label: v.label.trim() || 'Standard',
      fixed_price: deZuApiDezimal(v.fixed_price),
      is_standard: v.is_standard,
    };

    this.vkLaedt.set(true);
    this.svc.setSalePrice(art.id, payload).subscribe({
      next: () => {
        this.vkLaedt.set(false);
        this.vkOffen.set(false);
        this.meldung.set({ art: 'erfolg', text: 'VK-Festpreis wurde gesetzt.' });
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
