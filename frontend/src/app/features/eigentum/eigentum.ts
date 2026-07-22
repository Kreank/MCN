import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { AuthService } from '../../core/auth.service';
import { EigentumService } from '../../core/eigentum.service';
import {
  EIGENTUMSART_LABEL,
  Eigentuemer,
  Eigentumsstand,
  EinheitEigentum,
  QUELLENART_LABEL,
  VOLLSTAENDIGKEIT_HINWEIS,
  VOLLSTAENDIGKEIT_LABEL,
  vollstaendigkeitClass,
} from '../../core/eigentum.model';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { fehlerDetail } from '../../shared/http-fehler';
import { EigentumDialog, EigentumDialogModus } from './eigentum-dialog';

type Zustand = 'loading' | 'ready' | 'error';

/**
 * Reiter „Eigentum" der Liegenschaftsmappe: **Wem gehört das — und woher wissen
 * wir das?**
 *
 * Der Reiter zeigte bis Arbeitspaket AP5 einen Platzhalter („sobald die
 * Lesepfade angebunden sind"), obwohl die Tabellen seit Migration 0005
 * vollständig in der Datenbank liegen.
 *
 * Sascha in seiner Domänenmodell-Skizze: *„Eine Liegenschaft kann mehrere
 * Eigentümer beherbergen und dadurch mehrere Rechnungsadressen besitzen. […] Es
 * kann sein, dass ich 20 Rechnungsadressen habe, die ich immer angeben muss."*
 * Genau diese Liste entsteht hier.
 *
 * Drei Dinge, die dieses UI aussprechen muss (Farbe trägt nie allein eine
 * Aussage — WCAG 1.4.1):
 *
 * * **„Nicht erfasst" ≠ „gehört niemandem".** Eine Einheit ohne Stand hat
 *   niemand eingetragen.
 * * **„Teilweise geklärt" ist der Normalfall, kein Mangel.** Man kennt oft nur
 *   einen von vier Eigentümern. Deshalb trägt dieser Grad auch keine Warnfarbe.
 * * **Der Anteil ist ein Bruch.** „1/3" wird als „1/3" gezeigt, nicht als
 *   „33,33 %" — sonst ergäben drei Erben 99,99 % und der Stand wäre nie
 *   vollständig.
 *
 * **Gelöscht wird nie, und Beteiligte werden nicht ausgetauscht.** Ein
 * Eigentümerwechsel ist *Stand beenden → neuen Stand anlegen*; die alte Aussage
 * bleibt als Kette sichtbar. Wer wem wann verkauft hat, ist der Nachweis.
 */
@Component({
  selector: 'app-eigentum',
  imports: [RouterLink, EigentumDialog, Bestaetigung],
  templateUrl: './eigentum.html',
  styleUrl: './eigentum.scss',
})
export class Eigentum {
  private readonly svc = inject(EigentumService);
  private readonly auth = inject(AuthService);

  readonly propertyId = input.required<string>();

  protected readonly zustand = signal<Zustand>('loading');
  protected readonly einheiten = signal<EinheitEigentum[]>([]);
  protected readonly fehler = signal<string | null>(null);
  protected readonly aktionsFehler = signal<string | null>(null);
  protected readonly ansage = signal('');

  /** Beendete Stände mitladen — das entscheidet der SERVER, kein Client-Filter. */
  protected readonly historie = signal(false);

  protected readonly dialog = signal<EigentumDialogModus | null>(null);
  protected readonly zuBeenden = signal<{ stand: Eigentumsstand; wohnung: string } | null>(
    null,
  );
  protected readonly aktionLaeuft = signal(false);

  private ladeReq = 0;

  protected readonly darfAnlegen = computed(() => this.auth.darf('tenure', 'ANLEGEN'));
  protected readonly darfAendern = computed(() => this.auth.darf('tenure', 'AENDERN'));
  protected readonly darfBestaetigen = computed(() =>
    this.auth.darf('tenure', 'FREIGEBEN'),
  );

