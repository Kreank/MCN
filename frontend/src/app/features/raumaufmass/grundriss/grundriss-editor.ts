import { Component, computed, effect, inject, input, output, signal } from '@angular/core';
import { RaumService } from '../../../core/raum.service';
import {
  OpeningType,
  Orientation,
  Room,
  SurfaceType,
  adjacentLabel,
  openingTypeLabel,
  orientationLabel,
  surfaceTypeLabel,
} from '../../../core/raum.model';
import { fehlerDetail } from '../../../shared/http-fehler';
import { Huelle, Oeffnung, abgeleiteteWandflaeche } from '../aufbau-modell';
import { eingabe, zeige } from '../raum-rechnen';
import {
  Befund,
  Kante,
  Nord,
  Passung,
  Punkt,
  Sicht,
  beschreibung,
  flaecheM2,
  inSicht,
  istSchliessendeKante,
  kanteLaengeSetzen,
  kanten,
  meterAusMm,
  mmAusMeter,
  nordRichtung,
  oeffnungPasst,
  pruefe,
  punktAufKante,
  punktEinfuegen,
  punktLoeschen,
  punktSetzen,
  punktVerschieben,
  sichtEinpassen,
  snapPunkt,
  umfangM,
  zuSicht,
  zuWelt,
} from './geometrie';

/** Kürzel an der Kante — zwei Zeichen sagen mehr als eine Farbe. */
const ART_KURZ: Record<SurfaceType, string> = {
  AUSSENWAND: 'AW',
  INNENWAND: 'IW',
  DACHSCHRAEGE: 'DS',
  DECKE: 'DE',
  BODEN: 'BO',
};
const NACHBAR_KURZ: Record<string, string> = {
  AUSSENLUFT: 'Außenluft',
  ERDREICH: 'Erdreich',
  UNBEHEIZT: 'unbeheizt',
  BEHEIZT: 'beheizt',
};

/** Eine Öffnung, wie sie in der Kantenzeile (und ggf. in der Zeichnung) steht. */
interface OeffnungZeile {
  readonly uid: string;
  readonly name: string;
  readonly art: OpeningType;
  readonly istTuer: boolean;
  readonly anzahl: number;
  readonly breiteM: number | null;
  readonly positionM: number | null;
  readonly feld: string;
  readonly passung: Passung;
}

/** Eine Kante mit allem, was an ihr hängt — das barrierefreie Äquivalent zur Zeichnung. */
interface KantenZeile {
  readonly index: number;
  readonly nr: number;
  readonly schliessend: boolean;
  readonly laengeM: number;
  readonly feld: string;
  readonly fehlerhaft: boolean;
  readonly huelle: Huelle | null;
  readonly kuerzel: string;
  readonly nachbar: string;
  readonly richtung: string;
  readonly abgeleitetM2: number | null;
  readonly bruttoM2: number | null;
  readonly ueberschrieben: boolean;
  readonly oeffnungen: readonly OeffnungZeile[];
}

/** Eine Kante, wie sie gezeichnet wird. */
interface KanteSicht {
  readonly index: number;
  readonly nr: number;
  readonly x1: number;
  readonly y1: number;
  readonly x2: number;
  readonly y2: number;
  readonly mx: number;
  readonly my: number;
  readonly fehlerhaft: boolean;
  readonly hatWand: boolean;
  readonly text: string;
  readonly titel: string;
}

/** Eine Öffnung, maßstäblich IN ihrer Kante. */
interface OeffnungSicht {
  readonly uid: string;
  readonly x1: number;
  readonly y1: number;
  readonly x2: number;
  readonly y2: number;
  readonly tx: number;
  readonly ty: number;
  readonly istTuer: boolean;
  readonly kuerzel: string;
  readonly titel: string;
  readonly bogen: string;
}

/** Laufende Nummer für eindeutige DOM-IDs (zwei Editoren auf einer Seite). */
let editorSeq = 0;

const RASTER: readonly { wert: number; label: string }[] = [
  { wert: 100, label: '10 cm' },
  { wert: 250, label: '25 cm' },
  { wert: 500, label: '50 cm' },
  { wert: 1000, label: '1 m' },
  { wert: 0, label: 'frei (mm)' },
];

