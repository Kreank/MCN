import { Component, computed, effect, inject, input, output, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { dezimalValidator } from '../../shared/formular/dezimal';
import { fehlerDetail } from '../../shared/http-fehler';
import { RaumService } from '../../core/raum.service';
import {
  ADJACENTS,
  AufbauIn,
  OPENING_TYPES,
  ORIENTATIONS,
  OpeningIn,
  OpeningType,
  ROOM_TYPES,
  Room,
  RoomIn,
  RoomType,
  SURFACE_TYPES,
  Surface,
  SurfaceIn,
  SurfaceType,
  adjacentLabel,
  istGezeichnet,
  istWaermeverlust,
  openingTypeLabel,
  orientationLabel,
  roomTypeLabel,
  surfaceTypeLabel,
} from '../../core/raum.model';
import { Building, gebaeudeLabel, unitTypeLabel } from '../../core/property.model';
import { Bauteil } from '../../core/bauteilkatalog.model';
import { BauteilkatalogService } from '../../core/bauteilkatalog.service';
import { BauteilWahl } from '../bauteilkatalog/bauteil-wahl';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { Huelle, Oeffnung, abgeleiteteWandflaeche, neueUid } from './aufbau-modell';
import { GrundrissEditor } from './grundriss/grundriss-editor';
import { kanten, meterAusMm, oeffnungPasst } from './grundriss/geometrie';
import {
  Flaechenwert,
  RAUM_HEIZLAST_HAFTUNG,
  apiZahl,
  bruttoGesamt,
  eingabe,
  flaecheAusMassen,
  ganzzahlAus,
  istFehleingabe,
  mitEinheit,
  nettoFlaeche,
  nettoGesamt,
  oeffnungFlaeche,
  oeffnungenGesamt,
  oeffnungenSumme,
  runde,
  volumenAus,
  zahlAus,
  zeige,
} from './raum-rechnen';

/**
 * Ein Raum: Stammdaten, Aufbau (Hüllflächen + Öffnungen), Kennzahlen.
 *
 * Bedient wird das **auf der Baustelle, im Stehen, mit Handschuhen**: große
 * Zahlenfelder (`inputmode="decimal"`), großzügige Klickflächen, zwei getrennt
 * speicherbare Schritte (Raum · Aufbau) statt eines Formular-Dickichts.
 *
 * INVARIANTEN:
 *  - **Die Fläche ist die Wahrheit, L × B nur die Herleitung.** Der Vorschlag
 *    wird live gerechnet und ist jederzeit überschreibbar (L-förmige Räume).
 *  - **Die Heizlast rechnet der Server.** Hier wird sie nur angezeigt; ist sie
 *    null, steht „unbekannt" mit Grund da — niemals 0, niemals eine Schätzung.
 *  - **Der Aufbau wird als GANZES gespeichert** (ein `PUT …/aufbau`), damit eine
 *    Öffnung nie auf eine Wand zeigt, die es noch nicht gibt.
 *  - **Der Katalog-U-Wert ist eine KOPIE.** Die Vorlage belegt das U-Wert-Feld
 *    vor; ein gemessener Wert schlägt sie. Eine spätere Katalogänderung zieht
 *    dieses Aufmaß NICHT nach (`template_id` merkt nur die Herkunft).
 *  - **Wer zeichnet, misst nicht doppelt.** Hat der Raum einen Umriss
 *    (Schritt 2, `GrundrissEditor`), rechnet der Server Fläche und Umfang aus dem
 *    Polygon — die beiden Felder sind dann nicht mehr tippbar, sondern zeigen das
 *    Ergebnis der Zeichnung. Ohne Umriss bleibt alles wie bisher.
 */
@Component({
  selector: 'app-raum-editor',
  imports: [ReactiveFormsModule, Feld, BauteilWahl, GrundrissEditor, Bestaetigung],
  templateUrl: './raum-editor.html',
  styleUrl: './raum-editor.scss',
})
export class RaumEditor {
  private readonly fb = inject(FormBuilder);
  private readonly svc = inject(RaumService);
  private readonly katalog = inject(BauteilkatalogService);

  readonly propertyId = input.required<string>();
  /** Der zu bearbeitende Raum — `null` legt einen neuen an. */
  readonly raum = input<Room | null>(null);
  readonly darfAendern = input(false);
  /** Gebäude/Einheiten der Liegenschaft — Grundlage der Standort-Zuordnung. */
  readonly gebaeude = input<readonly Building[]>([]);
  /**
   * Vorbelegung der Zuordnung für einen NEUEN Raum (er wurde aus einer
   * Einheiten-Gruppe heraus mit „＋ Raum" angelegt). Bei einem bestehenden Raum
   * unwirksam — dort gilt seine eigene Zuordnung.
   */
  readonly vorbelegung = input<{ building_id: string | null; unit_id: string | null } | null>(null);

  readonly gespeichert = output<Room>();
  readonly abbrechen = output<void>();

  protected readonly haftung = RAUM_HEIZLAST_HAFTUNG;

  // --- Auswahllisten -------------------------------------------------------
  protected readonly nutzungOptionen: FeldOption[] = ROOM_TYPES.map((t) => ({
    wert: t,
    label: roomTypeLabel(t),
  }));
  protected readonly bauteilArten = SURFACE_TYPES.map((t) => ({
    wert: t,
    label: surfaceTypeLabel(t),
  }));
  protected readonly nachbarn = ADJACENTS.map((a) => ({ wert: a, label: adjacentLabel(a) }));
  protected readonly himmelsrichtungen = ORIENTATIONS.map((o) => ({
    wert: o,
    label: orientationLabel(o),
  }));
  protected readonly oeffnungsArten = OPENING_TYPES.map((t) => ({
    wert: t,
    label: openingTypeLabel(t),
  }));

  // --- Standort-Zuordnung (Gebäude → Einheit) ------------------------------
  // Kaskade wie im Anlagen-Dialog: die Einheit setzt IHR Gebäude voraus (die DB
  // erzwingt das über zusammengesetzte Fremdschlüssel, 0086). Ein Gebäudewechsel
  // leert deshalb die Einheit — sonst schickte das Formular eine Einheit, die
  // zum neuen Gebäude nicht gehört, und der Server wiese sie mit 422 ab.
  protected readonly hatStruktur = computed(() => this.gebaeude().length > 0);
  protected readonly gebaeudeOptionen = computed<FeldOption[]>(() =>
    this.gebaeude().map((b) => ({ wert: b.id, label: gebaeudeLabel(b) })),
  );
  /** Gewähltes Gebäude als Signal, damit die Einheitsliste darauf reagiert. */
  private readonly gewaehltesGebaeude = signal('');
  protected readonly einheitOptionen = computed<FeldOption[]>(() => {
    const b = this.gebaeude().find((g) => g.id === this.gewaehltesGebaeude());
    if (!b) return [];
    return b.units.map((u) => ({
      wert: u.id,
      label: `${u.unit_number} · ${unitTypeLabel(u.unit_type)}`,
    }));
  });

  // --- Zustand -------------------------------------------------------------
  protected readonly huellen = signal<Huelle[]>([]);
  protected readonly oeffnungen = signal<Oeffnung[]>([]);
  protected readonly aufbauGeaendert = signal(false);
  protected readonly speichertRaum = signal(false);
  protected readonly speichertAufbau = signal(false);
  protected readonly raumFehler = signal<string | null>(null);
  protected readonly aufbauFehler = signal<string | null>(null);
  protected readonly ansage = signal('');
  /** Hat der Anwender die Fläche von Hand gesetzt? Dann wird sie NICHT überschrieben. */
  protected readonly flaecheManuell = signal(false);
  protected readonly erweitertOffen = signal(false);
  /** Ist der Grundriss-Abschnitt aufgeklappt? */
  protected readonly grundrissOffen = signal(false);
  protected readonly umrissFragen = signal(false);
  protected readonly umrissLaeuft = signal(false);

  /**
   * Hat der Raum einen Umriss? Dann rechnet der **Server** Fläche und Umfang aus
   * dem Polygon — die Felder sind hier nicht mehr frei tippbar. `geometrie_quelle`
   * ist die Auskunft des Servers darüber, was gilt; die Punkte sind nur der
   * Rückfall, falls das Feld (noch) fehlt.
   */
  protected readonly gezeichnet = computed(() => {
    const r = this.raum();
    if (!r) return false;
    return r.kennzahlen?.geometrie_quelle === 'GEZEICHNET' || istGezeichnet(r);
  });

  // --- Bauteilkatalog ------------------------------------------------------
  // Nur die AKTIVEN Vorlagen sind wählbar. Eine bereits erfasste, inzwischen
  // stillgelegte Vorlage bleibt trotzdem sichtbar (die Auswahl-Komponente sagt
  // es) — ihr U-Wert ist eine Kopie und bleibt gültig.
  protected readonly flaechenVorlagen = signal<Bauteil[]>([]);
  protected readonly oeffnungsVorlagen = signal<Bauteil[]>([]);
  /** Katalog nicht erreichbar: erfassen geht weiter — U-Werte dann von Hand. */
  protected readonly katalogFehler = signal<string | null>(null);

  protected readonly form = this.fb.group({
    name: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    storey: this.fb.control('', { nonNullable: true }),
    room_type: this.fb.control('', { nonNullable: true }),
    building_id: this.fb.control('', { nonNullable: true }),
    unit_id: this.fb.control('', { nonNullable: true }),
    length_m: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
    width_m: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
    floor_area_m2: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, dezimalValidator],
    }),
    room_height_m: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, dezimalValidator],
    }),
    perimeter_m: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
    indoor_temp_c: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
    air_change_rate: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
    heat_load_w_per_m2: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
    riser_distance_m: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
    note: this.fb.control('', { nonNullable: true }),
  });

  /** Zählt Formularänderungen — Auslöser für die Live-Vorschau. */
  private readonly tick = signal(0);

  constructor() {
    this.katalog.vorlagen(true).subscribe({
      next: (v) => {
        this.flaechenVorlagen.set(v.flaechen);
        this.oeffnungsVorlagen.set(v.oeffnungen);
      },
      error: (err) => {
        this.katalogFehler.set(
          fehlerDetail(err) ??
            'Der Bauteilkatalog konnte nicht geladen werden. U-Werte lassen sich trotzdem ' +
              'von Hand eintragen.',
        );
      },
    });

    effect(() => {
      const r = this.raum();
      this.uebernehmen(r);
    });

    // L oder B geändert → Flächenvorschlag nachziehen, SOLANGE die Fläche nicht
    // von Hand gesetzt wurde. Die Fläche bleibt die Wahrheit; L × B ist nur die
    // Herleitung (ein L-förmiger Raum hat keine.)
    this.form.controls.length_m.valueChanges
      .pipe(takeUntilDestroyed())
      .subscribe(() => this.flaecheNachziehen());
    this.form.controls.width_m.valueChanges
      .pipe(takeUntilDestroyed())
      .subscribe(() => this.flaecheNachziehen());

    // Tippt der Anwender in die Fläche, gehört sie ihm. (Programmatische Setzer
    // benutzen `emitEvent: false` — dieser Handler feuert also nur bei Eingabe.)
    this.form.controls.floor_area_m2.valueChanges.pipe(takeUntilDestroyed()).subscribe(() => {
      this.flaecheManuell.set(true);
    });

    // Gebäude gewechselt: die Einheit passt nicht mehr zum neuen Gebäude — sie
    // wird geleert, statt mitgeschleift zu werden (siehe Kommentar oben).
    this.form.controls.building_id.valueChanges.pipe(takeUntilDestroyed()).subscribe((b) => {
      if (b === this.gewaehltesGebaeude()) return;
      this.gewaehltesGebaeude.set(b);
      this.form.controls.unit_id.setValue('', { emitEvent: false });
    });

    this.form.valueChanges.pipe(takeUntilDestroyed()).subscribe(() => {
      this.raumFehler.set(null);
      this.tick.update((n) => n + 1);
    });
  }

  // --- Laden ---------------------------------------------------------------
  private uebernehmen(r: Room | null): void {
    this.raumFehler.set(null);
    this.aufbauFehler.set(null);
    this.aufbauGeaendert.set(false);

    if (!r) {
      this.form.reset();
      this.form.controls.room_height_m.setValue('2,50', { emitEvent: false });
      // Zuordnung eines neuen Raumes: die Gruppe, aus der „＋ Raum" geklickt wurde,
      // gibt Gebäude/Einheit vor. `reset()` hat sie eben geleert — hier zurück.
      const vor = this.vorbelegung();
      this.form.controls.building_id.setValue(vor?.building_id ?? '', { emitEvent: false });
      this.form.controls.unit_id.setValue(vor?.unit_id ?? '', { emitEvent: false });
      this.gewaehltesGebaeude.set(vor?.building_id ?? '');
      this.flaecheManuell.set(false);
      this.huellen.set([]);
      this.oeffnungen.set([]);
      this.tick.update((n) => n + 1);
      return;
    }

    this.gewaehltesGebaeude.set(r.building_id ?? '');
    this.form.setValue(
      {
        name: r.name,
        storey: r.storey ?? '',
        room_type: r.room_type ?? '',
        building_id: r.building_id ?? '',
        unit_id: r.unit_id ?? '',
        length_m: this.feldWert(r.length_m),
        width_m: this.feldWert(r.width_m),
        floor_area_m2: this.feldWert(r.floor_area_m2),
        room_height_m: this.feldWert(r.room_height_m),
        perimeter_m: this.feldWert(r.perimeter_m),
        indoor_temp_c: this.feldWert(r.indoor_temp_c),
        air_change_rate: this.feldWert(r.air_change_rate),
        heat_load_w_per_m2: this.feldWert(r.heat_load_w_per_m2),
        riser_distance_m: this.feldWert(r.riser_distance_m),
        note: r.note ?? '',
      },
      { emitEvent: false },
    );

    // Eine gespeicherte Fläche, die NICHT dem Produkt L × B entspricht, ist eine
    // bewusste Angabe (L-förmiger Raum) — sie darf nicht überschrieben werden.
    const ausMassen = flaecheAusMassen(this.feldWert(r.length_m), this.feldWert(r.width_m));
    const gespeichert = Number(r.floor_area_m2);
    this.flaecheManuell.set(
      ausMassen == null ||
        !Number.isFinite(gespeichert) ||
        Math.abs(ausMassen - gespeichert) > 1e-6,
    );

    // Refs: die Öffnungen hängen an der `uid` ihrer Wand (der Server kennt IDs).
    const refVonId = new Map<string, string>();
    const huellen = r.surfaces.map((s) => {
      const uid = neueUid('f');
      refVonId.set(s.id, uid);
      return this.huelleAusApi(s, uid);
    });
    this.huellen.set(huellen);
    this.oeffnungen.set(
      r.openings.map((o) => ({
        uid: neueUid('o'),
        surfaceRef: o.surface_id ? (refVonId.get(o.surface_id) ?? null) : null,
        opening_type: o.opening_type,
        label: o.label ?? '',
        anzahl: String(o.quantity ?? 1),
        breite: this.feldWert(o.width_m),
        hoehe: this.feldWert(o.height_m),
        u_value: this.feldWert(o.u_value),
        template_id: o.template_id ?? null,
        // null bleibt LEER — nicht 0. Fehlende Lage heißt unbekannt.
        position: this.feldWert(o.position_m),
      })),
    );
    this.tick.update((n) => n + 1);
  }

  private huelleAusApi(s: Surface, uid: string): Huelle {
    return {
      uid,
      surface_type: s.surface_type,
      adjacent: s.adjacent,
      orientation: s.orientation ?? '',
      label: s.label ?? '',
      // Hat der SERVER die Fläche abgeleitet (`area_is_derived`), bleibt das Feld
      // LEER — sonst schickte der nächste Speichervorgang den abgeleiteten Wert
      // zurück und machte die Wand damit zur Handeingabe. Ab da bliebe sie auf der
      // alten Raumhöhe stehen und die Heizlast würde still falsch. Der gerechnete
      // Wert wird trotzdem angezeigt (`abgeleitetM2`) — anzeigen ist nicht behaupten.
      brutto: s.area_is_derived ? '' : this.feldWert(s.gross_area_m2),
      u_value: this.feldWert(s.u_value),
      temp_factor: this.feldWert(s.temp_factor),
      template_id: s.template_id ?? null,
      edge_index: s.edge_index ?? null,
    };
  }

  /**
   * Die **abgeleitete** Bruttofläche einer Kantenwand: Kantenlänge × Raumhöhe —
   * dieselbe Formel, die der Server anwendet. Nur zur ANZEIGE; gesendet wird sie
   * nicht (sonst wäre die Wand Handeingabe).
   */
  abgeleitet(h: Huelle): number | null {
    if (h.edge_index == null) return null;
    const kante = this.kanteLaenge(h.edge_index);
    const e = eingabe(this.form.getRawValue().room_height_m);
    if (kante == null || e.art !== 'wert') return null;
    return abgeleiteteWandflaeche(kante, e.zahl);
  }

  /** Ist die Fläche dieser Wand abgeleitet (leer auf einer Kante)? */
  istAbgeleitet(h: Huelle): boolean {
    return h.edge_index != null && h.brutto.trim() === '';
  }

  /**
   * Die Fläche, mit der GERECHNET wird — die eingetragene, sonst die abgeleitete.
   * Nur für die Live-Vorschau (Netto, Wandflächen-Summe).
   */
  private effektivBrutto(h: Huelle): string {
    if (!this.istAbgeleitet(h)) return h.brutto;
    const a = this.abgeleitet(h);
    return a == null ? '' : apiZahl(a).replace('.', ',');
  }

  /** „Fläche abweichend eintragen" — ab jetzt Handeingabe (Giebel, Erker). */
  flaecheUebersteuern(h: Huelle): void {
    const a = this.abgeleitet(h);
    this.huelleSetzen(h.uid, { brutto: a == null ? '' : apiZahl(a).replace('.', ',') });
    this.ansage.set(
      'Die Fläche ist jetzt Handeingabe — der Server rechnet sie nicht mehr nach. ' +
        'Eine spätere Änderung von Umriss oder Raumhöhe zieht sie NICHT mehr nach.',
    );
    this.fokusAuf([`hf-brutto-${h.uid}`]);
  }

  /** „Zurück auf berechnet" — Feld leeren, dann schickt das Speichern nichts mehr. */
  flaecheAbleiten(h: Huelle): void {
    this.huelleSetzen(h.uid, { brutto: '' });
    this.ansage.set(
      'Die Fläche wird wieder aus der Kante berechnet (Kantenlänge × Raumhöhe) und bleibt ' +
        'automatisch aktuell.',
    );
  }

  /**
   * API-Wert → Eingabefeld: deutsches Komma, **ohne Tausenderpunkt** und ohne
   * Nachkomma-Nullen („12.500" → „12,5"). Ein gruppierter Wert wäre beim
   * Zurücklesen mehrdeutig — genau der alte Datenverlust-Bug.
   */
  private feldWert(w: string | number | null | undefined): string {
    if (w == null || w === '') return '';
    const n = Number(w);
    if (!Number.isFinite(n)) return String(w);
    return apiZahl(n).replace('.', ',');
  }

  // --- Fläche live ---------------------------------------------------------
  private flaecheNachziehen(): void {
    if (this.flaecheManuell()) return;
    const v = this.form.getRawValue();
    const f = flaecheAusMassen(v.length_m, v.width_m);
    this.form.controls.floor_area_m2.setValue(f == null ? '' : apiZahl(f).replace('.', ','), {
      emitEvent: false,
    });
  }

  /** „Aus L × B übernehmen" — holt den Anwender aus der Handeingabe zurück. */
  flaecheAusMassenUebernehmen(): void {
    const f = this.flaecheVorschlag();
    if (f == null) return;
    this.form.controls.floor_area_m2.setValue(apiZahl(f).replace('.', ','), { emitEvent: false });
    this.flaecheManuell.set(false);
    this.tick.update((n) => n + 1);
    this.ansage.set(`Fläche aus Länge × Breite übernommen: ${zeige(f)} m².`);
  }

  protected readonly flaecheVorschlag = computed(() => {
    this.tick();
    const v = this.form.getRawValue();
    return flaecheAusMassen(v.length_m, v.width_m);
  });

  /** Weicht die eingetragene Fläche vom Produkt L × B ab? (Text, nicht nur Farbe.) */
  protected readonly flaecheAbweichung = computed(() => {
    const vorschlag = this.flaecheVorschlag();
    if (vorschlag == null) return null;
    const v = this.form.getRawValue();
    const e = eingabe(v.floor_area_m2);
    if (e.art !== 'wert') return null;
    return Math.abs(e.zahl - vorschlag) > 1e-6 ? vorschlag : null;
  });

  /** Die eingetippte Fläche (Vorschau, solange der Raum nicht gespeichert ist). */
  protected readonly flaecheLive = computed(() => {
    this.tick();
    const e = eingabe(this.form.getRawValue().floor_area_m2);
    return e.art === 'wert' && e.zahl > 0 ? e.zahl : null;
  });

  protected readonly volumen = computed(() => {
    this.tick();
    const v = this.form.getRawValue();
    return volumenAus(v.floor_area_m2, v.room_height_m);
  });

  // --- Kennzahlen LIVE (Fläche/Volumen reagieren sofort auf die Eingabe) ----
  // Die verbindliche Zahl rechnet der Server; er liefert sie nach jedem Speichern
  // frisch mit (die Antwort aktualisiert `raum()`). SOLANGE aber getippt wird,
  // zeigt die Kennzahl die triviale Geometrie aus dem Formular — klar als
  // „Vorschau" markiert, wenn sie vom gespeicherten Stand abweicht. Ohne das
  // stünde neben einer frisch getippten Höhe weiter das alte Volumen: der Eindruck
  // „es rechnet nichts".
  /** Fläche für die Kennzahl: gezeichnet ⇒ Serverwert (aus dem Polygon), sonst live. */
  protected readonly kennFlaeche = computed<number | null>(() => {
    if (this.gezeichnet()) {
      const s = this.raum()?.kennzahlen.floor_area_m2;
      return s == null || s === '' ? null : Number(s);
    }
    return this.flaecheLive();
  });
  /** Volumen für die Kennzahl = Fläche × Raumhöhe (Höhe ist live, auch beim Umriss). */
  protected readonly kennVolumen = computed<number | null>(() => {
    this.tick();
    const f = this.kennFlaeche();
    const h = zahlAus(this.form.getRawValue().room_height_m);
    if (f == null || h == null || !(h > 0)) return this.gezeichnet() ? null : this.volumen();
    return runde(f * h, 3);
  });

  /** Weicht ein Live-Wert vom gespeicherten Serverwert ab? (dann: „Vorschau"). */
  private weichtAb(live: number | null, server: string | number | null | undefined): boolean {
    if (live == null) return false;
    if (server == null || server === '') return true;
    const s = Number(server);
    return !Number.isFinite(s) || Math.abs(s - live) > 1e-6;
  }
  protected readonly flaecheVorschau = computed(
    () => this.raum() != null && this.weichtAb(this.kennFlaeche(), this.raum()!.kennzahlen.floor_area_m2),
  );
  protected readonly volumenVorschau = computed(
    () => this.raum() != null && this.weichtAb(this.kennVolumen(), this.raum()!.kennzahlen.volume_m3),
  );

  /** Die Raumhöhe, wie sie im Formular steht — Grundlage der Kantenwand-Flächen. */
  protected readonly hoeheFeld = computed(() => {
    this.tick();
    return this.form.getRawValue().room_height_m;
  });

  // --- Live-Geometrie des Aufbaus -----------------------------------------
  // Jede dieser Größen ist ein `Flaechenwert`: Wert ODER unbekannt-mit-Grund.
  // Eine halb getippte Öffnung als 0 m² zu zählen (das alte `?? 0`) zeigte die
  // Wand zu groß — unvollständig heißt unbekannt, auch in der Vorschau.
  /**
   * Die Hüllflächen für die Vorschau — mit der **effektiven** Bruttofläche: die
   * eingetragene, sonst die aus der Kante abgeleitete. Anzeigen ist erlaubt;
   * gesendet wird die abgeleitete trotzdem nicht.
   */
  private readonly huellenMasse = computed(() => {
    this.tick(); // die Raumhöhe steht im Formular — sie geht in die Ableitung ein
    return this.huellen().map((h) => ({ brutto: this.effektivBrutto(h) }));
  });

  protected readonly wandBrutto = computed(() => bruttoGesamt(this.huellenMasse()));
  protected readonly oeffnungFlaecheSumme = computed(() => oeffnungenGesamt(this.oeffnungen()));
  protected readonly wandNetto = computed(() =>
    nettoGesamt(this.huellenMasse(), this.oeffnungen()),
  );

  /** Der erste Grund, warum die Vorschau unvollständig ist (Klartext, nicht nur „—"). */
  protected readonly vorschauGrund = computed(() => {
    for (const f of [this.wandBrutto(), this.oeffnungFlaecheSumme(), this.wandNetto()]) {
      if (f.art === 'unbekannt') return f.grund;
    }
    return null;
  });

  /** Nettofläche EINER Wand (brutto − ihre Öffnungen) — live, für Sofort-Feedback. */
  netto(h: Huelle): Flaechenwert {
    return nettoFlaeche(this.effektivBrutto(h), h.uid, this.oeffnungen());
  }

  oeffnungAnteil(h: Huelle): Flaechenwert {
    return oeffnungenSumme(h.uid, this.oeffnungen());
  }

  /** Anzeige eines Flächenwerts — unbekannt bleibt „—", nie 0. */
  fl(f: Flaechenwert, einheit = 'm²'): string {
    return f.art === 'wert' ? `${zeige(f.m2)} ${einheit}` : '—';
  }

  /** Der Grund, wenn ein Flächenwert unbekannt ist — sonst null. */
  grund(f: Flaechenwert): string | null {
    return f.art === 'unbekannt' ? f.grund : null;
  }

  /** Fläche EINER Öffnung — live. */
  flaecheDerOeffnung(o: Oeffnung): number | null {
    return oeffnungFlaeche(o.anzahl, o.breite, o.hoehe);
  }

  wandName(h: Huelle, index: number): string {
    return h.label.trim() || `${surfaceTypeLabel(h.surface_type)} ${index + 1}`;
  }

  /** Auswahl „welcher Wand gehört die Öffnung?" — nur die erfassten Hüllflächen. */
  protected readonly wandWahl = computed(() =>
    this.huellen().map((h, i) => ({ ref: h.uid, label: this.wandName(h, i) })),
  );

  /** Braucht diese Hüllfläche einen U-Wert? (Nur wenn sie gegen Kälte grenzt.) */
  brauchtUWert(h: Huelle): boolean {
    return istWaermeverlust(h.adjacent);
  }

  // --- Bauteil aus dem Katalog ---------------------------------------------
  /** Der U-Wert der Vorlage in Eingabeform — oder null, wenn der Katalog keinen hat. */
  private vorlagenWert(v: Bauteil | null): string | null {
    if (!v || v.u_value == null || v.u_value === '') return null;
    return this.feldWert(v.u_value);
  }

  /**
   * Vorlage für eine Hüllfläche gewählt.
   *
   * `template_id` merkt nur die **Herkunft**; der U-Wert wird **kopiert** und
   * bleibt überschreibbar (ein gemessener Wert schlägt die Vorlage). Hat die
   * Vorlage keinen U-Wert, bleibt das Feld unangetastet — ein bereits getippter
   * Wert wird NICHT weggeworfen.
   */
  huelleVorlage(uid: string, v: Bauteil | null): void {
    const patch: Partial<Huelle> = { template_id: v?.id ?? null };
    if (v?.default_surface_type) patch.surface_type = v.default_surface_type;
    const wert = this.vorlagenWert(v);
    if (wert !== null) patch.u_value = wert;
    this.huelleSetzen(uid, patch);

    if (!v) this.ansage.set('Vorlage entfernt. Der U-Wert bleibt stehen.');
    else if (wert === null) {
      this.ansage.set(
        `Vorlage „${v.name}" gewählt — im Katalog ist kein U-Wert hinterlegt. ` +
          'Ohne Eintrag bleibt die Heizlast unbekannt.',
      );
    } else {
      this.ansage.set(
        `Vorlage „${v.name}" gewählt. U-Wert ${wert} aus dem Katalog kopiert (überschreibbar).`,
      );
    }
  }

  /** „Vorlagenwert übernehmen" — holt den Katalogwert zurück in das U-Wert-Feld. */
  huelleVorlagenWert(uid: string, v: Bauteil): void {
    const wert = this.vorlagenWert(v);
    if (wert === null) return;
    this.huelleSetzen(uid, { u_value: wert });
    this.ansage.set(`U-Wert ${wert} aus der Vorlage „${v.name}" übernommen.`);
  }

  oeffnungVorlage(uid: string, v: Bauteil | null): void {
    const patch: Partial<Oeffnung> = { template_id: v?.id ?? null };
    if (v?.default_opening_type) patch.opening_type = v.default_opening_type;
    const wert = this.vorlagenWert(v);
    if (wert !== null) patch.u_value = wert;
    this.oeffnungSetzen(uid, patch);

    if (!v) this.ansage.set('Vorlage entfernt. Der U-Wert bleibt stehen.');
    else if (wert === null) {
      this.ansage.set(
        `Vorlage „${v.name}" gewählt — im Katalog ist kein U-Wert hinterlegt. ` +
          'Ohne Eintrag bleibt die Heizlast unbekannt.',
      );
    } else {
      this.ansage.set(
        `Vorlage „${v.name}" gewählt. U-Wert ${wert} aus dem Katalog kopiert (überschreibbar).`,
      );
    }
  }

  oeffnungVorlagenWert(uid: string, v: Bauteil): void {
    const wert = this.vorlagenWert(v);
    if (wert === null) return;
    this.oeffnungSetzen(uid, { u_value: wert });
    this.ansage.set(`U-Wert ${wert} aus der Vorlage „${v.name}" übernommen.`);
  }

  /** Unlesbare oder MEHRDEUTIGE Eingabe („1.500") — wird nie geraten, sondern gemeldet. */
  fehleingabe(roh: string): boolean {
    return istFehleingabe(roh);
  }

  /** Ausgefüllte, aber keine gültige Stückzahl. Leer meldet erst das Speichern. */
  anzahlUngueltig(roh: string): boolean {
    return roh.trim() !== '' && ganzzahlAus(roh) == null;
  }

  // --- Aufbau bearbeiten ---------------------------------------------------
  private aufbauAendern(): void {
    this.aufbauGeaendert.set(true);
    this.aufbauFehler.set(null);
  }

  huelleHinzufuegen(edge_index: number | null = null, brutto = ''): void {
    this.huellen.update((hs) => [
      ...hs,
      {
        uid: neueUid('f'),
        surface_type: 'AUSSENWAND',
        adjacent: 'AUSSENLUFT',
        orientation: '',
        label: edge_index == null ? '' : `Wand an Kante ${edge_index + 1}`,
        brutto,
        u_value: '',
        temp_factor: '',
        template_id: null,
        edge_index,
      },
    ]);
    this.aufbauAendern();
    this.ansage.set('Hüllfläche hinzugefügt.');
  }

  /**
   * Aus der Zeichnung heraus: dieser Kante eine Wand zuordnen.
   *
   * Ihre Bruttofläche bleibt **leer und damit abgeleitet** — der Server rechnet
   * sie (Kantenlänge × Raumhöhe) und hält sie aktuell. Sie hier vorzubelegen wäre
   * bequem und falsch: Die Wand wäre ab dem ersten Speichern Handeingabe und
   * würde eine spätere Höhenkorrektur nicht mehr mitmachen.
   */
  huelleAnKante(e: { edge_index: number }): void {
    if (this.huellen().some((h) => h.edge_index === e.edge_index)) return; // die DB verbietet zwei
    this.huelleHinzufuegen(e.edge_index, '');
  }

  huelleSetzen(uid: string, patch: Partial<Huelle>): void {
    // Decke, Boden und Dachschräge haben KEINE Kante — sie liegen über bzw. unter
    // dem Polygon. Wird eine Kantenwand dazu umgewidmet, fällt ihre Kante weg;
    // sie im Zustand stehen zu lassen, würde eine Zuordnung anzeigen, die beim
    // Speichern gar nicht mitgeht.
    const wirkt: Partial<Huelle> = { ...patch };
    const art = patch.surface_type;
    if (art && art !== 'AUSSENWAND' && art !== 'INNENWAND') wirkt.edge_index = null;
    this.huellen.update((hs) => hs.map((h) => (h.uid === uid ? { ...h, ...wirkt } : h)));
    this.aufbauAendern();
  }

  /**
   * Wand entfernen — die Öffnungen darin verlieren ihre Zuordnung und werden
   * NICHT stillschweigend mitgelöscht: sie bleiben stehen (ohne Wand) und
   * fallen dem Anwender auf. Ein heimlich verschwundenes Fenster wäre schlimmer.
   */
  huelleEntfernen(uid: string): void {
    const hs = this.huellen();
    const i = hs.findIndex((h) => h.uid === uid);
    const weg = hs[i];
    const vorher = i > 0 ? hs[i - 1].uid : null;
    this.huellen.update((liste) => liste.filter((h) => h.uid !== uid));
    this.oeffnungen.update((os) =>
      os.map((o) => (o.surfaceRef === uid ? { ...o, surfaceRef: null } : o)),
    );
    this.aufbauAendern();
    this.ansage.set(
      `Hüllfläche „${weg?.label || 'ohne Bezeichnung'}" entfernt. Zugehörige Öffnungen ` +
        'sind jetzt keiner Wand zugeordnet.',
    );
    this.fokusAuf([vorher ? `hf-weg-${vorher}` : null, 'hf-plus']);
  }

  oeffnungHinzufuegen(surfaceRef: string | null = null): void {
    this.oeffnungen.update((os) => [
      ...os,
      {
        uid: neueUid('o'),
        surfaceRef,
        opening_type: 'FENSTER',
        label: '',
        anzahl: '1',
        breite: '',
        hoehe: '',
        u_value: '',
        template_id: null,
        // Leer = Lage unbekannt. NICHT 0 — das wäre eine erfundene Angabe.
        position: '',
      },
    ]);
    this.aufbauAendern();
    this.ansage.set('Öffnung hinzugefügt.');
  }

  oeffnungSetzen(uid: string, patch: Partial<Oeffnung>): void {
    this.oeffnungen.update((os) => os.map((o) => (o.uid === uid ? { ...o, ...patch } : o)));
    this.aufbauAendern();
  }

  oeffnungEntfernen(uid: string): void {
    const os = this.oeffnungen();
    const i = os.findIndex((o) => o.uid === uid);
    const vorher = i > 0 ? os[i - 1].uid : null;
    this.oeffnungen.update((liste) => liste.filter((o) => o.uid !== uid));
    this.aufbauAendern();
    this.ansage.set('Öffnung entfernt.');
    this.fokusAuf([vorher ? `of-weg-${vorher}` : null, 'of-plus']);
  }

  /**
   * Fokus nach dem Entfernen gezielt weitersetzen (erste existierende ID
   * gewinnt). Ohne das fällt er auf `body` — wer per Tastatur mehrere Zeilen
   * löscht, verliert die Orientierung und muss sich neu durchtabben.
   *
   * `setTimeout` statt `queueMicrotask`: das Ziel existiert erst, nachdem
   * Angular das neue Template gerendert hat.
   */
  private fokusAuf(kandidaten: readonly (string | null)[]): void {
    setTimeout(() => {
      for (const id of kandidaten) {
        const el = id ? document.getElementById(id) : null;
        if (el) {
          el.focus();
          return;
        }
      }
    }, 0);
  }

  // --- Speichern: Raum -----------------------------------------------------
  raumSpeichern(): void {
    if (this.speichertRaum() || !this.darfAendern()) return;
    this.form.markAllAsTouched();
    if (this.form.invalid) {
      this.raumFehler.set('Bitte die rot markierten Felder prüfen.');
      return;
    }
    const v = this.form.getRawValue();

    const pflicht = (roh: string, feld: string): string | null => {
      const e = eingabe(roh);
      if (e.art === 'wert') return e.api;
      this.raumFehler.set(
        e.art === 'leer'
          ? `${feld} ist erforderlich.`
          : `${feld}: „${roh}" ist nicht eindeutig. Bitte ohne Tausenderpunkt eingeben ` +
              '(z. B. 1500 oder 1,5).',
      );
      return null;
    };
    const optional = (roh: string, feld: string): string | null | undefined => {
      const e = eingabe(roh);
      if (e.art === 'wert') return e.api;
      if (e.art === 'leer') return null;
      this.raumFehler.set(
        `${feld}: „${roh}" ist nicht eindeutig. Bitte ohne Tausenderpunkt eingeben.`,
      );
      return undefined; // Sentinel: Fehler
    };

    const flaeche = pflicht(v.floor_area_m2, 'Fläche');
    if (flaeche == null) return;
    const hoehe = pflicht(v.room_height_m, 'Raumhöhe');
    if (hoehe == null) return;

    const felder: Record<string, string> = {
      length_m: 'Länge',
      width_m: 'Breite',
      perimeter_m: 'Umfang',
      indoor_temp_c: 'Innentemperatur',
      air_change_rate: 'Luftwechselrate',
      heat_load_w_per_m2: 'Kennwert',
      riser_distance_m: 'Abstand zur Steigleitung',
    };
    const opt: Record<string, string | null> = {};
    for (const [key, label] of Object.entries(felder)) {
      const wert = optional((v as Record<string, string>)[key], label);
      if (wert === undefined) return;
      opt[key] = wert;
    }

    const payload: RoomIn = {
      name: v.name.trim(),
      floor_area_m2: flaeche,
      room_height_m: hoehe,
      storey: v.storey.trim() || null,
      room_type: (v.room_type as RoomType) || null,
      // Standort: leer = keine Zuordnung (Altbestand/„Gebäude allgemein"). Die
      // Einheit setzt ihr Gebäude voraus — die Kaskade oben hält das konsistent,
      // der Server prüft es zusätzlich (`ensure_standort`).
      building_id: v.building_id || null,
      unit_id: v.unit_id || null,
      length_m: opt['length_m'],
      width_m: opt['width_m'],
      perimeter_m: opt['perimeter_m'],
      indoor_temp_c: opt['indoor_temp_c'],
      air_change_rate: opt['air_change_rate'],
      heat_load_w_per_m2: opt['heat_load_w_per_m2'],
      riser_distance_m: opt['riser_distance_m'],
      note: v.note.trim() || null,
    };

    const vorhanden = this.raum();
    this.speichertRaum.set(true);
    this.raumFehler.set(null);
    const req$ = vorhanden
      ? this.svc.update(vorhanden.id, payload)
      : this.svc.create(this.propertyId(), payload);
    req$.subscribe({
      next: (r) => {
        this.speichertRaum.set(false);
        this.ansage.set(`Raum „${r.name}" gespeichert.`);
        this.gespeichert.emit(r);
      },
      error: (err) => {
        this.speichertRaum.set(false);
        this.raumFehler.set(fehlerDetail(err) ?? 'Der Raum konnte nicht gespeichert werden.');
      },
    });
  }

  // --- Speichern: Aufbau ---------------------------------------------------
  aufbauSpeichern(): void {
    const r = this.raum();
    if (!r || this.speichertAufbau() || !this.darfAendern()) return;

    const surfaces: SurfaceIn[] = [];
    for (const [i, h] of this.huellen().entries()) {
      const name = this.wandName(h, i);

      // Decke, Boden und Dachschräge haben KEINE Kante — sie liegen über bzw.
      // unter dem Polygon. Eine Kantenzuordnung wäre dort schlicht falsch.
      const kante =
        h.surface_type === 'AUSSENWAND' || h.surface_type === 'INNENWAND' ? h.edge_index : null;

      // ==================== DER ENTSCHEIDENDE PUNKT (0093) ====================
      // Sitzt die Wand auf einer Kante und hat NIEMAND eine Fläche eingetragen,
      // wird `gross_area_m2` NICHT gesendet. Der Server rechnet sie dann selbst
      // (Kantenlänge × Raumhöhe) und hält sie aktuell — korrigiert jemand die
      // Raumhöhe von 2,50 auf 2,80 m, wandern die Wandflächen mit.
      //
      // Schickten wir hier den abgeleiteten Wert mit, wäre die Wand ab sofort
      // Handeingabe (`area_is_derived = false`): Sie bliebe auf 2,50 m stehen,
      // niemand würde es merken, und die Heizlast wäre still falsch. Genau dagegen
      // ist 0093 gebaut.
      const brutto = eingabe(h.brutto);
      const abgeleitet = kante != null && brutto.art === 'leer';
      if (!abgeleitet) {
        if (brutto.art !== 'wert' || !(brutto.zahl > 0)) {
          this.aufbauFehler.set(
            brutto.art === 'fehler'
              ? `${name}: Die Fläche „${h.brutto}" ist nicht eindeutig (bitte ohne Tausenderpunkt).`
              : `${name}: Die Bruttofläche ist erforderlich (größer als 0). Ohne Kante gibt es ` +
                  'nichts, woraus der Server sie ableiten könnte.',
          );
          return;
        }
      }

      const u = eingabe(h.u_value);
      if (u.art === 'fehler') {
        this.aufbauFehler.set(`${name}: Der U-Wert „${h.u_value}" ist nicht lesbar.`);
        return;
      }
      const tf = eingabe(h.temp_factor);
      if (tf.art === 'fehler') {
        this.aufbauFehler.set(`${name}: Der Temperaturfaktor „${h.temp_factor}" ist nicht lesbar.`);
        return;
      }
      const eintrag: SurfaceIn = {
        ref: h.uid,
        surface_type: h.surface_type,
        adjacent: h.adjacent,
        orientation: (h.orientation || null) as SurfaceIn['orientation'],
        label: h.label.trim() || null,
        u_value: u.art === 'wert' ? u.api : null,
        temp_factor: tf.art === 'wert' ? tf.api : null,
        // Herkunftsvermerk. Der U-Wert oben ist und bleibt eine Kopie.
        template_id: h.template_id,
        edge_index: kante,
      };
      // Nur die HANDEINGABE wird gesendet. Weglassen = „rechne du" (siehe oben).
      if (!abgeleitet && brutto.art === 'wert') eintrag.gross_area_m2 = brutto.api;
      surfaces.push(eintrag);
    }

    const openings: OpeningIn[] = [];
    for (const [i, o] of this.oeffnungen().entries()) {
      const name = o.label.trim() || `${openingTypeLabel(o.opening_type)} ${i + 1}`;
      const anzahl = ganzzahlAus(o.anzahl);
      if (anzahl == null) {
        this.aufbauFehler.set(`${name}: Die Anzahl muss eine ganze Zahl größer als 0 sein.`);
        return;
      }
      const b = eingabe(o.breite);
      const h = eingabe(o.hoehe);
      if (b.art !== 'wert' || !(b.zahl > 0) || h.art !== 'wert' || !(h.zahl > 0)) {
        this.aufbauFehler.set(`${name}: Breite und Höhe sind erforderlich (größer als 0, in m).`);
        return;
      }
      const u = eingabe(o.u_value);
      if (u.art === 'fehler') {
        this.aufbauFehler.set(`${name}: Der U-Wert „${o.u_value}" ist nicht lesbar.`);
        return;
      }

      // Die Lage in der Wand. LEER BLEIBT LEER — daraus wird nie eine 0: eine
      // Öffnung ohne ausgemessene Lage zählt normal mit, sie wird nur nicht
      // gezeichnet. Eine 0 wäre die Behauptung „sitzt genau in der Ecke".
      const pos = eingabe(o.position);
      if (pos.art === 'fehler') {
        this.aufbauFehler.set(
          `${name}: Die Lage „${o.position}" ist nicht eindeutig (bitte ohne Tausenderpunkt).`,
        );
        return;
      }
      if (pos.art === 'wert' && pos.zahl < 0) {
        this.aufbauFehler.set(`${name}: Die Lage darf nicht negativ sein.`);
        return;
      }
      // Passt sie in ihre Kante? Der Server prüft es auch — hier steht es im Klartext.
      if (pos.art === 'wert') {
        const wand = this.huellen().find((x) => x.uid === o.surfaceRef);
        const kante = wand?.edge_index != null ? this.kanteLaenge(wand.edge_index) : null;
        const passung = oeffnungPasst(pos.zahl, b.zahl, kante);
        if (passung.art === 'passt_nicht') {
          this.aufbauFehler.set(`${name}: ${passung.grund}`);
          return;
        }
      }

      openings.push({
        surface_ref: o.surfaceRef,
        opening_type: o.opening_type,
        label: o.label.trim() || null,
        quantity: anzahl,
        width_m: b.api,
        height_m: h.api,
        u_value: u.art === 'wert' ? u.api : null,
        template_id: o.template_id,
        position_m: pos.art === 'wert' ? pos.api : null,
      });
    }

    const payload: AufbauIn = { surfaces, openings };
    this.speichertAufbau.set(true);
    this.aufbauFehler.set(null);
    this.svc.setAufbau(r.id, payload).subscribe({
      next: (aktualisiert) => {
        this.speichertAufbau.set(false);
        this.aufbauGeaendert.set(false);
        this.ansage.set('Aufbau gespeichert. Die Kennzahlen kommen vom Server.');
        this.gespeichert.emit(aktualisiert);
      },
      error: (err) => {
        this.speichertAufbau.set(false);
        this.aufbauFehler.set(
          fehlerDetail(err) ??
            'Der Aufbau konnte nicht gespeichert werden. Prüfen Sie, ob eine Öffnung ' +
              'größer ist als ihre Wand.',
        );
      },
    });
  }

  // --- Grundriss -----------------------------------------------------------
  /** Länge der Kante `i` des GESPEICHERTEN Umrisses (in m) — null, wenn es sie nicht gibt. */
  private kanteLaenge(i: number): number | null {
    const r = this.raum();
    if (!r) return null;
    const punkte = [...r.vertices]
      .sort((a, b) => a.idx - b.idx)
      .map((v) => ({ x_mm: v.x_mm, y_mm: v.y_mm }));
    const k = kanten(punkte)[i];
    return k ? meterAusMm(k.laenge_mm) : null;
  }

  /** Der Umriss wurde gespeichert — der Raum kommt komplett zurück (Fläche/Umfang vom Server). */
  grundrissGespeichert(r: Room): void {
    this.gespeichert.emit(r);
  }

  umrissEntfernenFragen(): void {
    if (!this.darfAendern()) return;
    this.umrissFragen.set(true);
  }

  umrissEntfernenAbbrechen(): void {
    if (this.umrissLaeuft()) return;
    this.umrissFragen.set(false);
  }

  /**
   * Umriss entfernen: Fläche und Umfang werden danach **wieder Handeingabe**, die
   * `edge_index` der Wände fallen auf null. Der Aufbau bleibt sonst unangetastet —
   * eine Wand verliert ihre Kante, nicht ihre Existenz.
   */
  umrissEntfernenBestaetigen(): void {
    const r = this.raum();
    if (!r || this.umrissLaeuft()) return;
    this.umrissLaeuft.set(true);
    this.svc.setGrundriss(r.id, []).subscribe({
      next: (neu) => {
        this.umrissLaeuft.set(false);
        this.umrissFragen.set(false);
        // Den Zeichner zumachen: er hält einen eigenen Arbeitsstand. Bliebe er
        // offen, zeigte er weiter den gelöschten Umriss — er wird hier zerstört
        // und beim nächsten Öffnen frisch vom Server befüllt.
        this.grundrissOffen.set(false);
        this.ansage.set(
          'Umriss entfernt. Fläche und Umfang sind wieder von Hand einzutragen; die Wände ' +
            'haben keine Kante mehr.',
        );
        this.gespeichert.emit(neu);
      },
      error: (err) => {
        this.umrissLaeuft.set(false);
        this.umrissFragen.set(false);
        this.aufbauFehler.set(fehlerDetail(err) ?? 'Der Umriss konnte nicht entfernt werden.');
      },
    });
  }

  // --- Anzeige -------------------------------------------------------------
  /**
   * Zahl aus der API **mit Einheit** — `null` bleibt „unbekannt" (ohne Einheit,
   * denn „unbekannt m²" wäre Unsinn) und wird NIE zu 0.
   */
  wert(
    w: string | number | null | undefined,
    einheit: string,
    nachkomma = 2,
    unbekannt = 'unbekannt',
  ): string {
    return mitEinheit(w, einheit, nachkomma, unbekannt);
  }

  /** Lokal gerechnete Geometrie (Vorschau). */
  live(n: number | null, nachkomma = 2): string {
    return n == null ? '—' : zeige(n, nachkomma);
  }

  /** Lokal gerechnete Geometrie mit Einheit. */
  liveEinheit(n: number | null, einheit: string, nachkomma = 2): string {
    return n == null ? '—' : `${zeige(n, nachkomma)} ${einheit}`;
  }

  kw(watt: string | number | null | undefined): string {
    if (watt == null || watt === '') return 'unbekannt';
    const n = Number(watt);
    return Number.isFinite(n) ? `${zeige(n / 1000, 1)} kW` : 'unbekannt';
  }

  bauteilLabel(t: SurfaceType): string {
    return surfaceTypeLabel(t);
  }
  oeffnungLabel(t: OpeningType): string {
    return openingTypeLabel(t);
  }
}
