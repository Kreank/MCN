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
  AssemblyKalkulation,
  ComponentIn,
  StammStatus,
} from '../../core/artikel.model';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: AssemblyDetail }
  | VerbotenState
  | { kind: 'error' };

type KalkState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; data: AssemblyKalkulation }
  | { kind: 'error' };

type Meldung = { art: 'erfolg' | 'fehler'; text: string };

/** Position, die der Dialog gerade bearbeitet — `null` heisst „neue Position". */
type PosZiel = { position: number } | null;

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
    { id: 'kalkulation', label: 'Kalkulation' },
    { id: 'stammdaten', label: 'Stammdaten' },
  ];

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  // --- Rechte (nur UI-Sichtbarkeit; der Server setzt sie durch) -----------
  protected readonly darfAendern = computed(() => this.auth.darf('pricing', 'AENDERN'));

  protected readonly meldung = signal<Meldung | null>(null);
  /** Eine Aktion auf der Stückliste läuft — sperrt die übrigen Knöpfe. */
  protected readonly listeLaedt = signal(false);

  // --- Position anlegen/bearbeiten (Dialog) --------------------------------
  protected readonly posOffen = signal(false);
  protected readonly posLaedt = signal(false);
  protected readonly posZiel = signal<PosZiel>(null);
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

  protected readonly posDialogTitel = computed(() =>
    this.posZiel() === null ? 'Position anhängen' : 'Position bearbeiten',
  );

  /** Artikelsuche für Material-Positionen. */
  protected readonly artikelSuche: RefSuche = (q) =>
    this.svc.listArticles({ page: 1, page_size: 20, q }).pipe(
      map((p) =>
        p.items.map((a) => ({ id: a.id, label: a.description, sub: a.article_number })),
      ),
    );

  // --- Stammdaten bearbeiten ----------------------------------------------
  protected readonly bearbeitenOffen = signal(false);
  protected readonly bearbeitenLaedt = signal(false);
  protected readonly bearbeitenForm = this.fb.group({
    assembly_number: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    name: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    unit: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    internal_name: this.fb.control('', { nonNullable: true }),
    description: this.fb.control('', { nonNullable: true }),
  });

  // --- Status --------------------------------------------------------------
  protected readonly statusLaedt = signal(false);

  // --- Kalkulation ---------------------------------------------------------
  protected readonly kalk = signal<KalkState>({ kind: 'idle' });
  private kalkReqId = 0;

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.tab.set('stueckliste');
      this.kalk.set({ kind: 'idle' });
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

  /** Reiterwechsel: die Kalkulation wird erst geladen, wenn sie sichtbar wird. */
  tabWechsel(id: string): void {
    this.tab.set(id);
    if (id === 'kalkulation' && this.kalk().kind === 'idle') this.kalkLaden();
  }

  // ---- Kalkulation --------------------------------------------------------
  kalkLaden(): void {
    const a = this.daten();
    if (!a) return;
    const rid = ++this.kalkReqId;
    this.kalk.set({ kind: 'loading' });
    this.svc.assemblyKalkulation(a.id).subscribe({
      next: (data) => {
        if (rid === this.kalkReqId) this.kalk.set({ kind: 'ready', data });
      },
      error: () => {
        if (rid === this.kalkReqId) this.kalk.set({ kind: 'error' });
      },
    });
  }

  /** Nach jeder Änderung an der Stückliste: ein alter Preis wäre schlicht falsch. */
  private kalkVerwerfen(): void {
    this.kalkReqId++;
    this.kalk.set({ kind: 'idle' });
    if (this.tab() === 'kalkulation') this.kalkLaden();
  }

  // ---- Stückliste: eine Liste, drei Aktionen ------------------------------
  /** Die geladene Stückliste als Eingabeliste — Ausgangspunkt jeder Änderung. */
  private aktuelleListe(): ComponentIn[] {
    const a = this.daten();
    if (!a) return [];
    return a.components.map((c) => this.zuEingabe(c));
  }

  private zuEingabe(c: AssemblyComponent): ComponentIn {
    return c.kind === 'MATERIAL'
      ? { article_id: c.article_id, quantity: c.quantity, note: c.note }
      : { wage_group_id: c.wage_group_id, minutes: c.minutes, note: c.note };
  }

  /** Schickt die ganze Liste; die Positionsnummern folgen der Reihenfolge. */
  private listeSpeichern(components: ComponentIn[], erfolg: string): void {
    const a = this.daten();
    if (!a) return;
    this.listeLaedt.set(true);
    this.svc.replaceAssemblyComponents(a.id, { components }).subscribe({
      next: (detail) => {
        this.listeLaedt.set(false);
        this.state.set({ kind: 'ready', data: detail });
        this.meldung.set({ art: 'erfolg', text: erfolg });
        this.kalkVerwerfen();
      },
      error: () => {
        this.listeLaedt.set(false);
        this.meldung.set({
          art: 'fehler',
          text: 'Die Stückliste konnte nicht gespeichert werden.',
        });
      },
    });
  }

  posEntfernen(c: AssemblyComponent): void {
    if (this.listeLaedt()) return;
    const liste = this.aktuelleListe().filter((_, i) => i !== c.position - 1);
    this.listeSpeichern(liste, `Position ${c.position} wurde entfernt.`);
  }

  posVerschieben(c: AssemblyComponent, richtung: -1 | 1): void {
    if (this.listeLaedt()) return;
    const liste = this.aktuelleListe();
    const von = c.position - 1;
    const nach = von + richtung;
    if (nach < 0 || nach >= liste.length) return;
    [liste[von], liste[nach]] = [liste[nach], liste[von]];
    this.listeSpeichern(
      liste,
      `Position wurde nach ${richtung < 0 ? 'oben' : 'unten'} verschoben.`,
    );
  }

  /** Erste/letzte Zeile: die Pfeile dort führen ins Leere. */
  istErste(c: AssemblyComponent): boolean {
    return c.position <= 1;
  }
  istLetzte(c: AssemblyComponent): boolean {
    return c.position >= (this.daten()?.components.length ?? 0);
  }

  // ---- Position anlegen/bearbeiten ---------------------------------------
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
    this.posZiel.set(null);
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

  posBearbeiten(c: AssemblyComponent): void {
    if (this.listeLaedt()) return;
    this.posZiel.set({ position: c.position });
    // reset() loest den valueChanges-Abonnenten auf `kind` aus, der die Felder
    // der jeweils ANDEREN Art leert. Deshalb steht posValidatoren() darunter
    // noch einmal — es setzt die Pflichtfelder passend zur geladenen Art.
    this.posForm.reset({
      kind: c.kind,
      article_id: c.article_id ?? '',
      quantity: c.quantity ?? '',
      wage_group_id: c.wage_group_id ?? '',
      minutes: c.minutes ?? '',
      note: c.note ?? '',
    });
    this.posValidatoren(c.kind);
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
      next: (wg) => this.lohngruppen.set(wg.map((g) => ({ wert: g.id, label: g.name }))),
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

    const ziel = this.posZiel();
    const liste = this.aktuelleListe();
    if (ziel === null) liste.push(component);
    else liste[ziel.position - 1] = component;

    this.posLaedt.set(true);
    this.svc.replaceAssemblyComponents(a.id, { components: liste }).subscribe({
      next: (detail) => {
        this.posLaedt.set(false);
        this.posOffen.set(false);
        this.state.set({ kind: 'ready', data: detail });
        this.meldung.set({
          art: 'erfolg',
          text: ziel === null ? 'Position wurde angehängt.' : 'Position wurde geändert.',
        });
        this.kalkVerwerfen();
      },
      error: (err) => {
        this.posLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.posForm).formular);
      },
    });
  }

  // ---- Stammdaten bearbeiten ---------------------------------------------
  bearbeitenOeffnen(): void {
    const a = this.daten();
    if (!a) return;
    serverFehlerZuruecksetzen(this.bearbeitenForm);
    this.formularMeldung.set(null);
    this.bearbeitenForm.reset({
      assembly_number: a.assembly_number,
      name: a.name,
      unit: a.unit,
      internal_name: a.internal_name ?? '',
      description: a.description ?? '',
    });
    this.bearbeitenOffen.set(true);
  }

  bearbeitenSchliessen(): void {
    if (!this.bearbeitenLaedt()) this.bearbeitenOffen.set(false);
  }

  bearbeitenAbsenden(): void {
    const a = this.daten();
    if (!a || this.bearbeitenLaedt()) return;
    serverFehlerZuruecksetzen(this.bearbeitenForm);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.bearbeitenForm);
    if (this.bearbeitenForm.invalid) return;

    const v = this.bearbeitenForm.getRawValue();
    this.bearbeitenLaedt.set(true);
    this.svc
      .updateAssembly(a.id, {
        assembly_number: v.assembly_number.trim(),
        name: v.name.trim(),
        unit: v.unit.trim(),
        internal_name: v.internal_name.trim() || null,
        description: v.description.trim() || null,
      })
      .subscribe({
        next: (detail) => {
          this.bearbeitenLaedt.set(false);
          this.bearbeitenOffen.set(false);
          this.state.set({ kind: 'ready', data: detail });
          this.meldung.set({ art: 'erfolg', text: 'Die Stammdaten wurden geändert.' });
        },
        error: (err) => {
          this.bearbeitenLaedt.set(false);
          this.formularMeldung.set(apiFehlerZuweisen(err, this.bearbeitenForm).formular);
        },
      });
  }

  // ---- Status -------------------------------------------------------------
  statusUmschalten(): void {
    const a = this.daten();
    if (!a || this.statusLaedt()) return;
    const ziel: StammStatus = a.status === 'AKTIV' ? 'INAKTIV' : 'AKTIV';
    this.statusLaedt.set(true);
    this.svc.setAssemblyStatus(a.id, ziel).subscribe({
      next: (detail) => {
        this.statusLaedt.set(false);
        this.state.set({ kind: 'ready', data: detail });
        this.meldung.set({
          art: 'erfolg',
          text:
            ziel === 'INAKTIV'
              ? 'Die Leistung ist deaktiviert und steht nicht mehr zur Auswahl. Bestehende Belege bleiben unberührt.'
              : 'Die Leistung ist wieder aktiv.',
        });
      },
      error: () => {
        this.statusLaedt.set(false);
        this.meldung.set({
          art: 'fehler',
          text: 'Der Status konnte nicht geändert werden.',
        });
      },
    });
  }

  // ---- Anzeige ------------------------------------------------------------
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

  /** Betrag als Euro; `null` heisst „unbekannt" und wird nicht als 0 gezeigt. */
  euro(betrag: string | null): string {
    if (betrag === null) return '—';
    return new Intl.NumberFormat('de-DE', {
      style: 'currency',
      currency: 'EUR',
    }).format(Number(betrag));
  }

  prozent(wert: string | null): string {
    if (wert === null) return '—';
    return `${new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1 }).format(
      Number(wert),
    )} %`;
  }

  /** Minuten als „1 h 30 min" — in Stundensätzen denkt niemand in 90 Minuten. */
  dauer(minuten: string): string {
    const m = Number(minuten);
    if (!m) return '—';
    const std = Math.floor(m / 60);
    const rest = Math.round((m % 60) * 100) / 100;
    if (!std) return `${rest} min`;
    return rest ? `${std} h ${rest} min` : `${std} h`;
  }
}
