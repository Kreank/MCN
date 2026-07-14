import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormBuilder, FormControl, ReactiveFormsModule, Validators } from '@angular/forms';
import { AuthService } from '../../core/auth.service';
import {
  BAUTEIL_KOPIE_HINWEIS,
  BAUTEIL_OHNE_UWERT_HINWEIS,
  Bauteil,
  BauteilGattung,
  BauteilIn,
  bauteilArtLabel,
  ohneUWert,
} from '../../core/bauteilkatalog.model';
import { BauteilkatalogService } from '../../core/bauteilkatalog.service';
import {
  OPENING_TYPES,
  SURFACE_TYPES,
  openingTypeLabel,
  surfaceTypeLabel,
} from '../../core/raum.model';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { dezimalValidator } from '../../shared/formular/dezimal';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';
import { EinstellungenNav } from '../einstellungen-nav/einstellungen-nav';
import { apiZahl, eingabe } from '../raumaufmass/raum-rechnen';

type ViewState = { kind: 'loading' } | { kind: 'ready' } | VerbotenState | { kind: 'error' };

/**
 * Bauteilkatalog — die Vorlagen, aus denen im Raumaufmaß erfasst wird
 * („Fenster, Doppelkastenfenster" statt „2,7").
 *
 * ZWEI DINGE, die diese Seite unmissverständlich sagen muss:
 *
 * 1. **Der Katalog kommt ohne U-Werte.** Es gibt bewusst keine mitgelieferten
 *    Normtabellen; der Betrieb trägt den Wert einmal ein. Eine Vorlage ohne Wert
 *    ist der Auslieferungszustand, kein Fehler — aber sie ist markiert („U-Wert
 *    fehlt") und direkt befüllbar, sonst bleibt die Heizlast unbemerkt unbekannt.
 *
 * 2. **Der Wert wird beim Erfassen kopiert, nicht verlinkt.** Eine Korrektur hier
 *    ändert **kein bestehendes Aufmaß**. Ohne diesen Satz hält es jemand für einen
 *    Bug.
 *
 * Gelöscht wird nie (die Datenbank verbietet es) — stillgelegt wird über
 * `status = 'INAKTIV'`.
 */
@Component({
  selector: 'app-bauteilkatalog',
  imports: [ReactiveFormsModule, Feld, EinstellungenNav, KeinZugriff],
  templateUrl: './bauteilkatalog.html',
  styleUrl: './bauteilkatalog.scss',
})
export class Bauteilkatalog {
  private readonly svc = inject(BauteilkatalogService);
  private readonly fb = inject(FormBuilder);
  private readonly auth = inject(AuthService);

  protected readonly kopieHinweis = BAUTEIL_KOPIE_HINWEIS;
  protected readonly ohneUWertHinweis = BAUTEIL_OHNE_UWERT_HINWEIS;

  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly vorlagen = signal<Bauteil[]>([]);
  protected readonly laedt = signal(false);
  protected readonly meldung = signal<string | null>(null);
  protected readonly ansage = signal('');
  /** Welche Zeile hat gerade ihr U-Wert-Feld offen? */
  protected readonly uOffen = signal<string | null>(null);

  protected readonly darfAnlegen = computed(() => this.auth.darf('property', 'ANLEGEN'));
  protected readonly darfAendern = computed(() => this.auth.darf('property', 'AENDERN'));

  /** Alle Vorlagen — auch die stillgelegten (sonst ließe sich keine reaktivieren). */
  protected readonly flaechen = computed(() => this.nachGattung('FLAECHE'));
  protected readonly oeffnungen = computed(() => this.nachGattung('OEFFNUNG'));

  protected readonly ohneWertAnzahl = computed(
    () => this.vorlagen().filter((v) => v.status === 'AKTIV' && ohneUWert(v)).length,
  );

