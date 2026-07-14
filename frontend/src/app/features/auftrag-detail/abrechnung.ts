import { Component, computed, effect, inject, input, output, signal } from '@angular/core';
import { Router } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import { AuftragService } from '../../core/auftrag.service';
import { BelegService } from '../../core/beleg.service';
import { AuthService } from '../../core/auth.service';
import {
  BillingMode,
  OffeneAbrechnung,
  OffeneBerichtsposition,
  OffeneZeitgruppe,
  WorkOrderDetail,
} from '../../core/auftrag.model';
import {
  PreisKlaerung as PreisKlaerungPos,
  Quote,
  QuoteStatus,
  RechnungAusAuftrag,
} from '../../core/beleg.model';
import { PreisKlaerung } from '../../shared/preis-klaerung/preis-klaerung';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { fehlerDetail } from '../../shared/http-fehler';
import { apiZuDeAnzeige } from '../../shared/formular/dezimal';
import { isoDatumDe } from '../../shared/datum';

type Zustand =
  | { kind: 'loading' }
  | { kind: 'ready'; daten: OffeneAbrechnung }
  | { kind: 'forbidden' }
  | { kind: 'error' };

type Meldung = { art: 'erfolg' | 'fehler'; text: string };

/** Diese Angebotsstatus bilden kein Soll — daraus entsteht keine Rechnung
 *  (Server: `site_report.SOLL_AUSGESCHLOSSENE_STATUS`). */
const KEIN_SOLL: QuoteStatus[] = ['ENTWURF', 'INTERN_GEPRUEFT', 'ABGELEHNT', 'ERSETZT'];

const TAX_CODE_OPTIONEN: FeldOption[] = [
  { wert: 'DE_19', label: 'USt 19 %' },
  { wert: 'DE_7', label: 'USt 7 %' },
  { wert: 'DE_0', label: 'Steuerfrei (0 %)' },
  { wert: 'DE_13B', label: '§ 13b UStG (Reverse Charge)' },
];

/**
 * Der Reiter **Abrechnung** der Auftrags-Mappe.
 *
 * Drei Dinge an einem Ort:
 *
 * 1. **Abrechnungsart** (PAUSCHAL | REGIE) — sie entscheidet, WORAUS die Rechnung
 *    entsteht. Nach erfolgter Abrechnung sperrt der Server den Wechsel (422): er
 *    wäre das Tor zur Doppelabrechnung (dieselbe Leistung einmal als vereinbarte
 *    Angebotsposition, einmal als geleistete Berichtsposition — beide für sich
 *    sauber gebunden). Der 422 wird **wörtlich gezeigt**, nicht verschluckt.
 * 2. **Was ist noch nicht abgerechnet** — Berichtspositionen und Zeitgruppen ohne
 *    aktive Bindung. Ein unbekannter Preis ist hier **schon sichtbar** (Text +
 *    Symbol, nie nur Farbe), lange bevor jemand fakturieren will.
 * 3. Die beiden **Abrechnungsläufe**, je nach Modus. Nach Erfolg springt die
 *    Ansicht in den erzeugten Rechnungsentwurf.
 *
 * **Keine Geldberechnung im Client.** Jede Summe kommt vom Server; hier wird nur
 * angezeigt — und im Klärungsfall ein Einzelpreis entgegengenommen.
 *
 * Der Endpunkt `offene-abrechnung` ist eine Auftragssicht über die ganze
 * Baustelle: Rollen mit row_scope EIGENE bekommen 403 (fail-closed). Der Reiter
 * wird bei ihnen gar nicht erst angeboten (Muster `darfAlle`); die Komponente
 * behandelt den 403 trotzdem sauber.
 */
