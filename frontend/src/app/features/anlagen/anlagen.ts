import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { AnlageService } from '../../core/anlage.service';
import {
  Anlage,
  AssetType,
  artLabel,
  energieLabel,
  istStillgelegt,
  kwAnzeige,
  supplyLabel,
} from '../../core/anlage.model';
import { Building } from '../../core/property.model';
import { MieterRolle, rolleLabel } from '../../core/belegung.model';
import { AuthService } from '../../core/auth.service';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { fehlerDetail } from '../../shared/http-fehler';
import { AnlageDialog } from './anlage-dialog';

type Zustand = 'loading' | 'ready' | 'error';

/** Anlagen einer Art zusammen — der Monteur denkt in „Heizung", nicht in Zeilen. */
interface Gruppe {
  readonly key: AssetType;
  readonly label: string;
  readonly anlagen: readonly Anlage[];
}

/**
 * Reiter „Anlagen" der Liegenschaftsmappe: Was steht technisch in diesem Objekt?
 *
 * Das ist die Antwort auf „Mieter meldet Heizkörper kalt" — steht hier eine
 * **zentrale** Anlage, ist es ein Objektproblem; steht in der Einheit eine
 * Therme, ein Wohnungsproblem. Die Liste zeigt das deshalb an erster Stelle, und
 * zwar **als Text**, nicht nur als Farbe.
 *
 * **Gelöscht wird nie**: Eine ausgebaute Anlage wird stillgelegt und bleibt
 * lesbar — die Aufträge von damals zeigen weiter auf sie.
 */
@Component({
  selector: 'app-anlagen',
  imports: [RouterLink, AnlageDialog, Bestaetigung],
  templateUrl: './anlagen.html',
  styleUrl: './anlagen.scss',
})
export class Anlagen {
  private readonly svc = inject(AnlageService);
  private readonly auth = inject(AuthService);

  readonly propertyId = input.required<string>();
  readonly gebaeude = input<readonly Building[]>([]);

  protected readonly zustand = signal<Zustand>('loading');
  protected readonly anlagen = signal<Anlage[]>([]);
  protected readonly fehler = signal<string | null>(null);
  /** Fehler nach einer Aktion — die Liste bleibt stehen, die Lüge nicht. */
  protected readonly aktionsFehler = signal<string | null>(null);
  protected readonly ansage = signal('');

  /** Stillgelegte mitladen? Das entscheidet der SERVER, nicht ein Client-Filter. */
  protected readonly mitInaktiven = signal(false);

  protected readonly dialogOffen = signal(false);
  protected readonly bearbeitet = signal<Anlage | null>(null);

  protected readonly stillzulegen = signal<Anlage | null>(null);
  protected readonly statusLaeuft = signal(false);

  private ladeReq = 0;

  protected readonly darfAnlegen = computed(() => this.auth.darf('property', 'ANLEGEN'));
  protected readonly darfAendern = computed(() => this.auth.darf('property', 'AENDERN'));

  protected readonly inaktivAnzahl = computed(
    () => this.anlagen().filter((a) => istStillgelegt(a)).length,
  );

  /** Nach Anlagenart gruppiert; stillgelegte ans Ende jeder Gruppe. */
  protected readonly gruppen = computed<Gruppe[]>(() => {
    const map = new Map<AssetType, Anlage[]>();
    for (const a of this.anlagen()) {
      const liste = map.get(a.asset_type);
      if (liste) liste.push(a);
      else map.set(a.asset_type, [a]);
    }
    return [...map.entries()]
      .map(([key, anlagen]) => ({
        key,
        label: artLabel(key),
        anlagen: [...anlagen].sort(
          (x, y) =>
            Number(istStillgelegt(x)) - Number(istStillgelegt(y)) ||
            x.name.localeCompare(y.name, 'de'),
        ),
      }))
      .sort((a, b) => a.label.localeCompare(b.label, 'de'));
  });

  constructor() {
    effect(() => {
      const id = this.propertyId();
      const mitInaktiven = this.mitInaktiven();
      if (id) this.laden(id, mitInaktiven);
    });
  }

  private laden(propertyId: string, mitInaktiven: boolean): void {
    const rid = ++this.ladeReq;
    this.zustand.set('loading');
    this.fehler.set(null);
    this.aktionsFehler.set(null);
    this.svc.list(propertyId, mitInaktiven).subscribe({
      next: (liste) => {
        if (rid !== this.ladeReq) return;
        this.anlagen.set(liste);
        this.zustand.set('ready');
      },
      error: (err) => {
        if (rid !== this.ladeReq) return;
        this.zustand.set('error');
        this.fehler.set(fehlerDetail(err) ?? 'Die Anlagen konnten nicht geladen werden.');
      },
    });
  }