  /** Die zwei Listen der Seite — ein Template-Block, zwei Gattungen. */
  protected readonly gruppen = computed(() => [
    {
      key: 'FLAECHE' as const,
      titel: 'Hüllflächen',
      lead: 'Wände, Decken, Böden — z. B. „Außenwand, Ziegel ungedämmt".',
      liste: this.flaechen(),
    },
    {
      key: 'OEFFNUNG' as const,
      titel: 'Öffnungen',
      lead: 'Fenster und Türen — z. B. „Fenster, Doppelkastenfenster".',
      liste: this.oeffnungen(),
    },
  ]);

  // --- Neue Vorlage --------------------------------------------------------
  protected readonly neuForm = this.fb.group({
    kind: this.fb.control<BauteilGattung>('FLAECHE', { nonNullable: true }),
    name: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    art: this.fb.control('', { nonNullable: true }),
    u_value: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
    note: this.fb.control('', { nonNullable: true }),
  });

  /** U-Wert einer bestehenden Vorlage nachtragen/ändern (inline). */
  protected readonly uControl = new FormControl('', {
    nonNullable: true,
    validators: [dezimalValidator],
  });

  private readonly gattung = signal<BauteilGattung>('FLAECHE');

  protected readonly gattungOptionen: FeldOption[] = [
    { wert: 'FLAECHE', label: 'Hüllfläche (Wand, Decke, Boden)' },
    { wert: 'OEFFNUNG', label: 'Öffnung (Fenster, Tür)' },
  ];

  /** Die Art-Vorbelegung hängt an der Gattung — Fenster sind keine Außenwände. */
  protected readonly artOptionen = computed<FeldOption[]>(() =>
    this.gattung() === 'FLAECHE'
      ? SURFACE_TYPES.map((t) => ({ wert: t, label: surfaceTypeLabel(t) }))
      : OPENING_TYPES.map((t) => ({ wert: t, label: openingTypeLabel(t) })),
  );

  constructor() {
    this.neuForm.controls.kind.valueChanges.pipe(takeUntilDestroyed()).subscribe((k) => {
      this.gattung.set(k);
      this.neuForm.controls.art.setValue('', { emitEvent: false });
    });
    this.laden();
  }

  private nachGattung(k: BauteilGattung): Bauteil[] {
    return this.vorlagen()
      .filter((v) => v.kind === k)
      .sort((a, b) => a.sort_index - b.sort_index || a.name.localeCompare(b.name, 'de'));
  }

  private laden(): void {
    this.state.set({ kind: 'loading' });
    // `nurAktive = false`: die Pflegeseite zeigt auch Stillgelegtes — sonst ließe
    // sich nichts reaktivieren, und der Bestand wäre unsichtbar statt stillgelegt.
    this.svc.vorlagen(false).subscribe({
      next: (v) => {
        this.vorlagen.set([...v.flaechen, ...v.oeffnungen]);
        this.state.set({ kind: 'ready' });
      },
      error: (err: unknown) => this.state.set(fehlerState(err)),
    });
  }

  neuLaden(): void {
    this.laden();
  }

  // --- Anlegen -------------------------------------------------------------
  anlegen(): void {
    if (this.laedt() || !this.darfAnlegen()) return;
    this.meldung.set(null);
    this.neuForm.markAllAsTouched();
    if (this.neuForm.invalid) return;

    const v = this.neuForm.getRawValue();
    const u = eingabe(v.u_value);
    if (u.art === 'fehler') {
      this.meldung.set(
        `Der U-Wert „${v.u_value}" ist nicht eindeutig. Bitte ohne Tausenderpunkt eingeben (z. B. 0,24).`,
      );
      return;
    }

    const payload: BauteilIn = {
      kind: v.kind,
      name: v.name.trim(),
      default_surface_type: v.kind === 'FLAECHE' && v.art ? (v.art as never) : null,
      default_opening_type: v.kind === 'OEFFNUNG' && v.art ? (v.art as never) : null,
      u_value: u.art === 'wert' ? u.api : null,
      note: v.note.trim() || null,
    };

    this.laedt.set(true);
    this.svc.create(payload).subscribe({
      next: (angelegt) => {
        this.laedt.set(false);
        this.neuForm.reset({ kind: v.kind, name: '', art: '', u_value: '', note: '' });
        this.ansage.set(
          ohneUWert(angelegt)
            ? `Vorlage „${angelegt.name}" angelegt — noch ohne U-Wert.`
            : `Vorlage „${angelegt.name}" angelegt.`,
        );
        this.laden();
      },
      error: (err: unknown) => {
        this.laedt.set(false);
        this.meldung.set(fehlerDetail(err) ?? 'Die Vorlage konnte nicht angelegt werden.');
      },
    });
  }

