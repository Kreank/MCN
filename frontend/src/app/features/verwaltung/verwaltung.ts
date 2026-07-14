import { DatePipe } from '@angular/common';
import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { VerwaltungService } from '../../core/verwaltung.service';
import {
  Mandat,
  Zustaendigkeit,
  mandatLabel,
  scopeLabel,
  zustaendigLabel,
} from '../../core/verwaltung.model';
import { AuthService } from '../../core/auth.service';
import { Building } from '../../core/property.model';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { fehlerDetail } from '../../shared/http-fehler';
import { VerwaltungDialog, VerwaltungDialogModus } from './verwaltung-dialog';

type Zustand = 'loading' | 'ready' | 'error';

/**
 * Reiter „Verwaltung" der Liegenschaftsmappe: **Wer verwaltet dieses Haus?**
 *
 * Die Verwaltung ist **keine Beteiligtenrolle** an der Liegenschaft — sie läuft
 * ausschließlich über ein **Mandat**. Deshalb steht sie hier und nicht bei den
 * Beteiligten. Der Unterschied ist nicht formal, er wird bei der Rechnung scharf:
 *
 * * Die **WEG** beauftragt und zahlt (Auftraggeber).
 * * **Stegos** verwaltet und ist der Ansprechpartner.
 * * Der **Standardkontakt** ist die Person, die abnimmt.
 *
 * Das UI zeigt alle drei nebeneinander, weil man sie sonst verwechselt — und
 * dann geht die Rechnung an den Falschen.
 *
 * **Ein Mandat wird beendet, nie gelöscht**, und sein **Umfang ist
 * unveränderlich** (A-11): Ein anderer Umfang ist ein Nachfolgemandat. Das UI
 * bietet deshalb gar keinen Weg an, Einheiten nachträglich zu ändern.
 */
@Component({
  selector: 'app-verwaltung',
  imports: [DatePipe, RouterLink, VerwaltungDialog, Bestaetigung],
  templateUrl: './verwaltung.html',
  styleUrl: './verwaltung.scss',
})
export class Verwaltung {
  private readonly svc = inject(VerwaltungService);
  private readonly auth = inject(AuthService);

  readonly propertyId = input.required<string>();
  /** Gebäude/Einheiten der Liegenschaft — Grundlage der Umfangswahl beim Teilmandat. */
  readonly gebaeude = input<readonly Building[]>([]);

  protected readonly zustand = signal<Zustand>('loading');
  protected readonly mandate = signal<Mandat[]>([]);
  protected readonly fehler = signal<string | null>(null);
  protected readonly aktionsFehler = signal<string | null>(null);
  protected readonly ansage = signal('');

  protected readonly historie = signal(false);

  protected readonly dialog = signal<VerwaltungDialogModus | null>(null);
  protected readonly zuBeenden = signal<Mandat | null>(null);
  protected readonly zustaendigZuBeenden = signal<Zustaendigkeit | null>(null);
  protected readonly aktionLaeuft = signal(false);

  private ladeReq = 0;

  protected readonly darfAnlegen = computed(() => this.auth.darf('management', 'ANLEGEN'));
  protected readonly darfAendern = computed(() => this.auth.darf('management', 'AENDERN'));

  constructor() {
    effect(() => {
      const id = this.propertyId();
      const historie = this.historie();
      if (id) this.laden(id, historie);
    });
  }

  private laden(propertyId: string, historie: boolean): void {
    const rid = ++this.ladeReq;
    this.zustand.set('loading');
    this.fehler.set(null);
    this.aktionsFehler.set(null);
    this.svc.list(propertyId, historie).subscribe({
      next: (liste) => {
        if (rid !== this.ladeReq) return;
        this.mandate.set(liste);
        this.zustand.set('ready');
      },
      error: (err) => {
        if (rid !== this.ladeReq) return;
        this.zustand.set('error');
        this.fehler.set(fehlerDetail(err) ?? 'Die Verwaltung konnte nicht geladen werden.');
      },
    });
  }

