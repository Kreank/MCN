import { DatePipe } from '@angular/common';
import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { BelegungService } from '../../core/belegung.service';
import {
  Belegung as BelegungZeile,
  EinheitBelegung,
  Mieter,
  einheitLabel,
  nutzungLabel,
  rolleLabel,
} from '../../core/belegung.model';
import { AuthService } from '../../core/auth.service';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { fehlerDetail } from '../../shared/http-fehler';
import { BelegungDialog, BelegungDialogModus } from './belegung-dialog';

type Zustand = 'loading' | 'ready' | 'error';

/**
 * Reiter „Belegung" der Liegenschaftsmappe: **Wer wohnt hier — und wie erreiche
 * ich ihn?**
 *
 * Das ist der Reiter, den der Monteur öffnet, bevor er losfährt. Er muss in die
 * Wohnung EG rechts, also braucht er **Name und Telefonnummer von Robco**.
 * Deshalb steht die Nummer als **Wähl-Link** direkt an der Wohnung, nicht zwei
 * Klicks entfernt.
 *
 * Drei Unterscheidungen, die dieses UI aussprechen muss (Farbe allein trägt
 * nie eine Aussage — WCAG 1.4.1, jeder Zustand hat auch Text):
 *
 * * **„Nicht erfasst" ≠ „leerstehend".** Eine Einheit ohne Belegungszeile hat
 *   niemand eingetragen. Leerstand ist eine **erfasste** Belegung.
 * * **Gemeinschaftsfläche/Technikraum trägt keine Belegung** (F-12) — dort gibt
 *   es gar keinen Knopf, statt den Nutzer in einen 422 laufen zu lassen.
 * * **Keine Telefonnummer** heißt: Der Monteur kommt nicht rein. Das UI sagt es,
 *   statt eine leere Zelle zu zeigen.
 *
 * **Gelöscht wird nie.** Ein Mieter zieht aus (`valid_until`), eine Belegung
 * wird beendet — die Historie bleibt, weil der Baustellenbericht von damals auf
 * die Wohnung zeigt, in der damals Musili wohnte.
 */
@Component({
  selector: 'app-belegung',
  imports: [DatePipe, RouterLink, BelegungDialog, Bestaetigung],
  templateUrl: './belegung.html',
  styleUrl: './belegung.scss',
})
export class Belegung {
  private readonly svc = inject(BelegungService);
  private readonly auth = inject(AuthService);

  readonly propertyId = input.required<string>();

  protected readonly zustand = signal<Zustand>('loading');
  protected readonly einheiten = signal<EinheitBelegung[]>([]);
  protected readonly fehler = signal<string | null>(null);
  protected readonly aktionsFehler = signal<string | null>(null);
  protected readonly ansage = signal('');

  /** Beendete Belegungen mitladen — das entscheidet der SERVER, kein Client-Filter. */
  protected readonly historie = signal(false);

  protected readonly dialog = signal<BelegungDialogModus | null>(null);
  protected readonly auszuziehen = signal<{ mieter: Mieter; wohnung: string } | null>(null);
  protected readonly aktionLaeuft = signal(false);

  private ladeReq = 0;

  protected readonly darfAnlegen = computed(() => this.auth.darf('tenure', 'ANLEGEN'));
  protected readonly darfAendern = computed(() => this.auth.darf('tenure', 'AENDERN'));

  /** Nur die belegbaren Einheiten zählen — Technikräume sind keine Wohnungen. */
  protected readonly belegbareEinheiten = computed(
    () => new Set(this.einheiten().filter((e) => e.belegbar).map((e) => e.unit_id)).size,
  );

  protected readonly erfassteEinheiten = computed(
    () =>
      new Set(
        this.einheiten()
          .filter((e) => e.belegbar && e.belegung !== null)
          .map((e) => e.unit_id),
      ).size,
  );