  // --- U-Wert inline -------------------------------------------------------
  uBearbeiten(v: Bauteil): void {
    if (!this.darfAendern()) return;
    this.meldung.set(null);
    this.uControl.setValue(this.feldWert(v.u_value));
    this.uControl.markAsUntouched();
    this.uOffen.set(v.id);
  }

  uAbbrechen(): void {
    this.uOffen.set(null);
  }

  uSpeichern(v: Bauteil): void {
    if (this.laedt()) return;
    this.uControl.markAsTouched();
    if (this.uControl.invalid) return;
    const u = eingabe(this.uControl.value);
    if (u.art === 'fehler') {
      this.meldung.set('Der U-Wert ist nicht eindeutig. Bitte ohne Tausenderpunkt eingeben.');
      return;
    }
    this.laedt.set(true);
    this.svc.update(v.id, { u_value: u.art === 'wert' ? u.api : null }).subscribe({
      next: (neu) => {
        this.laedt.set(false);
        this.uOffen.set(null);
        this.ersetzen(neu);
        this.ansage.set(
          ohneUWert(neu)
            ? `U-Wert von „${neu.name}" geleert — die Heizlast bleibt damit unbekannt.`
            : `U-Wert von „${neu.name}" gespeichert. Bestehende Aufmaße bleiben unverändert.`,
        );
      },
      error: (err: unknown) => {
        this.laedt.set(false);
        this.meldung.set(fehlerDetail(err) ?? 'Der U-Wert konnte nicht gespeichert werden.');
      },
    });
  }

  // --- Stilllegen / reaktivieren ------------------------------------------
  statusUmschalten(v: Bauteil): void {
    if (this.laedt() || !this.darfAendern()) return;
    this.meldung.set(null);
    const neuerStatus = v.status === 'AKTIV' ? 'INAKTIV' : 'AKTIV';
    this.laedt.set(true);
    this.svc.update(v.id, { status: neuerStatus }).subscribe({
      next: (neu) => {
        this.laedt.set(false);
        this.ersetzen(neu);
        this.ansage.set(
          neu.status === 'INAKTIV'
            ? `Vorlage „${neu.name}" stillgelegt. Erfasste Aufmaße behalten ihren Wert.`
            : `Vorlage „${neu.name}" wieder aktiv.`,
        );
      },
      error: (err: unknown) => {
        this.laedt.set(false);
        this.meldung.set(fehlerDetail(err) ?? 'Der Status konnte nicht geändert werden.');
      },
    });
  }

  private ersetzen(neu: Bauteil): void {
    this.vorlagen.update((vs) => vs.map((v) => (v.id === neu.id ? neu : v)));
  }

  // --- Anzeige -------------------------------------------------------------
  /** API-Wert → Eingabefeld: deutsches Komma, ohne Tausenderpunkt („0.240" → „0,24"). */
  private feldWert(w: string | null): string {
    if (w == null || w === '') return '';
    const n = Number(w);
    return Number.isFinite(n) ? apiZahl(n).replace('.', ',') : String(w);
  }

  uWertAnzeige(v: Bauteil): string {
    const s = this.feldWert(v.u_value);
    return s === '' ? '' : `${s} W/m²K`;
  }

  ohneWert(v: Bauteil): boolean {
    return ohneUWert(v);
  }

  artLabel(v: Bauteil): string {
    return bauteilArtLabel(v);
  }
}