  protected readonly VOLLSTAENDIGKEIT_LABEL = VOLLSTAENDIGKEIT_LABEL;
  protected readonly VOLLSTAENDIGKEIT_HINWEIS = VOLLSTAENDIGKEIT_HINWEIS;
  protected readonly QUELLENART_LABEL = QUELLENART_LABEL;
  protected readonly EIGENTUMSART_LABEL = EIGENTUMSART_LABEL;
  protected readonly vollstaendigkeitClass = vollstaendigkeitClass;

  /** Einheiten, die einen Eigentumsstand tragen könnten (ohne Gemeinschaftsflächen). */
  protected readonly eigentumsfaehige = computed(
    () => this.einheiten().filter((e) => e.eigentumsfaehig).length,
  );

  /**
   * Wie viele eigentumsfähige Einheiten haben gar keinen Stand?
   *
   * Die Zahl steht bewusst oben: Sie ist die ehrliche Aussage über die
   * Datenlage — und der Grund, warum die Rechnungsempfänger-Liste kurz ist.
   */
  protected readonly ohneErfassung = computed(
    () =>
      this.einheiten().filter((e) => e.eigentumsfaehig && e.eigentum === null).length,
  );

  /**
   * Die Eigentümer der Liegenschaft, dublettenfrei — Saschas „20
   * Rechnungsadressen".
   *
   * Aus den geladenen Ständen abgeleitet statt über einen zweiten Endpunkt:
   * Solange die Liste ohnehin auf dem Schirm ist, wäre ein weiterer Abruf nur
   * eine zweite Wahrheit, die auseinanderlaufen kann. Der Endpunkt
   * `/eigentuemer` bleibt für Aufrufer, die NUR die Empfängerliste brauchen
   * (etwa die Belegerfassung).
   */
  protected readonly alleEigentuemer = computed(() => {
    const gesehen = new Map<string, string>();
    for (const e of this.einheiten()) {
      // Nur GELTENDE Stände: Ein Voreigentümer ist kein Rechnungsempfänger.
      if (!e.eigentum?.is_current) continue;
      for (const person of e.eigentum.eigentuemer) {
        if (!gesehen.has(person.party_id)) {
          gesehen.set(person.party_id, person.display_name);
        }
      }
    }
    return [...gesehen.entries()]
      .map(([party_id, display_name]) => ({ party_id, display_name }))
      .sort((a, b) => a.display_name.localeCompare(b.display_name, 'de'));
  });

  constructor() {
    effect(() => {
      const id = this.propertyId();
      const mitHistorie = this.historie();
      if (id) this.laden(id, mitHistorie);
    });
  }

  protected laden(propertyId: string, historie: boolean): void {
    const req = ++this.ladeReq;
    this.zustand.set('loading');
    this.svc.list(propertyId, historie).subscribe({
      next: (daten) => {
        if (req !== this.ladeReq) return;
        this.einheiten.set(daten);
        this.zustand.set('ready');
      },
      error: (err) => {
        if (req !== this.ladeReq) return;
        this.fehler.set(fehlerDetail(err) ?? 'Unbekannter Fehler.');
        this.zustand.set('error');
      },
    });
  }

  protected neuLaden(): void {
    this.laden(this.propertyId(), this.historie());
  }

  protected historieUmschalten(): void {
    this.historie.update((v) => !v);
  }

  // --- Dialoge -------------------------------------------------------------

  protected anlegenOeffnen(einheit: EinheitEigentum): void {
    this.aktionsFehler.set(null);
    this.dialog.set({
      art: 'neu',
      propertyId: this.propertyId(),
      unitId: einheit.unit_id,
      wohnung: einheit.unit_number,
    });
  }

  protected bearbeitenOeffnen(einheit: EinheitEigentum, stand: Eigentumsstand): void {
    this.aktionsFehler.set(null);
    this.dialog.set({
      art: 'bearbeiten',
      propertyId: this.propertyId(),
      unitId: einheit.unit_id,
      wohnung: einheit.unit_number,
      stand,
    });
  }