  protected readonly ohneErfassung = computed(
    () => this.belegbareEinheiten() - this.erfassteEinheiten(),
  );

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
        this.einheiten.set(liste);
        this.zustand.set('ready');
      },
      error: (err) => {
        if (rid !== this.ladeReq) return;
        this.zustand.set('error');
        this.fehler.set(fehlerDetail(err) ?? 'Die Belegung konnte nicht geladen werden.');
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

  belegen(e: EinheitBelegung): void {
    this.dialog.set({ art: 'neu', unitId: e.unit_id, unitNummer: e.unit_number });
  }

  bearbeiten(e: EinheitBelegung, b: BelegungZeile): void {
    this.dialog.set({ art: 'bearbeiten', belegung: b, unitNummer: e.unit_number });
  }

  mieterHinzufuegen(e: EinheitBelegung, b: BelegungZeile): void {
    this.dialog.set({ art: 'mieter', belegung: b, unitNummer: e.unit_number });
  }

  dialogSchliessen(): void {
    this.dialog.set(null);
  }

  gespeichert(meldung: string): void {
    this.dialog.set(null);
    this.ansage.set(meldung);
    this.neuLaden();
  }

  // --- Auszug (kein Löschen) -------------------------------------------------

  auszugFragen(m: Mieter, wohnung: string): void {
    if (!this.darfAendern()) return;
    this.auszuziehen.set({ mieter: m, wohnung });
  }

  auszugAbbrechen(): void {
    if (this.aktionLaeuft()) return;
    this.auszuziehen.set(null);
  }

  auszugBestaetigen(): void {
    const ziel = this.auszuziehen();
    if (!ziel || this.aktionLaeuft()) return;
    this.aktionLaeuft.set(true);
    this.aktionsFehler.set(null);
    // Der Auszug gilt ab heute: `daterange` ist halboffen, ein `valid_until` von
    // heute hieße „gilt heute nicht mehr" — richtig für „ist ausgezogen".
    this.svc.endMieter(ziel.mieter.id, this.heute()).subscribe({
      next: () => {
        this.aktionLaeuft.set(false);
        this.auszuziehen.set(null);
        this.ansage.set(
          `${ziel.mieter.display_name} ist ausgezogen. Der Eintrag bleibt in der Historie lesbar.`,
        );
        this.neuLaden();
      },
      error: (err) => {
        this.aktionLaeuft.set(false);
        this.auszuziehen.set(null);
        this.aktionsFehler.set(
          fehlerDetail(err) ?? 'Der Auszug konnte nicht gespeichert werden.',
        );
      },
    });
  }

  private heute(): string {
    return new Date().toISOString().slice(0, 10);
  }

  // --- Darstellung -----------------------------------------------------------

  nutzung = nutzungLabel;
  rolle = rolleLabel;
  einheitArt = einheitLabel;

  /** Die geltenden Mieter zuerst; ausgezogene bleiben lesbar, aber leise. */
  mieterSortiert(b: BelegungZeile): readonly Mieter[] {
    return [...b.mieter].sort(
      (a, c) =>
        Number(c.is_current) - Number(a.is_current) ||
        a.display_name.localeCompare(c.display_name, 'de'),
    );
  }

  /** Leerstand wird als solcher markiert — nicht als „keine Mieter" verschwiegen. */
  istLeerstand(b: BelegungZeile): boolean {
    return b.occupancy_type === 'VACANT';
  }

  nutzungClass(b: BelegungZeile): string {
    if (!b.is_current) return 'stamp';
    if (b.occupancy_type === 'VACANT') return 'stamp stamp--warn';
    if (b.occupancy_type === 'UNKNOWN') return 'stamp';
    return 'stamp stamp--positive';
  }

  /** `tel:`-Ziel — Leerzeichen stören manche Geräte. */
  telHref(nummer: string): string {
    return `tel:${nummer.replace(/[^\d+]/g, '')}`;
  }
}