  neuLaden(): void {
    this.laden(this.propertyId(), this.historie());
  }

  historieUmschalten(): void {
    this.historie.update((v) => !v);
  }

  // --- Dialoge ---------------------------------------------------------------

  anlegen(): void {
    this.dialog.set({ art: 'neu' });
  }

  bearbeiten(m: Mandat): void {
    this.dialog.set({ art: 'bearbeiten', mandat: m });
  }

  zustaendigkeitHinzufuegen(m: Mandat): void {
    this.dialog.set({ art: 'zustaendigkeit', mandat: m });
  }

  dialogSchliessen(): void {
    this.dialog.set(null);
  }

  gespeichert(meldung: string): void {
    this.dialog.set(null);
    this.ansage.set(meldung);
    this.neuLaden();
  }

  // --- Beenden (kein Löschen) ------------------------------------------------

  beendenFragen(m: Mandat): void {
    if (!this.darfAendern()) return;
    this.zuBeenden.set(m);
  }

  beendenAbbrechen(): void {
    if (this.aktionLaeuft()) return;
    this.zuBeenden.set(null);
  }

  beendenBestaetigen(): void {
    const m = this.zuBeenden();
    if (!m || this.aktionLaeuft()) return;
    this.aktionLaeuft.set(true);
    this.aktionsFehler.set(null);
    this.svc.beenden(m.id, this.heute()).subscribe({
      next: () => {
        this.aktionLaeuft.set(false);
        this.zuBeenden.set(null);
        this.ansage.set(
          `Mandat beendet. Es bleibt lesbar — die Aufträge von damals liefen darüber.`,
        );
        this.neuLaden();
      },
      error: (err) => {
        this.aktionLaeuft.set(false);
        this.zuBeenden.set(null);
        this.aktionsFehler.set(
          fehlerDetail(err) ?? 'Das Mandat konnte nicht beendet werden.',
        );
      },
    });
  }

  zustaendigBeendenFragen(z: Zustaendigkeit): void {
    if (!this.darfAendern()) return;
    this.zustaendigZuBeenden.set(z);
  }

  zustaendigBeendenAbbrechen(): void {
    if (this.aktionLaeuft()) return;
    this.zustaendigZuBeenden.set(null);
  }

  zustaendigBeendenBestaetigen(): void {
    const z = this.zustaendigZuBeenden();
    if (!z || this.aktionLaeuft()) return;
    this.aktionLaeuft.set(true);
    this.aktionsFehler.set(null);
    this.svc.endZustaendigkeit(z.id, this.heute()).subscribe({
      next: () => {
        this.aktionLaeuft.set(false);
        this.zustaendigZuBeenden.set(null);
        this.ansage.set('Zuständigkeit beendet.');
        this.neuLaden();
      },
      error: (err) => {
        this.aktionLaeuft.set(false);
        this.zustaendigZuBeenden.set(null);
        this.aktionsFehler.set(
          fehlerDetail(err) ?? 'Die Zuständigkeit konnte nicht beendet werden.',
        );
      },
    });
  }

  private heute(): string {
    return new Date().toISOString().slice(0, 10);
  }

  // --- Darstellung -----------------------------------------------------------

  mandatArt = mandatLabel;
  umfang = scopeLabel;
  zustaendigArt = zustaendigLabel;

  /** Geltende Zuständigkeiten zuerst, danach nach Eskalationsstufe. */
  zustaendigSortiert(m: Mandat): readonly Zustaendigkeit[] {
    return [...m.zustaendigkeiten].sort(
      (a, b) => Number(b.is_current) - Number(a.is_current) || a.priority - b.priority,
    );
  }

  telHref(nummer: string): string {
    return `tel:${nummer.replace(/[^\d+]/g, '')}`;
  }
}