@Component({
  selector: 'app-abrechnung',
  imports: [ReactiveFormsModule, Feld, PreisKlaerung],
  templateUrl: './abrechnung.html',
  styleUrl: './abrechnung.scss',
})
export class Abrechnung {
  readonly auftrag = input.required<WorkOrderDetail>();
  /** Der Auftrag hat sich geändert (Abrechnungsart) — die Mappe übernimmt ihn. */
  readonly geaendert = output<WorkOrderDetail>();

  private readonly svc = inject(AuftragService);
  private readonly belegSvc = inject(BelegService);
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly fb = inject(FormBuilder);

  protected readonly taxCodeOptionen = TAX_CODE_OPTIONEN;

  // --- Rechte (nur Sichtbarkeit — der Server setzt sie durch) ---------------
  /** Der Moduswechsel verlangt BEIDE Rechte (Server: workflow + invoicing). */
  protected readonly darfModusWechseln = computed(
    () => this.auth.darf('workflow', 'AENDERN') && this.auth.darf('invoicing', 'AENDERN'),
  );
  protected readonly darfAbrechnen = computed(() => this.auth.darf('invoicing', 'ANLEGEN'));

  protected readonly zustand = signal<Zustand>({ kind: 'loading' });
  protected readonly meldung = signal<Meldung | null>(null);
  protected readonly aktionLaedt = signal(false);
  private reqId = 0;

  protected readonly offen = computed(() => {
    const z = this.zustand();
    return z.kind === 'ready' ? z.daten : null;
  });

  // --- Abrechnungsart -------------------------------------------------------
  protected readonly modusForm = this.fb.group({
    billing_mode: this.fb.control<BillingMode>('PAUSCHAL', { nonNullable: true }),
  });
  protected readonly modusLaedt = signal(false);
  /** Server-Meldung des letzten Wechselversuchs (z. B. „bereits abgerechnet"). */
  protected readonly modusFehler = signal<string | null>(null);

  protected readonly modusGeaendert = computed(() => {
    const gewaehlt = this.modusGewaehlt();
    return gewaehlt !== this.auftrag().billing_mode;
  });
  /** Signal-Spiegel des Formularwerts (das Formular selbst ist kein Signal). */
  protected readonly modusGewaehlt = signal<BillingMode>('PAUSCHAL');

  // --- Angebotsweg (PAUSCHAL) ----------------------------------------------
  protected readonly angebote = signal<Quote[]>([]);
  protected readonly angeboteLaden = signal(false);
  protected readonly angebotForm = this.fb.group({
    quote_id: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
  });

  /** Nur Angebote, die diesem Auftrag zugeordnet sind UND ein Soll bilden. */
  protected readonly angebotOptionen = computed<FeldOption[]>(() =>
    this.angebote()
      .filter((q) => !KEIN_SOLL.includes(q.status))
      .map((q) => ({
        wert: q.id,
        label: `${q.quote_number ?? 'ohne Nummer'} — ${q.title}`,
      })),
  );
  /** Zugeordnet, aber (noch) kein Soll: benennen statt verschweigen. */
  protected readonly angeboteOhneSoll = computed(() =>
    this.angebote().filter((q) => KEIN_SOLL.includes(q.status)),
  );

  // --- Regieweg (REGIE) -----------------------------------------------------
  protected readonly regieForm = this.fb.group({
    tax_code: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    mit_berichten: this.fb.control(true, { nonNullable: true }),
    mit_zeiten: this.fb.control(true, { nonNullable: true }),
  });

  // --- Preisklärung ---------------------------------------------------------
  protected readonly klaerungOffen = signal(false);
  protected readonly klaerungPositionen = signal<PreisKlaerungPos[]>([]);
  protected readonly klaerungFehler = signal<string | null>(null);

  constructor() {
    // Auftragswechsel (oder erster Aufbau): Liste + Formulare neu aufsetzen.
    effect(() => {
      const a = this.auftrag();
      this.modusForm.controls.billing_mode.setValue(a.billing_mode, { emitEvent: false });
      this.modusGewaehlt.set(a.billing_mode);
      this.laden(a.id);
      this.ladeAngebote(a);
    });

    this.modusForm.controls.billing_mode.valueChanges.subscribe((m) => {
      this.modusGewaehlt.set(m);
      this.modusFehler.set(null);
    });
  }

