import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { map } from 'rxjs';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
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
import { ArtikelService } from '../../core/artikel.service';
import { AuthService } from '../../core/auth.service';
import {
  AssemblyComponent,
  AssemblyDetail,
  ComponentIn,
  StammStatus,
} from '../../core/artikel.model';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: AssemblyDetail }
  | VerbotenState
  | { kind: 'error' };

type Meldung = { art: 'erfolg' | 'fehler'; text: string };

@Component({
  selector: 'app-leistung-detail',
  imports: [Mappe, RouterLink, KeinZugriff, ReactiveFormsModule, Dialog, Feld, ReferenzWahl],
  templateUrl: './leistung-detail.html',
  styleUrl: './leistung-detail.scss',
})
export class LeistungDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(ArtikelService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  protected readonly tab = signal('stueckliste');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  protected readonly tabs: MappeTab[] = [
    { id: 'stueckliste', label: 'Stückliste' },
    { id: 'stammdaten', label: 'Stammdaten' },
  ];

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  // --- Rechte (nur UI-Sichtbarkeit; der Server setzt sie durch) -----------
  protected readonly darfAendern = computed(() => this.auth.darf('pricing', 'AENDERN'));

  // --- Position anhängen (Dialog) -----------------------------------------
  protected readonly meldung = signal<Meldung | null>(null);
  protected readonly posOffen = signal(false);
  protected readonly posLaedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);
  /** Lohngruppen als Auswahl (Lohn-Position); leer bis geladen. */
  protected readonly lohngruppen = signal<FeldOption[]>([]);
  protected readonly lohngruppenGeladen = signal(false);
  protected readonly posArten: FeldOption[] = [
    { wert: 'MATERIAL', label: 'Material (Artikel)' },
    { wert: 'LOHN', label: 'Lohn (Lohngruppe)' },
  ];
  protected readonly posForm = this.fb.group({
    kind: this.fb.control<'MATERIAL' | 'LOHN'>('MATERIAL', { nonNullable: true }),
    article_id: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    quantity: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, dezimalValidator],
    }),
    wage_group_id: this.fb.control('', { nonNullable: true }),
    minutes: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
    note: this.fb.control('', { nonNullable: true }),
  });

  /** Artikelsuche für Material-Positionen. */
  protected readonly artikelSuche: RefSuche = (q) =>
    this.svc.listArticles({ page: 1, page_size: 20, q }).pipe(
      map((p) =>
        p.items.map((a) => ({ id: a.id, label: a.description, sub: a.article_number })),
      ),
    );

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.tab.set('stueckliste');
      if (!id) {
        this.state.set({ kind: 'error' });
        return;
      }
      this.load(id);
    });

    // Positionsart umschalten: Pflicht-Validatoren wandern auf die passenden
    // Felder (Material: Artikel + Menge; Lohn: Lohngruppe + Minuten).
    this.posForm.controls.kind.valueChanges
      .pipe(takeUntilDestroyed())
      .subscribe((k) => this.posValidatoren(k));
  }

  retry(): void {
    const id = this.route.snapshot.paramMap.get('id');
    if (id) this.load(id);
  }

  private load(id: string): void {
    const rid = ++this.reqId;
    this.state.set({ kind: 'loading' });
    this.svc.getAssembly(id).subscribe({
      next: (data) => {
        if (rid === this.reqId) this.state.set({ kind: 'ready', data });
      },
      error: (err) => {
        if (rid === this.reqId) this.state.set(fehlerState(err));
      },
    });
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  // ---- Position anhängen --------------------------------------------------
  private posValidatoren(kind: 'MATERIAL' | 'LOHN'): void {
    const article = this.posForm.controls.article_id;
    const quantity = this.posForm.controls.quantity;
    const wage = this.posForm.controls.wage_group_id;
    const minutes = this.posForm.controls.minutes;
    if (kind === 'MATERIAL') {
      article.setValidators([Validators.required]);
      quantity.setValidators([Validators.required, dezimalValidator]);
      wage.clearValidators();
      wage.setValue('');
      minutes.setValidators([dezimalValidator]);
      minutes.setValue('');
    } else {
      article.clearValidators();
      article.setValue('');
      quantity.setValidators([dezimalValidator]);
      quantity.setValue('');
      wage.setValidators([Validators.required]);
      minutes.setValidators([Validators.required, dezimalValidator]);
    }
    article.updateValueAndValidity();
    quantity.updateValueAndValidity();
    wage.updateValueAndValidity();
    minutes.updateValueAndValidity();
  }

  posOeffnen(): void {
    this.posForm.reset({
      kind: 'MATERIAL',
      article_id: '',
      quantity: '',
      wage_group_id: '',
      minutes: '',
      note: '',
    });
    this.posValidatoren('MATERIAL');
    this.formularMeldung.set(null);
    this.posOffen.set(true);
    if (!this.lohngruppenGeladen()) this.ladeLohngruppen();
  }

  posSchliessen(): void {
    if (!this.posLaedt()) this.posOffen.set(false);
  }

  private ladeLohngruppen(): void {
    this.lohngruppenGeladen.set(true);
    this.svc.listWageGroups().subscribe({
      next: (wg) =>
        this.lohngruppen.set(wg.map((g) => ({ wert: g.id, label: g.name }))),
      error: () => this.lohngruppen.set([]),
    });
  }

  posAbsenden(): void {
    const a = this.daten();
    if (!a || this.posLaedt()) return;
    serverFehlerZuruecksetzen(this.posForm);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.posForm);
    if (this.posForm.invalid) return;

    const v = this.posForm.getRawValue();
    const component: ComponentIn =
      v.kind === 'MATERIAL'
        ? {
            article_id: v.article_id,
            quantity: deZuApiDezimal(v.quantity),
            note: v.note.trim() || null,
          }
        : {
            wage_group_id: v.wage_group_id,
            minutes: deZuApiDezimal(v.minutes),
            note: v.note.trim() || null,
          };

    this.posLaedt.set(true);
    this.svc.addAssemblyComponents(a.id, { components: [component] }).subscribe({
      next: (detail) => {
        this.posLaedt.set(false);
        this.posOffen.set(false);
        this.meldung.set({ art: 'erfolg', text: 'Position wurde angehängt.' });
        this.state.set({ kind: 'ready', data: detail });
      },
      error: (err) => {
        this.posLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.posForm).formular);
      },
    });
  }

  statusLabel(s: StammStatus): string {
    return s === 'AKTIV' ? 'Aktiv' : 'Inaktiv';
  }
  statusClass(s: StammStatus): string {
    return s === 'AKTIV' ? 'stamp--positive' : '';
  }

  menge(c: AssemblyComponent): string {
    if (c.kind === 'MATERIAL' && c.quantity !== null) {
      const q = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 3 }).format(
        Number(c.quantity),
      );
      return c.unit ? `${q} ${c.unit}` : q;
    }
    if (c.kind === 'LOHN' && c.minutes !== null) {
      const m = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 2 }).format(
        Number(c.minutes),
      );
      return `${m} min`;
    }
    return '';
  }

  kindLabel(k: 'MATERIAL' | 'LOHN'): string {
    return k === 'MATERIAL' ? 'Material' : 'Lohn';
  }
}