/**
 * **Der zeichenbare Grundriss.** Bis hierher war das Aufmaß eine Zahlenliste;
 * jetzt zeichnet man den Raum und die Zahlen fallen heraus.
 *
 * VIER DINGE, DIE HIER NICHT VERHANDELBAR SIND:
 *
 * 1. **Millimeter, ganzzahlig, im System des Geschosses.** Es wird an genau einer
 *    Stelle gerundet (`mmAusMeter`) — nicht bei jedem Zwischenschritt.
 *
 * 2. **Kantenlängen tippt man.** Der Handwerker misst mit dem Laser 4,37 m und
 *    trägt 4,37 ein; der Endpunkt wandert entlang der Kantenrichtung, die
 *    Folgepunkte gehen mit. Das ist der Unterschied zwischen einem Aufmaß und
 *    einer Malerei. Die **schließende** Kante ist abgeleitet — sie ergibt sich
 *    daraus, dass der Umriss zugeht.
 *
 * 3. **Die Kantenliste IST die Zeichnung** — in Text. Wer nicht zeichnen kann
 *    (Tastatur, Screenreader), erfasst den Raum vollständig über sie: jede Länge,
 *    jede Wand, jede Öffnung ist dort erreichbar und editierbar.
 *
 * 4. **Fehlende Lage ist unbekannt, nicht 0.** Eine Öffnung ohne `position_m`
 *    wird NICHT gezeichnet und NICHT bei 0 platziert — sie steht in der Liste
 *    „ohne Lage in der Wand" und zählt trotzdem in Fläche und Heizlast.
 *
 * Alles, was hier gerechnet wird, ist **Vorschau**. Verbindlich ist der Raum, den
 * der Server nach dem Speichern zurückgibt.
 */
@Component({
  selector: 'app-grundriss-editor',
  templateUrl: './grundriss-editor.html',
  styleUrl: './grundriss-editor.scss',
})
export class GrundrissEditor {
  private readonly svc = inject(RaumService);

  readonly raum = input.required<Room>();
  readonly huellen = input<readonly Huelle[]>([]);
  readonly oeffnungen = input<readonly Oeffnung[]>([]);
  /** Raumhöhe in deutscher Eingabeform — Grundlage der abgeleiteten Wandflächen. */
  readonly raumHoehe = input('');
  readonly darfAendern = input(false);
  /** Hat der Aufbau ungespeicherte Änderungen? Dann sperrt „Umriss speichern". */
  readonly aufbauGeaendert = input(false);

  readonly huelleSetzen = output<{ uid: string; patch: Partial<Huelle> }>();
  readonly huelleAnKante = output<{ edge_index: number }>();
  readonly oeffnungSetzen = output<{ uid: string; patch: Partial<Oeffnung> }>();
  readonly oeffnungAnWand = output<string>();
  readonly aufbauSpeichern = output<void>();
  readonly entfernenAngefordert = output<void>();
  readonly gespeichert = output<Room>();

  protected readonly rasterWahl = RASTER;

  // --- Zustand -------------------------------------------------------------
  /** Der Arbeitsstand des Umrisses. Ganze Millimeter, immer. */
  protected readonly punkte = signal<Punkt[]>([]);
  protected readonly raster = signal(250);
  protected readonly sicht = signal<Sicht>(sichtEinpassen([]));
  protected readonly zeichnet = signal(false);
  protected readonly ziehtPunkt = signal<number | null>(null);
  protected readonly aktivePunkt = signal<number | null>(null);
  protected readonly geaendert = signal(false);
  protected readonly speichert = signal(false);
  protected readonly fehler = signal<string | null>(null);
  protected readonly ansage = signal('');
  /** Eingabepuffer der Kantenlänge — sonst überschriebe die Anzeige das Tippen. */
  protected readonly kantenEntwurf = signal<{ index: number; text: string } | null>(null);
  /** Eingabepuffer der Öffnungslage. */
  protected readonly lageEntwurf = signal<{ uid: string; text: string } | null>(null);

  /** Eindeutige IDs — zwei Editoren nebeneinander dürfen sich nicht ins Gehege kommen. */
  protected readonly ids = `gr${++editorSeq}`;

  constructor() {
    // Der gespeicherte Umriss ist die Wahrheit — ABER ein ungespeicherter
    // Arbeitsstand darf nicht verloren gehen, nur weil der Aufbau nebenan
    // gespeichert wurde (dann kommt der Raum frisch vom Server zurück).
    effect(() => {
      const r = this.raum();
      const vom_server: Punkt[] = [...(r.vertices ?? [])]
        .sort((a, b) => a.idx - b.idx)
        .map((v) => ({ x_mm: v.x_mm, y_mm: v.y_mm }));
      if (this.geaendert()) return;
      this.punkte.set(vom_server);
      this.sicht.set(sichtEinpassen(vom_server));
      this.zeichnet.set(vom_server.length === 0);
      this.fehler.set(null);
    });
  }

  // --- Rechnung (Vorschau!) -------------------------------------------------
  protected readonly kantenListe = computed<Kante[]>(() => kanten(this.punkte()));
  protected readonly befunde = computed<Befund[]>(() => pruefe(this.punkte()));
  protected readonly gueltig = computed(() => this.befunde().length === 0);
  protected readonly flaeche = computed(() => flaecheM2(this.punkte()));
  protected readonly umfang = computed(() => umfangM(this.punkte()));

  private readonly fehlerKanten = computed(() => {
    const s = new Set<number>();
    for (const b of this.befunde()) for (const k of b.kanten) s.add(k);
    return s;
  });
  private readonly fehlerPunkte = computed(() => {
    const s = new Set<number>();
    for (const b of this.befunde()) for (const p of b.punkte) s.add(p);
    return s;
  });

