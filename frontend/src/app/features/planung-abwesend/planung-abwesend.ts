import { Component, computed, inject, signal } from '@angular/core';
import { EinsatzService } from '../../core/einsatz.service';
import { Abwesend } from '../../core/einsatz.model';
import { PlanungNav } from '../planung-nav/planung-nav';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';

type ViewState = { kind: 'loading' } | { kind: 'ready' } | VerbotenState | { kind: 'error' };

/**
 * „Wer ist gerade nicht da?" — die Abwesenheitsübersicht der Disposition.
 *
 * **Diese Ansicht zeigt die Abwesenheits-ART nicht, und sie kann es auch nicht.**
 * Urlaub von Krankheit zu unterscheiden ist ein Gesundheitsdatum — besondere
 * Kategorie nach DSGVO Art. 9. Sie hängt am `hr`-Recht, das die Disposition
 * nicht hat; diese Ansicht hängt an `workflow/LESEN`. Der Server liefert die Art
 * hier gar nicht erst aus (`AbwesendOut` in `api/planung.py` führt kein solches
 * Feld) — es gibt also nichts, was hier versehentlich angezeigt werden könnte.
 *
 * Für die Disposition genügt: **wer fehlt, von wann bis wann.** Das Feld ist
 * gesperrt, der Grund geht sie nichts an. Genau dieser Fehler — die Art in einer
 * Planungssicht mitzuliefern — war in der Plantafel schon einmal drin und wurde
 * behoben. Er wird hier nicht wiederholt.
 */
@Component({
  selector: 'app-planung-abwesend',
  imports: [PlanungNav, KeinZugriff],
  templateUrl: './planung-abwesend.html',
  styleUrl: './planung-abwesend.scss',
})
export class PlanungAbwesend {
  private readonly svc = inject(EinsatzService);

  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly zeilen = signal<Abwesend[]>([]);
  protected readonly fehler = signal<string | null>(null);

  protected readonly von = signal(this.heuteIso());
  protected readonly bis = signal(this.heuteIso());

  protected readonly spannen = [
    { wert: 'heute', label: 'Heute' },
    { wert: 'woche', label: 'Diese Woche' },
    { wert: 'monat', label: 'Dieser Monat' },
  ];
  protected readonly spanne = signal('heute');

  protected readonly anzahl = computed(() => this.zeilen().length);

  constructor() {
    this.laden();
  }

  private heuteIso(): string {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(
      d.getDate(),
    ).padStart(2, '0')}`;
  }

  private iso(d: Date): string {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(
      d.getDate(),
    ).padStart(2, '0')}`;
  }

  protected spanneWaehlen(wert: string): void {
    this.spanne.set(wert);
    const heute = new Date();
    if (wert === 'heute') {
      this.von.set(this.iso(heute));
      this.bis.set(this.iso(heute));
    } else if (wert === 'woche') {
      const montag = new Date(heute);
      montag.setDate(heute.getDate() - ((heute.getDay() + 6) % 7));
      const sonntag = new Date(montag);
      sonntag.setDate(montag.getDate() + 6);
      this.von.set(this.iso(montag));
      this.bis.set(this.iso(sonntag));
    } else {
      const erster = new Date(heute.getFullYear(), heute.getMonth(), 1);
      const letzter = new Date(heute.getFullYear(), heute.getMonth() + 1, 0);
      this.von.set(this.iso(erster));
      this.bis.set(this.iso(letzter));
    }
    this.laden();
  }

  protected laden(): void {
    this.state.set({ kind: 'loading' });
    this.fehler.set(null);
    this.svc.abwesend(this.von(), this.bis()).subscribe({
      next: (z) => {
        this.zeilen.set(z);
        this.state.set({ kind: 'ready' });
      },
      error: (err) => {
        this.fehler.set(fehlerDetail(err));
        this.state.set(fehlerState(err));
      },
    });
  }

  protected datum(iso: string): string {
    const [y, m, d] = iso.split('-').map(Number);
    return new Intl.DateTimeFormat('de-DE', {
      weekday: 'short',
      day: '2-digit',
      month: '2-digit',
    }).format(new Date(y, m - 1, d));
  }

  /** „3 Tage" — die Dauer der Abwesenheit, halbe Randtage eingerechnet. */
  protected dauer(a: Abwesend): string {
    const start = new Date(a.start_date);
    const ende = new Date(a.end_date);
    let tage = Math.round((ende.getTime() - start.getTime()) / 86_400_000) + 1;
    if (a.half_day_start) tage -= 0.5;
    if (a.half_day_end) tage -= 0.5;
    const text = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1 }).format(tage);
    return tage === 1 ? '1 Tag' : `${text} Tage`;
  }
}
