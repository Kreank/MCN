import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';
import { AufgabeService } from '../../core/aufgabe.service';
import { ProjektService } from '../../core/projekt.service';
import { BelegService } from '../../core/beleg.service';
import { FirmaService } from '../../core/firma.service';
import { Task } from '../../core/aufgabe.model';
import { Project } from '../../core/projekt.model';
import { Quote } from '../../core/beleg.model';
import { Onboarding } from '../../core/firma.model';

type Tile<T> =
  | { kind: 'loading' }
  | { kind: 'ready'; total: number; items: T[] }
  | VerbotenState
  | { kind: 'error' };

/** Ein Erste-Schritte-Punkt: Label, Zielroute und ob er erledigt ist. */
export interface OnboardingSchritt {
  key: keyof Onboarding;
  label: string;
  hinweis: string;
  route: string;
  done: boolean;
}

const SCHRITTE: { key: keyof Onboarding; label: string; hinweis: string; route: string }[] = [
  { key: 'firmenprofil', label: 'Firmenprofil anlegen', hinweis: 'Name, Anschrift, Steuerdaten', route: '/einstellungen/profil' },
  { key: 'logo', label: 'Firmenlogo hochladen', hinweis: 'Erscheint auf den Beleg-PDFs', route: '/einstellungen/profil' },
  { key: 'bankdaten', label: 'Bankverbindung hinterlegen', hinweis: 'IBAN für die Rechnungen', route: '/einstellungen/profil' },
  { key: 'mailkonto', label: 'Mailkonto einrichten', hinweis: 'Für den Beleg-/Mahnungsversand', route: '/einstellungen/mailversand' },
  { key: 'kontakt', label: 'Ersten Kontakt anlegen', hinweis: 'Kunde, Interessent oder Firma', route: '/kontakte' },
  { key: 'liegenschaft', label: 'Erste Liegenschaft anlegen', hinweis: 'Objekt, an dem gearbeitet wird', route: '/liegenschaften' },
  { key: 'projekt', label: 'Erstes Projekt anlegen', hinweis: 'Akte für einen Auftrag', route: '/projekte' },
  { key: 'beleg', label: 'Ersten Beleg erstellen', hinweis: 'Angebot oder Rechnung', route: '/dokumente' },
];

@Component({
  selector: 'app-uebersicht',
  imports: [RouterLink],
  templateUrl: './uebersicht.html',
  styleUrl: './uebersicht.scss',
})
export class Uebersicht {
  private readonly aufgabeSvc = inject(AufgabeService);
  private readonly projektSvc = inject(ProjektService);
  private readonly belegSvc = inject(BelegService);
  private readonly firmaSvc = inject(FirmaService);

  protected readonly tasks = signal<Tile<Task>>({ kind: 'loading' });
  protected readonly projects = signal<Tile<Project>>({ kind: 'loading' });
  protected readonly quotes = signal<Tile<Quote>>({ kind: 'loading' });
  private readonly onboarding = signal<Onboarding | null>(null);

  /** Checkliste mit erledigt-Status; leer, solange nichts geladen ist. */
  protected readonly schritte = computed<OnboardingSchritt[]>(() => {
    const o = this.onboarding();
    if (!o) return [];
    return SCHRITTE.map((s) => ({ ...s, done: o[s.key] }));
  });
  protected readonly erledigt = computed(() => this.schritte().filter((s) => s.done).length);
  /** Karte nur zeigen, solange nicht alle Schritte erledigt sind. */
  protected readonly onboardingSichtbar = computed(() => {
    const s = this.schritte();
    return s.length > 0 && this.erledigt() < s.length;
  });

  constructor() {
    this.firmaSvc.getOnboarding().subscribe({
      next: (o) => this.onboarding.set(o),
      error: () => this.onboarding.set(null),
    });
    this.aufgabeSvc.list({ page: 1, page_size: 5, status: 'OFFEN' }).subscribe({
      next: (d) => this.tasks.set({ kind: 'ready', total: d.total, items: d.items }),
      error: (err) => this.tasks.set(fehlerState(err)),
    });
    this.projektSvc.list({ page: 1, page_size: 5, status: 'OPEN' }).subscribe({
      next: (d) => this.projects.set({ kind: 'ready', total: d.total, items: d.items }),
      error: (err) => this.projects.set(fehlerState(err)),
    });
    this.belegSvc.list({ page: 1, page_size: 5, status: 'ENTWURF' }).subscribe({
      next: (d) => this.quotes.set({ kind: 'ready', total: d.total, items: d.items }),
      error: (err) => this.quotes.set(fehlerState(err)),
    });
  }

  euro(amount: string | null): string {
    if (amount === null) return '—';
    return new Intl.NumberFormat('de-DE', {
      style: 'currency',
      currency: 'EUR',
    }).format(Number(amount));
  }
}