  /** Raumhöhe als Zahl — ohne sie gibt es keine abgeleitete Wandfläche. */
  protected readonly hoeheM = computed(() => {
    const e = eingabe(this.raumHoehe());
    return e.art === 'wert' && e.zahl > 0 ? e.zahl : null;
  });

  /** Die Wand auf Kante `i` (höchstens eine — die DB verhindert zwei). */
  private huelleAuf(i: number): Huelle | null {
    return this.huellen().find((h) => h.edge_index === i) ?? null;
  }

  protected readonly zeilen = computed<KantenZeile[]>(() => {
    const ks = this.kantenListe();
    const n = this.punkte().length;
    const entwurf = this.kantenEntwurf();
    const fehlerhaft = this.fehlerKanten();
    const hoehe = this.hoeheM();
    return ks.map((k) => {
      const h = this.huelleAuf(k.index);
      const laengeM = meterAusMm(k.laenge_mm);
      const abgeleitet = hoehe != null ? abgeleiteteWandflaeche(laengeM, hoehe) : null;
      const brutto = h ? this.zahl(h.brutto) : null;
      return {
        index: k.index,
        nr: k.index + 1,
        schliessend: istSchliessendeKante(k.index, n),
        laengeM,
        feld:
          entwurf?.index === k.index
            ? entwurf.text
            : laengeM > 0
              ? laengeM.toFixed(2).replace('.', ',')
              : '',
        fehlerhaft: fehlerhaft.has(k.index),
        huelle: h,
        kuerzel: h ? ART_KURZ[h.surface_type] : '',
        nachbar: h ? (NACHBAR_KURZ[h.adjacent] ?? h.adjacent) : '',
        richtung: h?.orientation ? orientationLabel(h.orientation as Orientation) : '',
        abgeleitetM2: abgeleitet,
        // Die Fläche, die gilt: die eingetragene — sonst die abgeleitete.
        bruttoM2: brutto ?? (h ? abgeleitet : null),
        // LEER auf einer Kante = abgeleitet (der Server rechnet sie und hält sie
        // aktuell). GEFÜLLT = jemand hat sie ausdrücklich eingetragen, und dann
        // wird sie nie wieder nachgerechnet — das ist die „abweichende" Angabe.
        ueberschrieben: h != null && h.brutto.trim() !== '',
        oeffnungen: h ? this.oeffnungenZu(h, laengeM) : [],
      };
    });
  });

  private oeffnungenZu(h: Huelle, kanteM: number): OeffnungZeile[] {
    const entwurf = this.lageEntwurf();
    return this.oeffnungen()
      .filter((o) => o.surfaceRef === h.uid)
      .map((o, i) => {
        const pos = this.zahl(o.position);
        const breite = this.zahl(o.breite);
        const anzahl = Number(o.anzahl) || 1;
        return {
          uid: o.uid,
          name: o.label.trim() || `${openingTypeLabel(o.opening_type)} ${i + 1}`,
          art: o.opening_type,
          istTuer: o.opening_type === 'TUER_AUSSEN' || o.opening_type === 'TUER_INNEN',
          anzahl,
          breiteM: breite,
          positionM: pos,
          feld: entwurf?.uid === o.uid ? entwurf.text : o.position,
          passung: oeffnungPasst(pos, breite, kanteM),
        };
      });
  }

  /**
   * Öffnungen **ohne Lage in der Wand** — sie werden NICHT gezeichnet und NICHT
   * bei 0 platziert. Sie zählen trotzdem in Fläche und Heizlast; sie stehen hier,
   * damit sie nicht verschwinden.
   */
  protected readonly ohneLage = computed(() => {
    const anKante = new Set(
      this.huellen()
        .filter((h) => h.edge_index != null)
        .map((h) => h.uid),
    );
    return this.oeffnungen()
      .filter(
        (o) => o.surfaceRef != null && anKante.has(o.surfaceRef) && this.zahl(o.position) == null,
      )
      .map((o, i) => ({
        uid: o.uid,
        name: o.label.trim() || `${openingTypeLabel(o.opening_type)} ${i + 1}`,
        wand: this.huellen().find((h) => h.uid === o.surfaceRef)?.label || 'Wand',
      }));
  });

  /** Öffnungen an einer Wand OHNE Kante — die lassen sich prinzipiell nicht zeichnen. */
  protected readonly ohneKante = computed(() => {
    const ohne = new Set(
      this.huellen()
        .filter((h) => h.edge_index == null)
        .map((h) => h.uid),
    );
    return this.oeffnungen().filter((o) => o.surfaceRef == null || ohne.has(o.surfaceRef)).length;
  });

  /** Wände, deren Kante es im ARBEITSSTAND nicht mehr gibt (Punkte gelöscht). */
  protected readonly verwaisteWaende = computed(() => {
    const n = this.punkte().length;
    const grenze = n >= 3 ? n : 0;
    return this.huellen().filter((h) => h.edge_index != null && h.edge_index >= grenze);
  });