  neuLaden(): void {
    this.laden(this.propertyId(), this.mitInaktiven());
  }

  inaktiveUmschalten(): void {
    this.mitInaktiven.update((v) => !v);
  }

  // --- Erfassen / Bearbeiten -------------------------------------------------
  anlegen(): void {
    this.bearbeitet.set(null);
    this.dialogOffen.set(true);
  }

  bearbeiten(a: Anlage): void {
    this.bearbeitet.set(a);
    this.dialogOffen.set(true);
  }

  dialogSchliessen(): void {
    this.dialogOffen.set(false);
  }

  gespeichert(a: Anlage): void {
    const war = this.bearbeitet() !== null;
    this.dialogOffen.set(false);
    this.bearbeitet.set(null);
    this.ansage.set(
      war ? `Anlage „${a.name}" gespeichert.` : `Anlage „${a.name}" wurde erfasst.`,
    );
    this.neuLaden();
  }

  // --- Stilllegen / reaktivieren --------------------------------------------
  stilllegenFragen(a: Anlage): void {
    if (!this.darfAendern()) return;
    this.stillzulegen.set(a);
  }

  stilllegenAbbrechen(): void {
    if (this.statusLaeuft()) return;
    this.stillzulegen.set(null);
  }

  stilllegenBestaetigen(): void {
    const a = this.stillzulegen();
    if (a && !this.statusLaeuft()) this.statusSetzen(a, 'INAKTIV');
  }

  reaktivieren(a: Anlage): void {
    if (this.darfAendern() && !this.statusLaeuft()) this.statusSetzen(a, 'AKTIV');
  }

  private statusSetzen(a: Anlage, status: 'AKTIV' | 'INAKTIV'): void {
    this.statusLaeuft.set(true);
    this.aktionsFehler.set(null);
    this.svc.setStatus(a.id, status).subscribe({
      next: () => {
        this.statusLaeuft.set(false);
        this.stillzulegen.set(null);
        this.ansage.set(
          status === 'INAKTIV'
            ? `Anlage „${a.name}" stillgelegt. Sie bleibt lesbar.`
            : `Anlage „${a.name}" wieder in Betrieb.`,
        );
        this.neuLaden();
      },
      error: (err) => {
        this.statusLaeuft.set(false);
        this.stillzulegen.set(null);
        this.aktionsFehler.set(
          fehlerDetail(err) ?? 'Der Status der Anlage konnte nicht geändert werden.',
        );
      },
    });
  }

  // --- Darstellung -----------------------------------------------------------
  stillgelegt = istStillgelegt;
  art = artLabel;
  versorgung = supplyLabel;
  energie = energieLabel;
  kw = kwAnzeige;

  /**
   * Rolle der Bewohner:in — **dieselben** Beschriftungen wie im Reiter Belegung.
   * Eine zweite Liste hier liefe garantiert irgendwann auseinander.
   */
  rolle(r: string): string {
    return rolleLabel(r as MieterRolle);
  }

  /** Standort in einer Zeile — „—", wenn nichts erfasst ist (nie erfunden). */
  ort(a: Anlage): string {
    const teile = [a.building_label, a.unit_label, a.location_note].filter(Boolean);
    return teile.length ? teile.join(' · ') : '—';
  }

  /** Gerät in einer Zeile: Hersteller, Modell, Baujahr. */
  geraet(a: Anlage): string {
    const teile = [
      a.manufacturer,
      a.model,
      a.year_built ? String(a.year_built) : null,
    ].filter(Boolean);
    return teile.length ? teile.join(' · ') : '—';
  }

  /**
   * Nur die ZENTRALE Anlage wird hervorgehoben — sie ist die Information, die den
   * Einsatz verändert. Die Versorgung steht immer zusätzlich als Text daneben
   * (Farbe allein trägt nie eine Aussage, WCAG 1.4.1).
   */
  versorgungClass(a: Anlage): string {
    if (a.supply_type === 'ZENTRAL') return 'stamp stamp--warn';
    if (a.supply_type === 'UNBEKANNT') return 'stamp';
    return 'stamp stamp--type';
  }
}