  protected eigentuemerOeffnen(einheit: EinheitEigentum, stand: Eigentumsstand): void {
    this.aktionsFehler.set(null);
    this.dialog.set({
      art: 'eigentuemer',
      propertyId: this.propertyId(),
      unitId: einheit.unit_id,
      wohnung: einheit.unit_number,
      stand,
    });
  }

  /**
   * Eine bestehende Beteiligung korrigieren — Anteil, Art oder Bestätigung.
   *
   * Ohne diesen Weg wäre „teilweise geklärt" eine Sackgasse: Ein Stand mit
   * unbestätigten Eigentümern ließe sich nie auf „vollständig geklärt" heben,
   * weil der nur bestätigte Beteiligungen duldet.
   */
  protected beteiligungOeffnen(
    einheit: EinheitEigentum,
    stand: Eigentumsstand,
    person: Eigentuemer,
  ): void {
    this.aktionsFehler.set(null);
    this.dialog.set({
      art: 'beteiligung',
      propertyId: this.propertyId(),
      unitId: einheit.unit_id,
      wohnung: einheit.unit_number,
      stand,
      person,
    });
  }

  protected dialogFertig(erfolg: boolean): void {
    this.dialog.set(null);
    if (erfolg) {
      this.ansage.set('Eigentum gespeichert.');
      this.neuLaden();
    }
  }

  // --- Beenden -------------------------------------------------------------

  protected beendenFragen(einheit: EinheitEigentum, stand: Eigentumsstand): void {
    this.aktionsFehler.set(null);
    this.zuBeenden.set({ stand, wohnung: einheit.unit_number });
  }

  protected beenden(): void {
    const ziel = this.zuBeenden();
    if (!ziel || this.aktionLaeuft()) return;
    this.aktionLaeuft.set(true);
    const heute = new Date();
    const pad = (n: number) => String(n).padStart(2, '0');
    const iso = `${heute.getFullYear()}-${pad(heute.getMonth() + 1)}-${pad(heute.getDate())}`;
    this.svc.beenden(ziel.stand.id, iso).subscribe({
      next: () => {
        this.aktionLaeuft.set(false);
        this.zuBeenden.set(null);
        this.ansage.set('Eigentumsstand beendet.');
        this.neuLaden();
      },
      error: (err) => {
        this.aktionLaeuft.set(false);
        this.zuBeenden.set(null);
        this.aktionsFehler.set(fehlerDetail(err) ?? 'Beenden fehlgeschlagen.');
      },
    });
  }

  // --- Bestätigen ----------------------------------------------------------

  protected bestaetigen(stand: Eigentumsstand): void {
    if (this.aktionLaeuft()) return;
    this.aktionLaeuft.set(true);
    this.aktionsFehler.set(null);
    this.svc.bestaetigen(stand.id).subscribe({
      next: () => {
        this.aktionLaeuft.set(false);
        this.ansage.set('Eigentumsstand bestätigt.');
        this.neuLaden();
      },
      error: (err) => {
        this.aktionLaeuft.set(false);
        this.aktionsFehler.set(fehlerDetail(err) ?? 'Bestätigen fehlgeschlagen.');
      },
    });
  }

  // --- Darstellung ---------------------------------------------------------

  protected datum(iso: string | null): string {
    if (!iso) return '—';
    const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
    if (!y || !m || !d) return iso;
    return new Intl.DateTimeFormat('de-DE', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
    }).format(new Date(y, m - 1, d));
  }

  /** „seit 01.01.2024" bzw. „01.01.2024 – 01.03.2026". */
  protected zeitraum(stand: Eigentumsstand): string {
    const von = this.datum(stand.valid_from);
    return stand.valid_until ? `${von} – ${this.datum(stand.valid_until)}` : `seit ${von}`;
  }

  /**
   * Trägt der Stand alle Anteile? Nur für die Anzeige eines Hinweises —
   * die Prüfung selbst macht der Server (und die Datenbank).
   */
  protected ohneAnteil(stand: Eigentumsstand): number {
    return stand.eigentuemer.filter((e) => e.share_numerator === null).length;
  }

  protected istUnbestaetigt(person: Eigentuemer): boolean {
    return person.confirmation_status !== 'CONFIRMED';
  }
}
