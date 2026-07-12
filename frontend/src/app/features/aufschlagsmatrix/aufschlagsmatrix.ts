import { Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { map } from 'rxjs';
import { AufschlagsmatrixService } from '../../core/aufschlagsmatrix.service';
import { AuthService } from '../../core/auth.service';
import { PartyService } from '../../core/party.service';
import {
  CalcBasis,
  MarkupRule,
  MarkupTierIn,
  MassenpflegeErgebnis,
  Warengruppe,
} from '../../core/aufschlagsmatrix.model';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { ReferenzWahl, RefSuche } from '../../shared/formular/referenz-wahl';
import {
  apiZuDeAnzeige,
  apiZuDeEingabe,
  deZuApiDezimal,
  dezimalValidator,
} from '../../shared/formular/dezimal';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: MarkupRule[] }
  | VerbotenState
  | { kind: 'error' };

/** Geltungsbereich im Formular — bestimmt, welche Selektoren sichtbar sind. */
type ScopeWahl = 'STANDARD' | 'WARENGRUPPE' | 'LIEFERANT' | 'WARENGRUPPE_LIEFERANT';

interface StaffelZeile {
  min_quantity: string;
  markup_percent: string;
}

/**
 * EK→VK-Aufschlagsmatrix: Regeln pflegen und die Verkaufspreise einer
 * Warengruppe in einem bestätigten Vorgang neu rechnen.
 *
 * Die Matrix steht UNTER der Artikelkalkulation: ein von Hand gesetzter Festpreis
 * oder eine am Artikel zugewiesene VK-Gruppe schlägt jede Regel. Gerechnet wird
 * ausschließlich auf dem Server — diese Seite zeigt nur, was er sagt.
 */
@Component({
  selector: 'app-aufschlagsmatrix',
  imports: [ReactiveFormsModule, Feld, ReferenzWahl, Bestaetigung, KeinZugriff],
  templateUrl: './aufschlagsmatrix.html',
  styleUrl: './aufschlagsmatrix.scss',
})
export class Aufschlagsmatrix {
  private readonly svc = inject(AufschlagsmatrixService);
  private readonly partySvc = inject(PartyService);
  private readonly fb = inject(FormBuilder);
  private readonly auth = inject(AuthService);

  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly darfAendern = computed(() => this.auth.darf('pricing', 'AENDERN'));
  protected readonly darfAnlegen = computed(() => this.auth.darf('pricing', 'ANLEGEN'));
  protected readonly laedt = signal(false);
  protected readonly meldung = signal<string | null>(null);
  protected readonly warengruppen = signal<Warengruppe[]>([]);

  /** 'neu' beim Anlegen, Regel-id beim Bearbeiten, sonst null. */
  protected readonly modus = signal<string | null>(null);
  protected readonly staffel = signal<StaffelZeile[]>([]);

  protected readonly basisOptionen: FeldOption[] = [
    { wert: 'EK', label: 'Einkaufspreis (EK)' },
    { wert: 'LISTENPREIS', label: 'Listenpreis' },
  ];
  protected readonly scopeOptionen: FeldOption[] = [
    { wert: 'STANDARD', label: 'Standardregel (greift, wenn keine andere passt)' },
    { wert: 'WARENGRUPPE', label: 'Warengruppe' },
    { wert: 'LIEFERANT', label: 'Lieferant' },
    { wert: 'WARENGRUPPE_LIEFERANT', label: 'Warengruppe + Lieferant' },
  ];

  protected readonly lieferantSuche: RefSuche = (q) =>
    this.partySvc
      .list({ page: 1, page_size: 20, q })
      .pipe(map((p) => p.items.map((o) => ({ id: o.id, label: o.display_name }))));

