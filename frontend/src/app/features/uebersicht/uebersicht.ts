import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';
import { AufgabeService } from '../../core/aufgabe.service';
import { ProjektService } from '../../core/projekt.service';
import { BelegService } from '../../core/beleg.service';
import { FirmaService } from '../../core/firma.service';
import { AuthService } from '../../core/auth.service';
import { Task } from '../../core/aufgabe.model';
import { Project } from '../../core/projekt.model';
import { Quote } from '../../core/beleg.model';
import { Onboarding } from '../../core/firma.model';
import { UebersichtMonteur } from '../uebersicht-monteur/uebersicht-monteur';

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
  imports: [RouterLink, UebersichtMonteur],
  templateUrl: './uebersicht.html',
  styleUrl: './uebersicht.scss',
})
export class Uebersicht {
  private readonly aufgabeSvc = inject(AufgabeService);
  private readonly projektSvc = inject(ProjektService);
  private readonly belegSvc = inject(BelegService);
  private readonly firmaSvc = inject(FirmaService);
  private readonly auth = inject(AuthService);

  protected readonly tasks = signal<Tile<Task>>({ kind: 'loading' });
  protected readonly projects = signal<Tile<Project>>({ kind: 'loading' });
  protected readonly quotes = signal<Tile<Quote>>({ kind: 'loading' });
  private readonly onboarding = signal<Onboarding | null>(null);

  /**
   * **Wer nur seine eigenen Zeilen sieht, bekommt eine eigene Startseite.**
   *
   * Dieser Leitstand ist ein Büro-Dashboard: offene Projekte, Angebots-Entwürfe,
   * Einrichtungs-Checkliste. Für den Monteur (row_scope EIGENE auf `workflow`)
   * ist davon nichts brauchbar — die Angebotsliste antwortet ihm mit 403
   * (`beleg.py` nutzt dort `require`, fail-closed), das Onboarding ist Chefkram.
   * Statt drei Kacheln „Keine Berechtigung" zu zeigen, übernimmt hier
   * `UebersichtMonteur`: seine Einsätze, seine Termine, seine Aufgaben, die
   * Stempeluhr.
   */
  protected readonly monteurSicht = computed(
    () => this.auth.darf('workflow', 'LESEN') && !this.auth.darfAlle('workflow', 'LESEN'),
  );

  /**
   * Die Angebots-Kachel setzt `GET /api/invoicing/quotes` voraus — ein
   * fail-closed-Endpunkt (`require`). Wer den Beleg-Scope nur mit EIGENE trägt,
   * bekommt dort 403. Eine Kachel, deren einziger Inhalt „darfst du nicht" ist,
   * ist keine Information — sie wird gar nicht erst gebaut.
   */
  protected readonly angeboteSichtbar = computed(() => this.auth.darfAlle('invoicing', 'LESEN'));

  /**
   * Die Einrichtungs-Checkliste führt ausschließlich in Bereiche, die
   * `company/AENDERN` bzw. `invoicing/AENDERN` verlangen (Firmenprofil, Logo,
   * Bankdaten, Mailkonto). Wer die Firma nicht einrichten darf, bekommt keine
   * Liste mit Schritten, die er nicht gehen kann.
   */
  private readonly darfEinrichten = computed(() => this.auth.darf('company', 'AENDERN'));

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
    return this.darfEinrichten() && s.length > 0 && this.erledigt() < s.length;
  });

  constructor() {
    // Monteurs-Sicht: keine einzige Abfrage dieses Büro-Leitstands ausführen.
    if (this.monteurSicht()) return;

    if (this.darfEinrichten()) {
      this.firmaSvc.getOnboarding().subscribe({
        next: (o) => this.onboarding.set(o),
        error: () => this.onboarding.set(null),
      });
    }
    this.aufgabeSvc.list({ page: 1, page_size: 5, status: 'OFFEN' }).subscribe({
      next: (d) => this.tasks.set({ kind: 'ready', total: d.total, items: d.items }),
      error: (err) => this.tasks.set(fehlerState(err)),
    });
    this.projektSvc.list({ page: 1, page_size: 5, status: 'OPEN' }).subscribe({
      next: (d) => this.projects.set({ kind: 'ready', total: d.total, items: d.items }),
      error: (err) => this.projects.set(fehlerState(err)),
    });
    if (this.angeboteSichtbar()) {
      this.belegSvc.list({ page: 1, page_size: 5, status: 'ENTWURF' }).subscribe({
        next: (d) => this.quotes.set({ kind: 'ready', total: d.total, items: d.items }),
        error: (err) => this.quotes.set(fehlerState(err)),
      });
    }
  }

  euro(amount: string | null): string {
    if (amount === null) return '—';
    return new Intl.NumberFormat('de-DE', {
      style: 'currency',
      currency: 'EUR',
    }).format(Number(amount));
  }
}
