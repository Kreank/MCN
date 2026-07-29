import { Component, computed, effect, inject, input, output, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { GebaeudeansichtService } from '../../core/gebaeudeansicht.service';
import {
  Gebaeudeansicht as Ansicht,
  Haus,
  HausAnlage,
  HausEinheit,
  HausEtage,
  hausName,
} from '../../core/gebaeudeansicht.model';
import { artLabel, energieLabel, kwAnzeige, supplyLabel } from '../../core/anlage.model';
import { MieterRolle, einheitLabel, rolleLabel } from '../../core/belegung.model';
import { fehlerDetail } from '../../shared/http-fehler';

type Zustand = 'loading' | 'ready' | 'error';

/** Was gerade im Panel steht — eine Einheit oder eine Gebäudeanlage. */
type Auswahl =
  | { art: 'einheit'; haus: Haus; etage: HausEtage; einheit: HausEinheit }
  | { art: 'anlage'; haus: Haus; anlage: HausAnlage };

/**
 * **Die Liegenschaft als Haus** — Gebäude, Etagen, Wohnungen, Technik in einem
 * Bild.
 *
 * Der Befund, aus dem das entstand (Sascha, Praxistest): Etage, Belegung,
 * Einheitennummer und die Frage „zentral oder Etagentherme?" stehen heute in
 * drei verschiedenen Reitern. Wer am Telefon einen Schaden aufnimmt, klickt
 * zwischen ihnen hin und her und baut sich das Haus im Kopf zusammen. Das hier
 * ist dasselbe Haus — nur sichtbar, und mit mehreren Gebäuden nebeneinander
 * (Vorderhaus, Seitenflügel, Hinterhaus).
 *
 * **Es ist eine Darstellung, keine zweite Datenhaltung.** Jede Angabe kommt aus
 * der Tabelle, in der sie gepflegt wird; geändert wird weiterhin dort.
 *
 * **Die Grafik ist nicht die Information.** Jede Kachel ist ein Button mit
 * vollständiger Vorlesefassung, jeder Status steht als Text (nie nur als
 * Farbe), und die Etage trägt ihren erfassten Text — auch den, den niemand
 * deuten konnte. Der bekommt ein eigenes Band unten, statt geraten zu werden.
 */
@Component({
  selector: 'app-gebaeudeansicht',
  imports: [RouterLink],
  templateUrl: './gebaeudeansicht.html',
  // Zwei Blätter: Das Auswahl-Panel liegt getrennt, damit keines das
  // Stil-Budget (8 kB) reißt — auslagern statt Budget lockern.
  styleUrls: ['./gebaeudeansicht.scss', './panel.scss'],
})
export class Gebaeudeansicht {
  private readonly svc = inject(GebaeudeansichtService);

  readonly propertyId = input.required<string>();

  /** „Einheit bearbeiten" gehört in den Dialog, den die Mappe schon hat. */
  readonly einheitBearbeiten = output<string>();
  /**
   * Sprung in den Reiter Belegung. **Ohne** Einheiten-Id: Der Reiter kann
   * (noch) nicht auf eine Einheit scharfstellen, und ein Parameter, den niemand
   * auswertet, ist ein Versprechen, das die Ansicht nicht hält.
   */
  readonly zurBelegung = output<void>();
  /** Sprung in den Reiter Anlagen (zum Erfassen einer neuen Anlage). */
  readonly zuAnlagen = output<void>();

  protected readonly zustand = signal<Zustand>('loading');
  protected readonly daten = signal<Ansicht | null>(null);
  protected readonly fehler = signal<string | null>(null);
  protected readonly auswahl = signal<Auswahl | null>(null);
  protected readonly ansage = signal('');

  private ladeReq = 0;

  protected readonly haeuser = computed(() => this.daten()?.haeuser ?? []);

  protected readonly leer = computed(
    () => this.zustand() === 'ready' && this.haeuser().length === 0,
  );

  /** Summe über alle Häuser — die Kopfzeile der Ansicht. */
  protected readonly bilanz = computed(() => {
    const h = this.haeuser();
    return {
      haeuser: h.length,
      einheiten: h.reduce((s, x) => s + x.einheiten_gesamt, 0),
      belegt: h.reduce((s, x) => s + x.einheiten_belegt, 0),
      anlagen: h.reduce(
        (s, x) =>
          s +
          x.technik.length +
          x.etagen.reduce(
            (t, e) => t + e.einheiten.reduce((u, i) => u + i.anlagen.length, 0),
            0,
          ),
        0,
      ),
    };
  });

  /** Gibt es irgendwo eine zentrale Anlage? Dann heißt „Heizung kalt" Objekt. */
  protected readonly hatZentrale = computed(() =>
    this.haeuser().some(
      (h) =>
        h.technik.some((a) => a.supply_type === 'ZENTRAL') ||
        h.etagen.some((e) =>
          e.einheiten.some((u) => u.anlagen.some((a) => a.supply_type === 'ZENTRAL')),
        ),
    ),
  );

  constructor() {
    effect(() => {
      const id = this.propertyId();
      if (id) this.laden(id);
    });
  }

  private laden(propertyId: string): void {
    const rid = ++this.ladeReq;
    this.zustand.set('loading');
    this.fehler.set(null);
    this.svc.get(propertyId).subscribe({
      next: (d) => {
        if (rid !== this.ladeReq) return;
        this.daten.set(d);
        this.auswahl.set(null);
        this.zustand.set('ready');
      },
      error: (err) => {
        if (rid !== this.ladeReq) return;
        this.zustand.set('error');
        this.fehler.set(fehlerDetail(err) ?? 'Die Gebäudeansicht konnte nicht geladen werden.');
      },
    });
  }

  neuLaden(): void {
    this.laden(this.propertyId());
  }

  // --- Auswahl ---------------------------------------------------------------
  einheitWaehlen(haus: Haus, etage: HausEtage, einheit: HausEinheit): void {
    const a = this.auswahl();
    if (a?.art === 'einheit' && a.einheit.id === einheit.id) {
      this.auswahl.set(null);
      this.ansage.set('Auswahl aufgehoben.');
      return;
    }
    this.auswahl.set({ art: 'einheit', haus, etage, einheit });
    this.ansage.set(`${einheitLabel(einheit.unit_type)} ${einheit.unit_number} ausgewählt.`);
  }

  anlageWaehlen(haus: Haus, anlage: HausAnlage): void {
    const a = this.auswahl();
    if (a?.art === 'anlage' && a.anlage.id === anlage.id) {
      this.auswahl.set(null);
      return;
    }
    this.auswahl.set({ art: 'anlage', haus, anlage });
    this.ansage.set(`Anlage ${anlage.name} ausgewählt.`);
  }

  auswahlSchliessen(): void {
    this.auswahl.set(null);
  }

  protected istGewaehlt(einheit: HausEinheit): boolean {
    const a = this.auswahl();
    return a?.art === 'einheit' && a.einheit.id === einheit.id;
  }

  protected istAnlageGewaehlt(anlage: HausAnlage): boolean {
    const a = this.auswahl();
    return a?.art === 'anlage' && a.anlage.id === anlage.id;
  }

  // --- Beschriftungen --------------------------------------------------------
  protected readonly hausName = hausName;
  protected readonly einheitArt = einheitLabel;
  protected readonly anlageArt = artLabel;
  protected readonly energie = energieLabel;
  protected readonly kw = kwAnzeige;
  protected readonly versorgung = supplyLabel;

  protected rolle(r: string): string {
    return rolleLabel(r as MieterRolle);
  }

  /**
   * Der Belegungsstatus **als Text**. Farbe allein trägt nie eine Aussage
   * (WCAG 1.4.1) — und „nicht belegbar" ist etwas anderes als „leer stehend".
   */
  protected statusText(u: HausEinheit): string {
    if (!u.belegbar) return 'keine Belegung vorgesehen';
    if (!this.daten()?.belegung_sichtbar) return 'Belegung nicht sichtbar';
    return u.belegt ? 'bewohnt' : 'frei';
  }

  protected statusKlasse(u: HausEinheit): string {
    if (!u.belegbar) return 'we--technik';
    if (!this.daten()?.belegung_sichtbar) return 'we--unbekannt';
    return u.belegt ? 'we--belegt' : 'we--frei';
  }

  /** Erste:r Bewohner:in für die Kachel; der Rest steht im Panel. */
  protected erster(u: HausEinheit): string | null {
    return u.bewohner.length ? u.bewohner[0].display_name : null;
  }

  /**
   * Der Ort einer Einheit in Worten: der **erfasste** Etagentext, wenn es ihn
   * gibt („EG links"), sonst das Band. Das Band trägt nur die Etage — wer vor
   * der Tür steht, braucht auch die Lage.
   */
  protected ortText(etage: HausEtage, u: HausEinheit): string {
    if (u.etage_text) return u.etage_text;
    return u.lage ? `${etage.label} ${u.lage}` : etage.label;
  }

  /**
   * Die Vorlesefassung einer Kachel — sie muss allein tragen, was das Bild
   * zeigt: Wo, was, wer, welche Technik.
   */
  protected kachelAnsage(haus: Haus, etage: HausEtage, u: HausEinheit): string {
    const teile = [
      `${this.einheitArt(u.unit_type)} ${u.unit_number}`,
      this.ortText(etage, u),
      hausName(haus),
      this.statusText(u),
    ];
    if (u.bewohner.length) {
      teile.push(u.bewohner.map((b) => b.display_name).join(', '));
    }
    for (const a of u.anlagen) {
      teile.push(`${this.anlageArt(a.asset_type)}, ${this.versorgung(a.supply_type)}`);
    }
    return teile.join(', ');
  }

  /** Kurzzeichen für die Technik auf der Kachel — Text steht in der Ansage. */
  protected technikKuerzel(u: HausEinheit): string {
    return u.anlagen.length === 1 ? '1 Anlage' : `${u.anlagen.length} Anlagen`;
  }
}