  protected readonly form = this.fb.group({
    name: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    scope: this.fb.control<ScopeWahl>('WARENGRUPPE', { nonNullable: true }),
    product_group: this.fb.control('', { nonNullable: true }),
    supplier_party_id: this.fb.control('', { nonNullable: true }),
    calc_basis: this.fb.control<CalcBasis>('EK', { nonNullable: true }),
    markup_percent: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, dezimalValidator],
    }),
    min_margin_percent: this.fb.control('', {
      nonNullable: true,
      validators: [dezimalValidator],
    }),
  });

  /** Massenpflege */
  protected readonly mpForm = this.fb.group({
    product_group: this.fb.control('', { nonNullable: true }),
  });
  protected readonly vorschau = signal<MassenpflegeErgebnis | null>(null);
  protected readonly mpLaedt = signal(false);
  protected readonly mpMeldung = signal<string | null>(null);
  protected readonly mpErfolg = signal<string | null>(null);
  protected readonly mpFragen = signal(false);

  constructor() {
    this.laden();
  }

  private laden(): void {
    this.state.set({ kind: 'loading' });
    this.svc.listRules().subscribe({
      next: (data) => this.state.set({ kind: 'ready', data }),
      error: (err: unknown) => this.state.set(fehlerState(err)),
    });
    this.svc.warengruppen().subscribe({
      next: (wg) => this.warengruppen.set(wg),
      error: () => this.warengruppen.set([]),
    });
  }

  protected readonly warengruppenOptionen = computed<FeldOption[]>(() => [
    { wert: '', label: '— Warengruppe wählen —' },
    ...this.warengruppen().map((w) => ({
      wert: w.product_group,
      label: `${w.product_group} (${w.anzahl})`,
    })),
  ]);

  /** Reaktiv auf die Auswahl im Formular (ein computed über `.value` würde nicht
   *  neu rechnen — Reactive Forms sind keine Signals). */
  private readonly scopeWahl = toSignal(this.form.controls.scope.valueChanges, {
    initialValue: this.form.controls.scope.value,
  });
  protected readonly zeigtWarengruppe = computed(() => {
    const s = this.scopeWahl();
    return s === 'WARENGRUPPE' || s === 'WARENGRUPPE_LIEFERANT';
  });
  protected readonly zeigtLieferant = computed(() => {
    const s = this.scopeWahl();
    return s === 'LIEFERANT' || s === 'WARENGRUPPE_LIEFERANT';
  });

  protected prozent(wert: string | null): string {
    if (wert == null || String(wert).trim() === '') return '—';
    return `${apiZuDeAnzeige(wert, 1)} %`;
  }

  protected menge(wert: string): string {
    return apiZuDeAnzeige(wert, 0);
  }

  protected euro(wert: string | null): string {
    if (wert == null || String(wert).trim() === '') return 'unbekannt';
    return `${apiZuDeAnzeige(wert, 2)} €`;
  }

  protected basisLabel(b: CalcBasis): string {
    return b === 'LISTENPREIS' ? 'Listenpreis' : 'Einkaufspreis';
  }

  // --- Regel anlegen / bearbeiten -------------------------------------------

  neu(): void {
    if (!this.darfAnlegen()) return;
    this.meldung.set(null);
    this.form.reset({
      name: '',
      scope: 'WARENGRUPPE',
      product_group: '',
      supplier_party_id: '',
      calc_basis: 'EK',
      markup_percent: '',
      min_margin_percent: '',
    });
    this.staffel.set([]);
    this.modus.set('neu');
  }

  starteBearbeiten(r: MarkupRule): void {
    if (!this.darfAendern()) return;
    this.meldung.set(null);
    this.form.reset({
      name: r.name,
      // Der Geltungsbereich ist unveränderlich (DB-Trigger) — das Feld wird beim
      // Bearbeiten nur angezeigt, nicht gesendet.
      scope: (r.scope === 'ARTIKEL' ? 'STANDARD' : r.scope) as ScopeWahl,
      product_group: r.product_group ?? '',
      supplier_party_id: r.supplier_party_id ?? '',
      calc_basis: r.calc_basis,
      markup_percent: apiZuDeEingabe(r.markup_percent, 3),
      min_margin_percent: apiZuDeEingabe(r.min_margin_percent, 3),
    });
    this.staffel.set(
      r.tiers.map((t) => ({
        min_quantity: apiZuDeEingabe(t.min_quantity, 3),
        markup_percent: apiZuDeEingabe(t.markup_percent, 3),
      })),
    );
    this.modus.set(r.id);
  }

  abbrechen(): void {
    this.modus.set(null);
    this.meldung.set(null);
  }

  staffelZeile(): void {
    this.staffel.update((s) => [...s, { min_quantity: '', markup_percent: '' }]);
  }

  staffelEntfernen(i: number): void {
    this.staffel.update((s) => s.filter((_, idx) => idx !== i));
  }

  staffelAendern(i: number, feld: keyof StaffelZeile, ev: Event): void {
    const wert = (ev.target as HTMLInputElement).value;
    this.staffel.update((s) => s.map((z, idx) => (idx === i ? { ...z, [feld]: wert } : z)));
  }

  private staffelPayload(): MarkupTierIn[] | null {
    const zeilen = this.staffel();
    const out: MarkupTierIn[] = [];
    for (const z of zeilen) {
      const menge = deZuApiDezimal(z.min_quantity);
      const auf = deZuApiDezimal(z.markup_percent);
      if (menge === '' && auf === '') continue;
      if (menge === '' || auf === '') return null;
      out.push({ min_quantity: menge, markup_percent: auf });
    }
    return out;
  }

  speichern(): void {
    if (this.laedt()) return;
    this.meldung.set(null);
    this.form.markAllAsTouched();
    if (this.form.invalid) return;
    const v = this.form.getRawValue();
    const tiers = this.staffelPayload();
    if (tiers === null) {
      this.meldung.set('Jede Staffelstufe braucht eine Menge UND einen Aufschlag.');
      return;
    }
    const marge = deZuApiDezimal(v.min_margin_percent);
    const modus = this.modus();
    this.laedt.set(true);

    if (modus === 'neu') {
      const wgNoetig = v.scope === 'WARENGRUPPE' || v.scope === 'WARENGRUPPE_LIEFERANT';
      const liefNoetig = v.scope === 'LIEFERANT' || v.scope === 'WARENGRUPPE_LIEFERANT';
      if (wgNoetig && !v.product_group) {
        this.laedt.set(false);
        this.meldung.set('Bitte eine Warengruppe wählen.');
        return;
      }
      if (liefNoetig && !v.supplier_party_id) {
        this.laedt.set(false);
        this.meldung.set('Bitte einen Lieferanten wählen.');
        return;
      }
      this.svc
        .createRule({
          name: v.name,
          calc_basis: v.calc_basis,
          markup_percent: deZuApiDezimal(v.markup_percent),
          min_margin_percent: marge === '' ? null : marge,
          product_group: wgNoetig ? v.product_group : null,
          supplier_party_id: liefNoetig ? v.supplier_party_id : null,
        })
        .subscribe({
          next: (regel) => this.staffelSpeichern(regel.id, tiers),
          error: (err: unknown) => this.speichernFehler(err),
        });
      return;
    }

    this.svc
      .updateRule(modus!, {
        name: v.name,
        calc_basis: v.calc_basis,
        markup_percent: deZuApiDezimal(v.markup_percent),
        min_margin_percent: marge === '' ? null : marge,
      })
      .subscribe({
        next: () => this.staffelSpeichern(modus!, tiers),
        error: (err: unknown) => this.speichernFehler(err),
      });
  }

  /** Die Staffel wird immer mitgesendet (auch leer = alle Stufen deaktivieren). */
  private staffelSpeichern(ruleId: string, tiers: MarkupTierIn[]): void {
    this.svc.setTiers(ruleId, tiers).subscribe({
      next: () => {
        this.laedt.set(false);
        this.modus.set(null);
        this.vorschau.set(null);
        this.laden();
      },
      error: (err: unknown) => this.speichernFehler(err),
    });
  }

  private speichernFehler(err: unknown): void {
    this.laedt.set(false);
    this.meldung.set(fehlerDetail(err) ?? 'Die Regel konnte nicht gespeichert werden.');
  }

  umschalten(r: MarkupRule): void {
    if (this.laedt() || !this.darfAendern()) return;
    this.meldung.set(null);
    this.laedt.set(true);
    this.svc.setStatus(r.id, r.status === 'AKTIV' ? 'INAKTIV' : 'AKTIV').subscribe({
      next: () => {
        this.laedt.set(false);
        this.vorschau.set(null);
        this.laden();
      },
      error: (err: unknown) => {
        this.laedt.set(false);
        this.meldung.set(fehlerDetail(err) ?? 'Der Status konnte nicht geändert werden.');
      },
    });
  }

  // --- Massenpflege ---------------------------------------------------------

  vorschauLaden(): void {
    if (this.mpLaedt() || !this.darfAendern()) return;
    this.mpMeldung.set(null);
    this.mpErfolg.set(null);
    this.mpLaedt.set(true);
    const wg = this.mpForm.controls.product_group.value;
    this.svc.massenpflege({ product_group: wg || null, dry_run: true }).subscribe({
      next: (res) => {
        this.mpLaedt.set(false);
        this.vorschau.set(res);
      },
      error: (err: unknown) => {
        this.mpLaedt.set(false);
        this.vorschau.set(null);
        this.mpMeldung.set(fehlerDetail(err) ?? 'Die Vorschau konnte nicht erstellt werden.');
      },
    });
  }

  protected readonly mpBetroffen = computed(() => {
    const v = this.vorschau();
    return v ? v.angelegt + v.aktualisiert : 0;
  });

  protected readonly mpText = computed(() => {
    const v = this.vorschau();
    if (!v) return '';
    const wo = v.product_group
      ? `der Warengruppe „${v.product_group}“`
      : 'des gesamten Artikelstamms';
    const abschnittweise = v.weiter
      ? `Die Vorschau zeigt den ersten Abschnitt (${v.verarbeitet} von ` +
        `${v.artikel_gesamt} Artikeln); beim Übernehmen werden ALLE Abschnitte ` +
        'nacheinander abgearbeitet. '
      : '';
    return (
      `${abschnittweise}${this.mpBetroffen()} Verkaufspreis(e) ${wo} werden neu ` +
      `geschrieben (${v.angelegt} neu, ${v.aktualisiert} geändert). ` +
      `${v.unveraendert} bleiben gleich, ${v.uebersprungen} werden übersprungen ` +
      '(von Hand gesetzte Preise und unbekannte Einkaufspreise bleiben unberührt). ' +
      'Bereits geschriebene Angebote und Rechnungen ändern sich nicht.'
    );
  });

  anwendenFragen(): void {
    if (!this.vorschau() || this.mpBetroffen() === 0) return;
    this.mpFragen.set(true);
  }

  /**
   * Anwenden — abschnittsweise. Der Server verarbeitet je Aufruf einen Abschnitt
   * und meldet mit `weiter` den Fortsetzungspunkt; wir hängen die Abschnitte
   * aneinander, bis nichts mehr folgt. So ist auch eine Warengruppe mit
   * zehntausenden Katalogartikeln pflegbar.
   */
  anwenden(): void {
    const v = this.vorschau();
    if (!v || this.mpLaedt()) return;
    this.mpLaedt.set(true);
    this.mpAbschnitte.set(0);
    this.abschnittAnwenden(v.product_group, null, {
      angelegt: 0,
      aktualisiert: 0,
      uebersprungen: 0,
    });
  }

  /** Zahl der bereits abgearbeiteten Abschnitte (Fortschritt bei großen Läufen). */
  protected readonly mpAbschnitte = signal(0);

  private abschnittAnwenden(
    productGroup: string | null,
    ab: string | null,
    summe: { angelegt: number; aktualisiert: number; uebersprungen: number },
  ): void {
    this.svc
      .massenpflege({ product_group: productGroup, dry_run: false, ab_artikelnummer: ab })
      .subscribe({
        next: (res) => {
          summe.angelegt += res.angelegt;
          summe.aktualisiert += res.aktualisiert;
          summe.uebersprungen += res.uebersprungen;
          this.mpAbschnitte.update((n) => n + 1);
          if (res.weiter) {
            // Es folgen weitere Artikel — nahtlos fortsetzen.
            this.abschnittAnwenden(productGroup, res.weiter, summe);
            return;
          }
          this.mpLaedt.set(false);
          this.mpFragen.set(false);
          this.vorschau.set(res);
          this.mpErfolg.set(
            `${summe.angelegt + summe.aktualisiert} Verkaufspreis(e) neu gerechnet ` +
              `(${summe.angelegt} angelegt, ${summe.aktualisiert} aktualisiert, ` +
              `${summe.uebersprungen} übersprungen).`,
          );
        },
        error: (err: unknown) => {
          this.mpLaedt.set(false);
          this.mpFragen.set(false);
          this.mpMeldung.set(
            fehlerDetail(err) ??
              'Die Massenpflege konnte nicht (vollständig) angewendet werden.',
          );
        },
      });
  }

  protected aktionLabel(a: string): string {
    switch (a) {
      case 'ANLEGEN':
        return 'neu';
      case 'AKTUALISIEREN':
        return 'geändert';
      case 'UNVERAENDERT':
        return 'unverändert';
      default:
        return 'übersprungen';
    }
  }
}
