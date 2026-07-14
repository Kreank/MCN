import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { AnlageService } from '../../core/anlage.service';
import { PropertyService } from '../../core/property.service';
import {
  AnlageDetail as AnlageDetailModel,
  artLabel,
  energieLabel,
  istStillgelegt,
  kwAnzeige,
  supplyLabel,
} from '../../core/anlage.model';
import { Building } from '../../core/property.model';
import { AuthService } from '../../core/auth.service';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';
import { AnlageDialog } from '../anlagen/anlage-dialog';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: AnlageDetailModel }
  | VerbotenState
  | { kind: 'error' };

/**
 * Anlagenmappe — Stammdaten, Wartung/Prüfungen, Aufträge und Fälligkeiten.
 *
 * **Warum eine eigene Route und kein Ausklapp-Panel im Reiter?** Weil die Anlage
 * über ihr Objekt hinausreicht: Aufträge, Prüfungen und Fälligkeiten hängen an
 * ihr, und alle drei müssen umgekehrt **auf sie verlinken** können („Auftrag
 * AU-00042 → Anlage: Heizzentrale"). Ein Panel im Reiter der Liegenschaft hat
 * keine Adresse — man kann es nicht verlinken, nicht als Lesezeichen ablegen und
 * nicht aus einer Fälligkeit heraus öffnen. Der Raum (`raumaufmass`) darf
 * inline bleiben, gerade weil ihn niemand referenziert.
 *
 * **`leistung_kw = null` bleibt „unbekannt", nie 0 kW.**
 */
@Component({
  selector: 'app-anlage-detail',
  imports: [Mappe, RouterLink, KeinZugriff, Bestaetigung, AnlageDialog],
  templateUrl: './anlage-detail.html',
  styleUrl: './anlage-detail.scss',
})
export class AnlageDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(AnlageService);
  private readonly properties = inject(PropertyService);
  private readonly auth = inject(AuthService);

  protected readonly tab = signal('stammdaten');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly ansage = signal('');
  protected readonly aktionsFehler = signal<string | null>(null);

  /** Gebäude der Liegenschaft — nur für den Bearbeiten-Dialog (Standortauswahl). */
  protected readonly gebaeude = signal<readonly Building[]>([]);

  protected readonly dialogOffen = signal(false);
  protected readonly stilllegenOffen = signal(false);
  protected readonly statusLaeuft = signal(false);

  private reqId = 0;

  protected readonly tabs: MappeTab[] = [
    { id: 'stammdaten', label: 'Stammdaten' },
    { id: 'wartung', label: 'Wartung & Prüfungen' },
    { id: 'auftraege', label: 'Aufträge' },
  ];

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  protected readonly darfAendern = computed(() => this.auth.darf('property', 'AENDERN'));

  protected readonly stillgelegt = computed(() => {
    const d = this.daten();
    return d ? istStillgelegt(d) : false;
  });

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.tab.set('stammdaten');
      this.aktionsFehler.set(null);
      if (!id) {
        this.state.set({ kind: 'error' });
        return;
      }
      this.laden(id);
    });
  }

  retry(): void {
    const id = this.route.snapshot.paramMap.get('id');
    if (id) this.laden(id);
  }

  private laden(id: string): void {
    const rid = ++this.reqId;
    this.state.set({ kind: 'loading' });
    this.svc.get(id).subscribe({
      next: (data) => {
        if (rid !== this.reqId) return;
        this.state.set({ kind: 'ready', data });
        this.gebaeudeLaden(data.property_id, rid);
      },
      error: (err) => {
        if (rid === this.reqId) this.state.set(fehlerState(err));
      },
    });
  }

  /**
   * Zweite Anfrage: Sie liefert nur die Auswahl für den Bearbeiten-Dialog.
   * Scheitert sie, bleibt die Mappe bedienbar — dann steht im Dialog eben keine
   * Gebäudeliste. Eine halb geladene Seite ist besser als gar keine.
   */
  private gebaeudeLaden(propertyId: string, rid: number): void {
    this.properties.get(propertyId).subscribe({
      next: (p) => {
        if (rid === this.reqId) this.gebaeude.set(p.buildings);
      },
      error: () => {
        if (rid === this.reqId) this.gebaeude.set([]);
      },
    });
  }

  private neuLaden(): void {
    const d = this.daten();
    if (d) this.laden(d.id);
  }

  // --- Bearbeiten ------------------------------------------------------------
  bearbeiten(): void {
    this.dialogOffen.set(true);
  }

  dialogSchliessen(): void {
    this.dialogOffen.set(false);
  }

  gespeichert(a: AnlageDetailModel): void {
    this.dialogOffen.set(false);
    // Die Antwort IST der neue Stand (inkl. Bezüge) — kein zweiter Aufruf nötig.
    this.state.set({ kind: 'ready', data: a });
    this.ansage.set(`Anlage „${a.name}" gespeichert.`);
  }

  // --- Stilllegen / reaktivieren --------------------------------------------
  stilllegenFragen(): void {
    if (this.darfAendern()) this.stilllegenOffen.set(true);
  }

  stilllegenAbbrechen(): void {
    if (!this.statusLaeuft()) this.stilllegenOffen.set(false);
  }

  stilllegenBestaetigen(): void {
    this.statusSetzen('INAKTIV');
  }

  reaktivieren(): void {
    if (this.darfAendern() && !this.statusLaeuft()) this.statusSetzen('AKTIV');
  }

  private statusSetzen(status: 'AKTIV' | 'INAKTIV'): void {
    const d = this.daten();
    if (!d || this.statusLaeuft()) return;
    this.statusLaeuft.set(true);
    this.aktionsFehler.set(null);
    this.svc.setStatus(d.id, status).subscribe({
      next: (a) => {
        this.statusLaeuft.set(false);
        this.stilllegenOffen.set(false);
        this.state.set({ kind: 'ready', data: a });
        this.ansage.set(
          status === 'INAKTIV'
            ? `Anlage „${a.name}" stillgelegt. Sie bleibt lesbar.`
            : `Anlage „${a.name}" wieder in Betrieb.`,
        );
      },
      error: (err) => {
        this.statusLaeuft.set(false);
        this.stilllegenOffen.set(false);
        this.aktionsFehler.set(
          fehlerDetail(err) ?? 'Der Status der Anlage konnte nicht geändert werden.',
        );
        this.neuLaden();
      },
    });
  }

  // --- Darstellung -----------------------------------------------------------
  art = artLabel;
  versorgung = supplyLabel;
  energie = energieLabel;
  kw = kwAnzeige;

  /** Nicht erfasst ist „—", nie ein erfundener Wert. */
  oder(w: string | number | null | undefined): string {
    return w === null || w === undefined || w === '' ? '—' : String(w);
  }

  versorgungClass(v: string): string {
    if (v === 'ZENTRAL') return 'stamp stamp--warn';
    if (v === 'UNBEKANNT') return 'stamp';
    return 'stamp stamp--type';
  }
}