  private laden(id: string): void {
    const rid = ++this.reqId;
    this.zustand.set({ kind: 'loading' });
    this.svc.offeneAbrechnung(id).subscribe({
      next: (daten) => {
        if (rid === this.reqId) this.zustand.set({ kind: 'ready', daten });
      },
      error: (err) => {
        if (rid !== this.reqId) return;
        const verboten = err instanceof HttpErrorResponse && err.status === 403;
        this.zustand.set(verboten ? { kind: 'forbidden' } : { kind: 'error' });
      },
    });
  }

  /**
   * Angebote der Liegenschaft laden und auf DIESEN Auftrag filtern. Die Liste
   * trägt `work_order_id` bereits mit — es braucht keinen Nachladepfad je Angebot.
   */
  private ladeAngebote(a: WorkOrderDetail): void {
    if (!this.darfAbrechnen()) return;
    this.angeboteLaden.set(true);
    this.angebote.set([]);
    this.belegSvc.list({ page: 1, page_size: 100, property_id: a.property.id }).subscribe({
      next: (p) => {
        this.angebote.set(p.items.filter((q) => q.work_order_id === a.id));
        this.angeboteLaden.set(false);
      },
      error: () => this.angeboteLaden.set(false),
    });
  }

  neuLaden(): void {
    this.laden(this.auftrag().id);
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  // --- Abrechnungsart umstellen --------------------------------------------
  modusSpeichern(): void {
    if (this.modusLaedt() || !this.modusGeaendert()) return;
    this.modusFehler.set(null);
    this.meldung.set(null);
    this.modusLaedt.set(true);
    const modus = this.modusGewaehlt();
    this.svc.setBillingMode(this.auftrag().id, { billing_mode: modus }).subscribe({
      next: (a) => {
        this.modusLaedt.set(false);
        this.geaendert.emit(a);
        this.meldung.set({
          art: 'erfolg',
          text: `Abrechnungsart auf ${this.modusLabel(modus)} umgestellt.`,
        });
        this.laden(a.id);
      },
      error: (err) => {
        this.modusLaedt.set(false);
        // Der Server begründet die Sperre ausführlich (Doppelabrechnung). Wörtlich
        // zeigen — hier eine eigene Formulierung zu erfinden, wäre eine Lüge.
        this.modusFehler.set(
          fehlerDetail(err) ?? 'Die Abrechnungsart konnte nicht geändert werden.',
        );
        // Auswahl auf den echten Serverzustand zurückdrehen.
        this.modusForm.controls.billing_mode.setValue(this.auftrag().billing_mode, {
          emitEvent: false,
        });
        this.modusGewaehlt.set(this.auftrag().billing_mode);
      },
    });
  }

  // --- Rechnung aus Angebot (PAUSCHAL) -------------------------------------
  ausAngebot(): void {
    if (this.aktionLaedt()) return;
    this.angebotForm.markAllAsTouched();
    if (this.angebotForm.invalid) return;
    this.meldung.set(null);
    this.aktionLaedt.set(true);
    this.belegSvc
      .rechnungAusAngebot({ quote_id: this.angebotForm.controls.quote_id.value })
      .subscribe({
        next: (rechnung) => {
          this.aktionLaedt.set(false);
          void this.router.navigate(['/rechnungen', rechnung.id]);
        },
        error: (err) => {
          this.aktionLaedt.set(false);
          this.meldung.set({
            art: 'fehler',
            text: fehlerDetail(err) ?? 'Die Rechnung konnte nicht erzeugt werden.',
          });
        },
      });
  }

  // --- Rechnung aus Auftrag (REGIE) ----------------------------------------
  ausAuftrag(): void {
    if (this.aktionLaedt()) return;
    this.regieForm.markAllAsTouched();
    if (this.regieForm.invalid) return;
    this.meldung.set(null);
    this.regieLauf({});
  }

  /** Aus der Klärungsmaske: **derselbe** Aufruf, diesmal mit genannten Preisen. */
  klaerungAbsenden(preise: Record<string, string>): void {
    this.klaerungFehler.set(null);
    this.regieLauf(preise);
  }

  klaerungAbbrechen(): void {
    if (this.aktionLaedt()) return;
    this.klaerungOffen.set(false);
    this.klaerungPositionen.set([]);
    this.klaerungFehler.set(null);
  }

  /**
   * Der Regielauf — einmal für den ersten Versuch, einmal für die Klärung.
   *
   * Ein 422 mit `preis_unbekannt` ist **kein Fehler, sondern eine Aufgabe**: Er
   * öffnet die Klärungsmaske. Ein 422 ohne Liste ist ein echter Fehler und wird
   * als solcher gezeigt — dort, wo der Nutzer gerade hinsieht (im Dialog, wenn er
   * offen ist).
   */
  private regieLauf(preise: Record<string, string>): void {
    const v = this.regieForm.getRawValue();
    const payload: RechnungAusAuftrag = {
      work_order_id: this.auftrag().id,
      tax_code: v.tax_code,
      mit_berichten: v.mit_berichten,
      mit_zeiten: v.mit_zeiten,
      preise,
    };
    this.aktionLaedt.set(true);
    this.belegSvc.rechnungAusAuftrag(payload).subscribe({
      next: (rechnung) => {
        this.aktionLaedt.set(false);
        this.klaerungOffen.set(false);
        this.klaerungPositionen.set([]);
        void this.router.navigate(['/rechnungen', rechnung.id]);
      },
      error: (err) => {
        this.aktionLaedt.set(false);
        const klaerung = this.klaerungAus(err);
        if (klaerung) {
          this.klaerungPositionen.set(klaerung);
          this.klaerungFehler.set(null);
          this.klaerungOffen.set(true);
          return;
        }
        const text = fehlerDetail(err) ?? 'Die Rechnung konnte nicht erzeugt werden.';
        if (this.klaerungOffen()) this.klaerungFehler.set(text);
        else this.meldung.set({ art: 'fehler', text });
      },
    });
  }

  /** Die strukturierte Klärungsliste aus einem 422 — oder null. */
  private klaerungAus(err: unknown): PreisKlaerungPos[] | null {
    if (!(err instanceof HttpErrorResponse) || err.status !== 422) return null;
    const liste = (err.error as { preis_unbekannt?: unknown } | null)?.preis_unbekannt;
    if (!Array.isArray(liste) || liste.length === 0) return null;
    return liste as PreisKlaerungPos[];
  }

  // --- Darstellungshelfer ---------------------------------------------------
  modusLabel(m: BillingMode): string {
    return m === 'REGIE' ? 'Regie' : 'Pauschal';
  }

  /** Reine Anzeige — mit Tausenderpunkt (nie in ein Eingabefeld). */
  euro(betrag: string | null): string {
    if (betrag === null || betrag === '') return '—';
    return `${apiZuDeAnzeige(betrag, 2)} €`;
  }

  menge(wert: string | null, einheit: string | null): string {
    if (wert === null || wert === '') return '—';
    const zahl = apiZuDeAnzeige(wert);
    return einheit ? `${zahl} ${einheit}` : zahl;
  }

  datum(iso: string | null): string {
    return iso ? isoDatumDe(iso) : '—';
  }

  unbekannt(p: OffeneBerichtsposition | OffeneZeitgruppe): boolean {
    return p.preis_status === 'UNBEKANNT';
  }

  berichtStatusLabel(s: string): string {
    return s === 'ENTWURF' ? 'Entwurf' : s;
  }
}
