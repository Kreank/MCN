import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
import { HeizlastRechner } from './heizlast-rechner';
import { EinheitenUmrechner } from './einheiten-umrechner';
import { VolumenstromRechner } from './volumenstrom-rechner';
import { HeizkoerperRechner } from './heizkoerper-rechner';

export interface WerkzeugTab {
  readonly id: string;
  readonly label: string;
  /** Einzeiler unter dem Reiter — sagt, wofür das Werkzeug gut ist. */
  readonly zweck: string;
}

export const WERKZEUGE: readonly WerkzeugTab[] = [
  {
    id: 'heizlast',
    label: 'Heizlast (überschlägig)',
    zweck: 'Beheizte Fläche × Kennwert — die schnelle Größenordnung fürs Gespräch.',
  },
  {
    id: 'heizkoerper',
    label: 'Heizkörper-Umrechnung',
    zweck: 'Was leistet der vorhandene Heizkörper bei niedrigerer Vorlauftemperatur?',
  },
  {
    id: 'volumenstrom',
    label: 'Volumenstrom',
    zweck: 'Volumenstrom aus Heizleistung und Spreizung (Wasser).',
  },
  {
    id: 'einheiten',
    label: 'Einheiten-Umrechner',
    zweck: 'kW, bar, °C, l/min, kWh, Zoll, °dH — alle Einheiten einer Größe auf einmal.',
  },
];

const STANDARD = 'heizlast';

/**
 * Werkzeuge — kleine Helfer für den SHK-Büroalltag. Rein rechnende Werkzeuge:
 * kein Serverzugriff, keine Daten, deshalb auch **kein Modulrecht** (jede
 * angemeldete Rolle darf rechnen).
 *
 * Aufrufbar direkt über `/werkzeuge/:werkzeug`; der Query-Parameter `objekt`
 * trägt einen Kontext (z. B. den Namen der Liegenschaft) auf die Ausgabe.
 */
@Component({
  selector: 'app-werkzeuge',
  imports: [HeizlastRechner, EinheitenUmrechner, VolumenstromRechner, HeizkoerperRechner],
  templateUrl: './werkzeuge.html',
  styleUrl: './werkzeuge.scss',
})
export class Werkzeuge {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  protected readonly tabs = WERKZEUGE;
  protected readonly aktiv = signal(STANDARD);
  /** Kontext aus der Herkunftsseite (z. B. Liegenschaft) — nur Anzeige/Ausgabe. */
  protected readonly kontext = signal('');

  protected readonly aktiverTab = computed(
    () => this.tabs.find((t) => t.id === this.aktiv()) ?? this.tabs[0],
  );

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('werkzeug');
      this.aktiv.set(this.tabs.some((t) => t.id === id) ? id! : STANDARD);
    });
    this.route.queryParamMap.pipe(takeUntilDestroyed()).subscribe((qp) => {
      this.kontext.set((qp.get('objekt') ?? '').slice(0, 120));
    });
  }

  /** Reiterwechsel spiegelt sich in der URL (teilbar, per Zurück-Taste nutzbar). */
  protected waehle(id: string): void {
    if (id === this.aktiv()) return;
    this.router.navigate(['/werkzeuge', id], { queryParamsHandling: 'preserve' });
  }

  /** Pfeiltasten-Bedienung der Reiterleiste (WAI-ARIA Tabs). */
  protected onTabKey(event: KeyboardEvent, index: number): void {
    const letzter = this.tabs.length - 1;
    let ziel: number | null = null;
    if (event.key === 'ArrowRight') ziel = index === letzter ? 0 : index + 1;
    else if (event.key === 'ArrowLeft') ziel = index === 0 ? letzter : index - 1;
    else if (event.key === 'Home') ziel = 0;
    else if (event.key === 'End') ziel = letzter;
    if (ziel === null) return;
    event.preventDefault();
    const id = this.tabs[ziel].id;
    this.waehle(id);
    queueMicrotask(() => document.getElementById(`werkzeug-tab-${id}`)?.focus());
  }
}