  // --- Zeichnung ------------------------------------------------------------
  protected readonly punkteSicht = computed(() =>
    this.punkte().map((p, i) => {
      const v = zuSicht(p, this.sicht());
      return {
        i,
        nr: i + 1,
        x: v.x,
        y: v.y,
        fehlerhaft: this.fehlerPunkte().has(i),
        titel: `Punkt ${i + 1}: ${zeige(meterAusMm(p.x_mm))} m / ${zeige(meterAusMm(p.y_mm))} m`,
      };
    }),
  );

  protected readonly polygon = computed(() =>
    this.punkteSicht()
      .map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`)
      .join(' '),
  );

  protected readonly kantenSicht = computed<KanteSicht[]>(() =>
    this.kantenListe().map((k) => {
      const a = zuSicht(k.von, this.sicht());
      const b = zuSicht(k.bis, this.sicht());
      const z = this.zeilen()[k.index];
      const teile = [`Kante ${k.index + 1}`, `${zeige(meterAusMm(k.laenge_mm))} m`];
      if (z?.huelle) {
        teile.push(`${surfaceTypeLabel(z.huelle.surface_type)} gegen ${z.nachbar}`);
        if (z.richtung) teile.push(z.richtung);
      } else {
        teile.push('keine Wand zugeordnet');
      }
      return {
        index: k.index,
        nr: k.index + 1,
        x1: a.x,
        y1: a.y,
        x2: b.x,
        y2: b.y,
        mx: (a.x + b.x) / 2,
        my: (a.y + b.y) / 2,
        fehlerhaft: this.fehlerKanten().has(k.index),
        hatWand: z?.huelle != null,
        text: `${k.index + 1} · ${zeige(meterAusMm(k.laenge_mm))} m${z?.kuerzel ? ` · ${z.kuerzel}` : ''}`,
        titel: teile.join(', '),
      };
    }),
  );

  /** Nur Öffnungen MIT Lage werden gezeichnet — und nur, wenn sie in die Kante passen. */
  protected readonly oeffnungenSicht = computed<OeffnungSicht[]>(() => {
    const s = this.sicht();
    const out: OeffnungSicht[] = [];
    for (const z of this.zeilen()) {
      const k = this.kantenListe()[z.index];
      if (!k) continue;
      for (const o of z.oeffnungen) {
        if (o.positionM == null || o.breiteM == null || o.passung.art !== 'passt') continue;
        const a = zuSicht(punktAufKante(k, o.positionM), s);
        const b = zuSicht(punktAufKante(k, o.positionM + o.breiteM), s);
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const len = Math.hypot(dx, dy) || 1;
        // Der Bogen der Tür — das ist die FORM, nicht die Farbe, die sie kenntlich macht.
        const r = Math.min(len, 26);
        const bogen = o.istTuer
          ? `M ${b.x.toFixed(1)} ${b.y.toFixed(1)} L ${(b.x - (dx / len) * r).toFixed(1)} ${(
              b.y -
              (dy / len) * r
            ).toFixed(1)} A ${r.toFixed(1)} ${r.toFixed(1)} 0 0 1 ${(b.x + (dy / len) * r).toFixed(
              1,
            )} ${(b.y - (dx / len) * r).toFixed(1)}`
          : '';
        out.push({
          uid: o.uid,
          x1: a.x,
          y1: a.y,
          x2: b.x,
          y2: b.y,
          tx: (a.x + b.x) / 2 + (dy / len) * 16,
          ty: (a.y + b.y) / 2 - (dx / len) * 16,
          istTuer: o.istTuer,
          kuerzel: o.istTuer ? 'T' : 'F',
          titel:
            `${o.name}: ${o.istTuer ? 'Tür' : 'Fenster'}, ${zeige(o.breiteM)} m breit, ` +
            `${zeige(o.positionM)} m ab Punkt ${z.index + 1} (Kante ${z.nr})` +
            // Die Lage ist EINE Angabe je Zeile — bei mehreren gleichen Öffnungen
            // wird deshalb nur eine gezeichnet. Das steht hier, statt still zu bleiben.
            (o.anzahl > 1
              ? `. ${o.anzahl} Stück erfasst; die Lage gilt für eine — nur diese wird gezeichnet.`
              : ''),
          bogen,
        });
      }
    }
    return out;
  });

  /** Rasterweite in SVG-Einheiten — zu feine Raster werden zusammengefasst. */
  protected readonly rasterSicht = computed(() => {
    const r = this.raster() > 0 ? this.raster() : 1000;
    let g = r * this.sicht().skala;
    while (g > 0 && g < 8) g *= 2;
    return g;
  });

  protected readonly massstab = computed(() => {
    const r = this.raster() > 0 ? this.raster() : 1000;
    let mm = r;
    let g = r * this.sicht().skala;
    while (g > 0 && g < 8) {
      g *= 2;
      mm *= 2;
    }
    return mm >= 1000 ? `${zeige(mm / 1000, 0)} m` : `${zeige(mm / 10, 0)} cm`;
  });

  /**
   * Der Nordpfeil wird aus den **Ausrichtungen der Wände** abgeleitet (die
   * Zeichnung selbst kennt keinen Norden). Widersprechen sie sich, wird KEIN
   * Pfeil gezeigt — ein erfundener Nordpfeil wäre schlimmer als keiner.
   */
  protected readonly nord = computed<Nord>(() => {
    const n = this.punkte().length;
    const je: (Orientation | null)[] = [];
    for (let i = 0; i < n; i++) {
      const h = this.huelleAuf(i);
      je.push((h?.orientation as Orientation) || null);
    }
    return nordRichtung(this.punkte(), je);
  });

  protected readonly nordPfeil = computed(() => {
    const n = this.nord();
    if (n.art !== 'richtung') return null;
    // Welt-y zeigt nach oben, SVG-y nach unten → spiegeln.
    const laenge = 34;
    return {
      x2: 60 + n.x * laenge,
      y2: 62 - n.y * laenge,
      tx: 60 + n.x * (laenge + 14),
      ty: 62 - n.y * (laenge + 14),
    };
  });

  protected readonly ausserhalb = computed(() =>
    this.punkte().some((p) => !inSicht(p, this.sicht())),
  );

  /** Die Zeichnung **als Satz** — das, was ein Screenreader vorliest. */
  protected readonly text = computed(() =>
    beschreibung(this.punkte(), {
      waende: this.zeilen().filter((z) => z.huelle).length,
      oeffnungen: this.oeffnungenSicht().length,
      ohneLage: this.ohneLage().length,
    }),
  );

  // --- Bedienung: Zeichenfläche --------------------------------------------
  private welt(ev: PointerEvent | MouseEvent, ziel: Element): Punkt {
    const r = ziel.getBoundingClientRect();
    const s = this.sicht();
    const x = ((ev.clientX - r.left) / Math.max(r.width, 1)) * s.breite;
    const y = ((ev.clientY - r.top) / Math.max(r.height, 1)) * s.hoehe;
    return snapPunkt(zuWelt(x, y, s), this.raster());
  }

  flaecheKlick(ev: PointerEvent, svg: Element): void {
    if (!this.zeichnet() || !this.darfAendern() || this.ziehtPunkt() != null) return;
    const p = this.welt(ev, svg);
    const punkte = this.punkte();
    if (punkte.some((q) => q.x_mm === p.x_mm && q.y_mm === p.y_mm)) {
      this.fehler.set(
        'Dort liegt bereits ein Punkt. Zwei Punkte dürfen nicht aufeinanderliegen — ' +
          'das ergäbe eine Kante ohne Länge.',
      );
      return;
    }
    this.fehler.set(null);
    this.aendern([...punkte, p]);
    this.ansage.set(
      `Punkt ${punkte.length + 1} gesetzt: ${zeige(meterAusMm(p.x_mm))} m / ${zeige(meterAusMm(p.y_mm))} m.`,
    );
  }

  punktGreifen(ev: PointerEvent, i: number): void {
    if (!this.darfAendern()) return;
    ev.stopPropagation();
    ev.preventDefault();
    this.ziehtPunkt.set(i);
    this.aktivePunkt.set(i);
    (ev.target as Element).setPointerCapture?.(ev.pointerId);
  }

  punktZiehen(ev: PointerEvent, svg: Element): void {
    const i = this.ziehtPunkt();
    if (i == null) return;
    const p = this.welt(ev, svg);
    this.aendern(punktSetzen(this.punkte(), i, p));
  }

  punktLoslassen(): void {
    const i = this.ziehtPunkt();
    if (i == null) return;
    this.ziehtPunkt.set(null);
    const p = this.punkte()[i];
    if (p) {
      this.ansage.set(
        `Punkt ${i + 1} liegt bei ${zeige(meterAusMm(p.x_mm))} m / ${zeige(meterAusMm(p.y_mm))} m.`,
      );
    }
  }

  /**
   * **Alles, was mit der Maus geht, geht mit der Tastatur.** Pfeil = ein
   * Rasterschritt, Shift+Pfeil = 10 mm (fein). Entf löscht, Enter springt in die
   * Kantenliste, wo sich die Länge exakt eintippen lässt.
   */
  punktTaste(ev: KeyboardEvent, i: number): void {
    if (!this.darfAendern()) return;
    const gross = this.raster() > 0 ? this.raster() : 100;
    const schritt = ev.shiftKey ? 10 : gross;
    let dx = 0;
    let dy = 0;
    switch (ev.key) {
      case 'ArrowLeft':
        dx = -schritt;
        break;
      case 'ArrowRight':
        dx = schritt;
        break;
      case 'ArrowUp':
        dy = schritt; // Welt-y zeigt nach oben.
        break;
      case 'ArrowDown':
        dy = -schritt;
        break;
      case 'Delete':
      case 'Backspace':
        ev.preventDefault();
        this.punktWeg(i);
        return;
      case 'Enter':
        ev.preventDefault();
        this.fokus(`${this.ids}-kante-${i}`);
        return;
      default:
        return;
    }
    ev.preventDefault();
    this.aendern(punktVerschieben(this.punkte(), i, dx, dy));
    const p = this.punkte()[i];
    this.ansage.set(
      `Punkt ${i + 1}: ${zeige(meterAusMm(p.x_mm))} m / ${zeige(meterAusMm(p.y_mm))} m.`,
    );
  }

  punktWeg(i: number): void {
    if (!this.darfAendern()) return;
    const vorher = this.punkte().length;
    this.aendern(punktLoeschen(this.punkte(), i));
    this.ansage.set(`Punkt ${i + 1} gelöscht. Der Umriss hat noch ${vorher - 1} Punkte.`);
    this.aktivePunkt.set(null);
    this.fokus(`${this.ids}-punkt-${Math.max(0, i - 1)}`, `${this.ids}-punkt-plus`);
  }

  punktNeuAufKante(i: number): void {
    if (!this.darfAendern()) return;
    this.aendern(punktEinfuegen(this.punkte(), i));
    this.ansage.set(`Neuer Punkt ${i + 2} in der Mitte von Kante ${i + 1} eingefügt.`);
    this.fokus(`${this.ids}-punkt-${i + 1}`);
  }

  /** Punkt über die LISTE anlegen — der Weg ohne Maus. */
  punktAnhaengen(): void {
    if (!this.darfAendern()) return;
    const ps = this.punkte();
    const schritt = this.raster() > 0 ? this.raster() * 4 : 1000;
    const letzter = ps[ps.length - 1];
    const p: Punkt = letzter
      ? { x_mm: letzter.x_mm + schritt, y_mm: letzter.y_mm }
      : { x_mm: 0, y_mm: 0 };
    this.aendern([...ps, p]);
    if (ps.length === 0) this.sicht.set(sichtEinpassen([p]));
    this.ansage.set(`Punkt ${ps.length + 1} angehängt. Jetzt die Kantenlängen eintragen.`);
    this.fokus(`${this.ids}-punkt-${ps.length}`);
  }

  private aendern(punkte: Punkt[]): void {
    this.punkte.set(punkte);
    this.geaendert.set(true);
    this.fehler.set(null);
    if (punkte.length === 1) this.sicht.set(sichtEinpassen(punkte));
  }

  einpassen(): void {
    this.sicht.set(sichtEinpassen(this.punkte()));
    this.ansage.set('Ansicht eingepasst.');
  }

  zeichnenUm(an: boolean): void {
    this.zeichnet.set(an);
    this.ansage.set(
      an
        ? 'Zeichnen an: Klicken setzt Punkte auf das Raster.'
        : 'Umriss geschlossen. Die letzte Kante schließt zurück auf Punkt 1.',
    );
  }

  rasterSetzen(mm: string): void {
    this.raster.set(Number(mm) || 0);
  }

  // --- Bedienung: Kantenlänge exakt eintippen -------------------------------
  kanteTippen(index: number, text: string): void {
    this.kantenEntwurf.set({ index, text });
  }

  /**
   * Die getippte Länge übernehmen: Der Endpunkt der Kante wandert **entlang ihrer
   * Richtung**, die Folgepunkte gehen mit. Meter kommen herein, ganze Millimeter
   * gehen hinaus — **einmal** gerundet.
   */
  kanteUebernehmen(index: number): void {
    const entwurf = this.kantenEntwurf();
    this.kantenEntwurf.set(null);
    if (!entwurf || entwurf.index !== index || !this.darfAendern()) return;

    const zeile = this.zeilen()[index];
    if (zeile?.schliessend) {
      this.fehler.set(
        `Kante ${index + 1} schließt den Umriss — ihre Länge ergibt sich aus den anderen ` +
          'Kanten und lässt sich nicht direkt eintragen. Bitte eine der übrigen Kanten ändern.',
      );
      return;
    }
    const e = eingabe(entwurf.text);
    if (e.art === 'leer') return;
    if (e.art === 'fehler') {
      this.fehler.set(
        `Kante ${index + 1}: „${entwurf.text}" ist nicht eindeutig. Bitte ohne Tausenderpunkt ` +
          'eingeben (z. B. 4,37 für 4,37 m).',
      );
      return;
    }
    if (!(e.zahl > 0)) {
      this.fehler.set(`Kante ${index + 1}: Die Länge muss größer als 0 sein.`);
      return;
    }
    const neu = kanteLaengeSetzen(this.punkte(), index, mmAusMeter(e.zahl));
    if (!neu) {
      this.fehler.set(`Kante ${index + 1} lässt sich nicht auf diese Länge setzen.`);
      return;
    }
    this.aendern(neu);
    this.ansage.set(`Kante ${index + 1} auf ${zeige(e.zahl)} m gesetzt.`);
  }

  // --- Bedienung: Wand an der Kante ----------------------------------------
  /**
   * Der Kante eine Wand zuordnen. Ihre Bruttofläche bleibt **leer** und wird damit
   * vom **Server** gerechnet (Kantenlänge × Raumhöhe) — und bei jeder Änderung von
   * Umriss oder Raumhöhe **neu** gerechnet.
   *
   * Sie hier mit dem vorgerechneten Wert zu belegen wäre bequem und falsch: Die
   * Wand wäre ab dem ersten Speichern Handeingabe und bliebe bei einer späteren
   * Höhenkorrektur still auf dem alten Wert stehen.
   */
  wandAnlegen(index: number): void {
    if (!this.darfAendern() || !this.kanteBelegbar()) return;
    const z = this.zeilen()[index];
    if (!z || z.huelle) return;
    this.huelleAnKante.emit({ edge_index: index });
    this.ansage.set(
      `Wand an Kante ${index + 1} angelegt. Ihre Bruttofläche rechnet der Server aus der ` +
        'Kante (Kantenlänge × Raumhöhe) und hält sie aktuell.',
    );
  }

  /**
   * „Fläche abweichend eintragen" (Giebel, Erker): Der abgeleitete Wert wird ins
   * Feld übernommen — **ab jetzt ist es eine Handeingabe**, und der Server rechnet
   * sie nicht mehr nach.
   */
  wandUebersteuern(index: number): void {
    const z = this.zeilen()[index];
    if (!z?.huelle || !this.darfAendern()) return;
    const wert = z.abgeleitetM2 == null ? '' : this.feld(z.abgeleitetM2);
    this.huelleSetzen.emit({ uid: z.huelle.uid, patch: { brutto: wert } });
    this.ansage.set(
      `Die Fläche von Kante ${index + 1} ist jetzt Handeingabe. Eine spätere Änderung von ` +
        'Umriss oder Raumhöhe zieht sie NICHT mehr nach. Im Aufbau (Schritt 3) eintragen.',
    );
  }

  /**
   * „Zurück auf berechnet": Das Feld wird **geleert** — damit schickt das nächste
   * Speichern keine Fläche mehr, und der Server rechnet sie wieder selbst.
   */
  wandAbleiten(index: number): void {
    const z = this.zeilen()[index];
    if (!z?.huelle || !this.darfAendern()) return;
    this.huelleSetzen.emit({ uid: z.huelle.uid, patch: { brutto: '' } });
    this.ansage.set(
      `Die Fläche von Kante ${index + 1} wird wieder aus der Kante berechnet` +
        (z.abgeleitetM2 == null ? '.' : `: ${zeige(z.abgeleitetM2)} m².`) +
        ' Sie bleibt damit automatisch aktuell.',
    );
  }

  wandLoesen(index: number): void {
    const z = this.zeilen()[index];
    if (!z?.huelle || !this.darfAendern()) return;
    this.huelleSetzen.emit({ uid: z.huelle.uid, patch: { edge_index: null } });
    this.ansage.set(
      `Die Wand ist nicht mehr Kante ${index + 1} zugeordnet. Sie bleibt im Aufbau stehen ` +
        '(sie zählt weiter in die Heizlast) — sie wird nur nicht mehr gezeichnet.',
    );
  }

  fensterAnKante(index: number): void {
    const z = this.zeilen()[index];
    if (!z?.huelle || !this.darfAendern()) return;
    this.oeffnungAnWand.emit(z.huelle.uid);
    this.ansage.set(`Öffnung an Kante ${index + 1} angelegt. Lage und Breite eintragen.`);
  }

  // --- Bedienung: Lage der Öffnung -----------------------------------------
  lageTippen(uid: string, text: string): void {
    this.lageEntwurf.set({ uid, text });
  }

  /**
   * Die Lage der Öffnung übernehmen. **Leer bleibt leer** — es wird NIE eine 0
   * daraus (unbestimmt ist nicht null; die Öffnung wandert dann in die Liste
   * „ohne Lage in der Wand" und zählt trotzdem in Fläche und Heizlast).
   */
  lageUebernehmen(uid: string): void {
    const entwurf = this.lageEntwurf();
    this.lageEntwurf.set(null);
    if (!entwurf || entwurf.uid !== uid || !this.darfAendern()) return;
    const e = eingabe(entwurf.text);
    if (e.art === 'fehler') {
      this.fehler.set(
        `Die Lage „${entwurf.text}" ist nicht eindeutig. Bitte ohne Tausenderpunkt eingeben ` +
          '(z. B. 1,20 für 1,20 m ab dem Anfangspunkt der Kante).',
      );
      return;
    }
    if (e.art === 'wert' && e.zahl < 0) {
      this.fehler.set(
        'Die Lage darf nicht negativ sein — sie zählt ab dem Anfangspunkt der Kante.',
      );
      return;
    }
    // Leer bleibt LEER — die Lage ist dann unbekannt, nicht 0.
    this.oeffnungSetzen.emit({
      uid,
      patch: { position: e.art === 'leer' ? '' : this.feld(e.zahl) },
    });
    this.ansage.set(
      e.art === 'leer'
        ? 'Lage entfernt. Die Öffnung ist damit ohne Lage in der Wand — sie zählt weiter mit, ' +
            'wird aber nicht gezeichnet.'
        : `Lage auf ${zeige(e.zahl)} m ab dem Anfangspunkt der Kante gesetzt.`,
    );
  }

  lageEintragen(uid: string): void {
    this.fokus(`${this.ids}-lage-${uid}`);
  }

  // --- Speichern ------------------------------------------------------------
  /** Kantenzuordnungen erst NACH dem Speichern — sonst kennt der Server die Kante nicht. */
  protected readonly kanteBelegbar = computed(() => !this.geaendert() && this.punkte().length >= 3);

  protected readonly speicherSperre = computed<string | null>(() => {
    if (!this.darfAendern()) return 'Zum Speichern fehlt das Recht am Objektregister.';
    if (this.aufbauGeaendert()) {
      return (
        'Der Aufbau hat ungespeicherte Änderungen. Das Speichern des Umrisses holt den Raum ' +
        'frisch vom Server — dabei gingen sie verloren. Bitte zuerst den Aufbau speichern.'
      );
    }
    // Kein Punkt = kein Umriss. Ein leeres Array WÜRDE den Umriss entfernen — aber
    // nicht so nebenbei über den Speichern-Knopf: dafür gibt es „Umriss entfernen",
    // mit Rückfrage.
    if (!this.punkte().length) {
      return this.raum().vertices?.length
        ? 'Es ist kein Punkt mehr gesetzt. Zum Löschen des Umrisses bitte „Umriss entfernen" ' +
            'benutzen — das fragt nach und sagt, was danach gilt.'
        : 'Noch kein Punkt gesetzt. Ein Umriss braucht mindestens 3 Punkte.';
    }
    if (!this.gueltig()) {
      return 'Der Umriss hat noch Fehler (siehe oben). Der Server würde ihn ablehnen.';
    }
    return null;
  });

  speichern(): void {
    if (this.speichert() || this.speicherSperre()) return;
    const punkte = this.punkte();
    if (punkte.length < 3) return;
    this.speichert.set(true);
    this.fehler.set(null);
    this.svc
      .setGrundriss(
        this.raum().id,
        punkte.map((p) => ({ x_mm: p.x_mm, y_mm: p.y_mm })),
      )
      .subscribe({
        next: (r) => {
          this.speichert.set(false);
          // Erst zurücknehmen, DANN melden: der Effect oben übernimmt den
          // Server-Umriss nur, wenn kein Arbeitsstand mehr offen ist.
          this.geaendert.set(false);
          this.zeichnet.set(false);
          this.ansage.set(
            'Umriss gespeichert. Fläche und Umfang kommen ab jetzt aus der Zeichnung — ' +
              'gerechnet hat sie der Server.',
          );
          this.gespeichert.emit(r);
        },
        error: (err) => {
          this.speichert.set(false);
          this.fehler.set(
            fehlerDetail(err) ??
              'Der Umriss konnte nicht gespeichert werden. Der Server lehnt ihn ab, wenn er ' +
                'weniger als 3 Punkte hat, ein Punkt doppelt vorkommt, die Fläche 0 ist oder ' +
                'er sich selbst schneidet.',
          );
        },
      });
  }

  verwerfen(): void {
    this.geaendert.set(false);
    const r = this.raum();
    const vom_server: Punkt[] = [...(r.vertices ?? [])]
      .sort((a, b) => a.idx - b.idx)
      .map((v) => ({ x_mm: v.x_mm, y_mm: v.y_mm }));
    this.punkte.set(vom_server);
    this.sicht.set(sichtEinpassen(vom_server));
    this.zeichnet.set(vom_server.length === 0);
    this.fehler.set(null);
    this.ansage.set('Arbeitsstand verworfen. Es gilt wieder der gespeicherte Umriss.');
  }

  // --- Hilfen ---------------------------------------------------------------
  /** Deutsche Eingabe → Zahl. Mehrdeutiges („1.500") ergibt null, statt geraten zu werden. */
  private zahl(roh: string | null | undefined): number | null {
    const e = eingabe(roh);
    return e.art === 'wert' ? e.zahl : null;
  }

  /** Zahl → Eingabefeld (Komma, **ohne Tausenderpunkt** — sonst wäre sie mehrdeutig). */
  private feld(n: number): string {
    return n.toFixed(3).replace(/0+$/, '').replace(/\.$/, '').replace('.', ',');
  }

  private fokus(...ids: string[]): void {
    setTimeout(() => {
      for (const id of ids) {
        const el = document.getElementById(id);
        if (el) {
          el.focus();
          return;
        }
      }
    }, 0);
  }

  zeig(n: number | null, nachkomma = 2): string {
    return n == null ? '—' : zeige(n, nachkomma);
  }

  passungGrund(p: Passung): string | null {
    return p.art === 'passt_nicht' ? p.grund : null;
  }

  /** Nicht entscheidbar (die Breite fehlt noch) — das ist KEIN „passt". */
  passungOffen(p: Passung): string | null {
    return p.art === 'unbekannt' ? p.grund : null;
  }

  huelleName(h: Huelle): string {
    return h.label.trim() || surfaceTypeLabel(h.surface_type);
  }

  nachbarLabel(h: Huelle): string {
    return adjacentLabel(h.adjacent);
  }
}
