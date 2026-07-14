import { Component, computed, input, output, signal } from '@angular/core';
import { Room, istGezeichnet, istStillgelegt, roomTypeLabel } from '../../../core/raum.model';
import { zeige } from '../raum-rechnen';
import {
  Punkt,
  beschreibung,
  flaecheM2,
  kasten,
  meterAusMm,
  sichtEinpassen,
  umfangM,
  zuSicht,
} from './geometrie';

/** Laufende Nummer für eindeutige DOM-IDs. */
let planSeq = 0;

interface RaumPlan {
  readonly id: string;
  readonly name: string;
  readonly nutzung: string;
  readonly still: boolean;
  readonly polygon: string;
  readonly cx: number;
  readonly cy: number;
  readonly flaeche: number;
  readonly umfang: number;
  readonly titel: string;
}

/**
 * **Die Etagenübersicht ergibt sich von selbst.** Die Koordinaten des Umrisses
 * gelten je GESCHOSS, nicht je Raum (Modulkopf 0091) — alle Räume einer Etage
 * liegen also schon im selben Raster. Man zeichnet sie einfach alle.
 *
 * Räume **ohne** Umriss verschwinden hier nicht stillschweigend: Sie stehen
 * daneben in der Liste „noch nicht gezeichnet" — mit dem Weg dorthin. Ein Raum,
 * der auf einem Plan fehlt, ohne dass jemand es merkt, ist schlimmer als ein
 * unvollständiger Plan.
 */
@Component({
  selector: 'app-geschoss-plan',
  templateUrl: './geschoss-plan.html',
  styleUrl: './geschoss-plan.scss',
})
export class GeschossPlan {
  readonly label = input('');
  readonly raeume = input<readonly Room[]>([]);

  readonly raumOeffnen = output<string>();

  protected readonly ids = `gp${++planSeq}`;

  protected readonly hervor = signal<string | null>(null);

  /** Nur Räume MIT Umriss liegen auf der Fläche. */
  protected readonly gezeichnete = computed(() => this.raeume().filter((r) => istGezeichnet(r)));

  protected readonly ohneUmriss = computed(() =>
    this.raeume()
      .filter((r) => !istGezeichnet(r))
      .map((r) => ({
        id: r.id,
        name: r.name,
        nutzung: roomTypeLabel(r.room_type),
        still: istStillgelegt(r),
      })),
  );

  private punkteVon(r: Room): Punkt[] {
    return [...(r.vertices ?? [])]
      .sort((a, b) => a.idx - b.idx)
      .map((v) => ({ x_mm: v.x_mm, y_mm: v.y_mm }));
  }

  /** EINE Sicht für das ganze Geschoss — sonst lägen die Räume nicht zueinander. */
  protected readonly sicht = computed(() => {
    const alle: Punkt[] = [];
    for (const r of this.gezeichnete()) alle.push(...this.punkteVon(r));
    return sichtEinpassen(alle, 1200, 780, 60);
  });

  protected readonly plaene = computed<RaumPlan[]>(() =>
    this.gezeichnete().map((r) => {
      const punkte = this.punkteVon(r);
      const s = this.sicht();
      const sichtPunkte = punkte.map((p) => zuSicht(p, s));
      const k = kasten(punkte)!;
      const mitte = zuSicht({ x_mm: (k.min_x + k.max_x) / 2, y_mm: (k.min_y + k.max_y) / 2 }, s);
      const flaeche = flaecheM2(punkte);
      const umfang = umfangM(punkte);
      const still = istStillgelegt(r);
      return {
        id: r.id,
        name: r.name,
        nutzung: roomTypeLabel(r.room_type),
        still,
        polygon: sichtPunkte.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' '),
        cx: mitte.x,
        cy: mitte.y,
        flaeche,
        umfang,
        titel:
          `${r.name} (${roomTypeLabel(r.room_type)})${still ? ', stillgelegt' : ''}: ` +
          `${zeige(flaeche)} m², ${zeige(umfang)} m Umfang. ` +
          beschreibung(punkte),
      };
    }),
  );

  /** Die Ausdehnung der Etage — Text neben der Zeichnung, nicht nur ein Bild. */
  protected readonly ausdehnung = computed(() => {
    const alle: Punkt[] = [];
    for (const r of this.gezeichnete()) alle.push(...this.punkteVon(r));
    const k = kasten(alle);
    if (!k) return null;
    return {
      breite: meterAusMm(k.max_x - k.min_x),
      tiefe: meterAusMm(k.max_y - k.min_y),
    };
  });

  protected readonly flaecheSumme = computed(() =>
    this.plaene()
      .filter((p) => !p.still)
      .reduce((s, p) => s + p.flaeche, 0),
  );

  protected readonly text = computed(() => {
    const n = this.plaene().length;
    if (!n) return `Geschoss ${this.label()}: kein Raum gezeichnet.`;
    const a = this.ausdehnung();
    return (
      `Geschoss ${this.label()}: ${n} gezeichnete ${n === 1 ? 'Raum' : 'Räume'}` +
      (a ? `, Ausdehnung ${zeige(a.breite)} m × ${zeige(a.tiefe)} m` : '') +
      `, zusammen ${zeige(this.flaecheSumme())} m². ` +
      this.plaene()
        .map((p) => `${p.name}: ${zeige(p.flaeche)} m²`)
        .join('. ') +
      '.'
    );
  });

  oeffnen(id: string): void {
    this.raumOeffnen.emit(id);
  }

  taste(ev: KeyboardEvent, id: string): void {
    if (ev.key === 'Enter' || ev.key === ' ') {
      ev.preventDefault();
      this.oeffnen(id);
    }
  }

  zeig(n: number, nachkomma = 2): string {
    return zeige(n, nachkomma);
  }
}
