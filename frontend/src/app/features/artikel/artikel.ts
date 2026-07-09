import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Subject, debounceTime, distinctUntilChanged } from 'rxjs';
import { ArtikelService } from '../../core/artikel.service';
import { AuthService } from '../../core/auth.service';
import {
  ArticleIn,
  ArticleLineType,
  ArticlePage,
  AssemblyIn,
  AssemblyPage,
} from '../../core/artikel.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import { deZuApiDezimal, dezimalValidator } from '../../shared/formular/dezimal';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';

type Modus = 'artikel' | 'leistungen';
type Meldung = { art: 'erfolg' | 'fehler'; text: string };

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; modus: 'artikel'; data: ArticlePage }
  | { kind: 'ready'; modus: 'leistungen'; data: AssemblyPage }
  | VerbotenState
  | { kind: 'error' };

@Component({
  selector: 'app-artikel',
  imports: [RouterLink, ReactiveFormsModule, KeinZugriff, Dialog, Feld],
  templateUrl: './artikel.html',
  styleUrl: './artikel.scss',
})
export class Artikel {
  private readonly svc = inject(ArtikelService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

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

  // --- Rechte (nur UI-Sichtbarkeit; der Server setzt sie durch) -----------
  protected readonly darfAnlegen = computed(() => this.auth.darf('pricing', 'ANLEGEN'));

  // --- Meldung + Dialoge ---------------------------------------------------
  protected readonly meldung = signal<Meldung | null>(null);
  protected readonly neuLaedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);

  protected readonly artikelOffen = signal(false);
  protected readonly artikelForm = this.fb.group({
    article_number: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    description: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    unit: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    line_type: this.fb.control<ArticleLineType>('MATERIAL', { nonNullable: true }),
    list_price: this.fb.control('', {
      nonNullable: true,
      validators: [dezimalValidator],
    }),
    long_description: this.fb.control('', { nonNullable: true }),
    manufacturer_name: this.fb.control('', { nonNullable: true }),
    product_group: this.fb.control('', { nonNullable: true }),
  });

  protected readonly leistungOffen = signal(false);
  protected readonly leistungForm = this.fb.group({
    assembly_number: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
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

  constructor() {
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

  // ---- Artikel anlegen ----------------------------------------------------
  artikelOeffnen(): void {
    this.artikelForm.reset({
      article_number: '',
      description: '',
      unit: '',
      line_type: 'MATERIAL',
      list_price: '',
      long_description: '',
      manufacturer_name: '',
      product_group: '',
    });
    this.formularMeldung.set(null);
    this.artikelOffen.set(true);
  }

  artikelSchliessen(): void {
    if (this.neuLaedt()) return;
    this.artikelOffen.set(false);
  }

  artikelAbsenden(): void {
    if (this.neuLaedt()) return;
    serverFehlerZuruecksetzen(this.artikelForm);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.artikelForm);
    if (this.artikelForm.invalid) return;

    const v = this.artikelForm.getRawValue();
    const preis = deZuApiDezimal(v.list_price);
    const payload: ArticleIn = {
      article_number: v.article_number.trim(),
      description: v.description.trim(),
      unit: v.unit.trim(),
      line_type: v.line_type,
      list_price: preis || null,
      long_description: v.long_description.trim() || null,
      manufacturer_name: v.manufacturer_name.trim() || null,
      product_group: v.product_group.trim() || null,
    };

    this.neuLaedt.set(true);
    this.svc.createArticle(payload).subscribe({
      next: (art) => {
        this.neuLaedt.set(false);
        this.artikelOffen.set(false);
        this.meldung.set({
          art: 'erfolg',
          text: `Artikel ${art.article_number} „${art.description}“ wurde angelegt.`,
        });
        this.modus.set('artikel');
        this.query.set('');
        this.page.set(1);
        this.fetch();
      },
      error: (err) => {
        this.neuLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.artikelForm).formular);
      },
    });
  }

  // ---- Leistung anlegen ---------------------------------------------------
  leistungOeffnen(): void {
    this.leistungForm.reset({
      assembly_number: '',
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
      assembly_number: v.assembly_number.trim(),
      name: v.name.trim(),
      unit: v.unit.trim(),
      description: v.description.trim() || null,
    };

    this.neuLaedt.set(true);
    this.svc.createAssembly(payload).subscribe({
      next: (a) => {
        this.neuLaedt.set(false);
        this.leistungOffen.set(false);
        this.meldung.set({
          art: 'erfolg',
          text: `Leistung ${a.assembly_number} „${a.name}“ wurde angelegt (ohne Stückliste).`,
        });
        this.modus.set('leistungen');
        this.query.set('');
        this.page.set(1);
        this.fetch();
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
