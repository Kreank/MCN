import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { Router } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import { AuftragService } from '../../core/auftrag.service';
import { BelegService } from '../../core/beleg.service';
import { AuthService } from '../../core/auth.service';
import { NachtragPosition, NachtragVorschau, WorkOrderDetail } from '../../core/auftrag.model';
import { PreisKlaerung as PreisKlaerungPos } from '../../core/beleg.model';
import { PreisKlaerung } from '../../shared/preis-klaerung/preis-klaerung';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { fehlerDetail } from '../../shared/http-fehler';
import { apiZuDeAnzeige } from '../../shared/formular/dezimal';

type Zustand =
  | { kind: 'loading' }
  | { kind: 'ready'; daten: NachtragVorschau }
  | { kind: 'forbidden' }
  | { kind: 'error' };

const TAX_CODE_OPTIONEN: FeldOption[] = [
  { wert: 'DE_19', label: 'USt 19 %' },
  { wert: 'DE_7', label: 'USt 7 %' },
  { wert: 'DE_0', label: 'Steuerfrei (0 %)' },
  { wert: 'DE_13B', label: '§ 13b UStG (Reverse Charge)' },
];

/**
 * **Nachtrag abrechnen** — die Rechnung aus den Abweichungen des Soll-Ist.
 *
 * Der Mehrverbrauch war bisher *sichtbar, aber nicht abrechenbar*: Der Abgleich
 * wies ihn sauber aus, und dann war Schluss — das Büro tippte die
 * Nachtragsrechnung von Hand ab. Genau die Handarbeit, die dieses System
 * beseitigen soll (und die Fehlerquelle, um die es geht: „das ist Geld, das im
 * Handwerk regelmäßig verlorengeht").
 *
 * Die drei Regeln, die dieser Abschnitt sichtbar macht:
 *
 * 1. **MEHRVERBRAUCH wird nur mit der Differenzmenge berechnet** (19 statt 18 →
 *    1 Stück). Die Sollmenge ist mit der Pauschale bezahlt. Die Tabelle zeigt Soll,
 *    Ist und die abzurechnende Menge nebeneinander — damit niemand raten muss.
 * 2. **ZUSATZ wird voll berechnet** (er war nie Teil der Pauschale).
 * 3. **MINDERVERBRAUCH und ENTFALLEN mindern die Pauschale nicht** — sie tauchen
 *    hier deshalb gar nicht auf. Das steht auch so da; eine Leerstelle ohne
 *    Erklärung wäre eine Einladung, das für einen Fehler zu halten.
 *
 * **Kein toter Knopf.** Gibt es nichts abzurechnen, sagt der Abschnitt warum:
 * keine Abweichung — oder alles schon fakturiert (mit Belegnummer). Und wenn
 * unsignierte Berichte herumliegen, wird auch das benannt: Ihre Mengen fließen
 * **nicht** ein, und sie sind der häufigste Grund für eine zu kleine Rechnung.
 *
 * **Keine Geldberechnung im Client.** Jede Summe kommt vom Server; ein unbekannter
 * Preis bleibt unbekannt (nie 0,00 €) und wird im Klärungsdialog abgefragt —
 * derselbe Weg wie beim Regielauf, kein zweiter.
 */
@Component({
  selector: 'app-nachtrag',
  imports: [ReactiveFormsModule, Feld, PreisKlaerung],
  templateUrl: './nachtrag.html',
  styleUrl: './nachtrag.scss',
})
export class Nachtrag {
  readonly auftrag = input.required<WorkOrderDetail>();

  private readonly svc = inject(AuftragService);
  private readonly belegSvc = inject(BelegService);
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly fb = inject(FormBuilder);

  protected readonly taxCodeOptionen = TAX_CODE_OPTIONEN;

  /** Rechnungen entstehen zu lassen ist eine kaufmännische Entscheidung — der
   *  Monteur darf das nicht (der Server setzt es durch, das UI zeigt es ehrlich). */
  protected readonly darfAbrechnen = computed(() => this.auth.darf('invoicing', 'ANLEGEN'));

  protected readonly zustand = signal<Zustand>({ kind: 'loading' });
  protected readonly fehler = signal<string | null>(null);
  protected readonly aktionLaedt = signal(false);
  private reqId = 0;

  protected readonly vorschau = computed(() => {
    const z = this.zustand();
    return z.kind === 'ready' ? z.daten : null;
  });

  /** Es gibt eine Abweichung, sie ist aber vollständig fakturiert. */
  protected readonly allesAbgerechnet = computed(() => {
    const v = this.vorschau();
    return !!v && v.positionen.length === 0 && v.bereits_abgerechnet.length > 0;
  });

  protected readonly form = this.fb.group({
    tax_code: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
  });

  // --- Preisklärung (derselbe Weg wie beim Regielauf) -----------------------
  protected readonly klaerungOffen = signal(false);
  protected readonly klaerungPositionen = signal<PreisKlaerungPos[]>([]);
  protected readonly klaerungFehler = signal<string | null>(null);

  constructor() {
    effect(() => {
      const a = this.auftrag();
      if (a) this.laden(a.id);
    });
  }

  private laden(id: string): void {
    const rid = ++this.reqId;
    this.zustand.set({ kind: 'loading' });
    this.svc.nachtrag(id).subscribe({
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

  neuLaden(): void {
    this.laden(this.auftrag().id);
  }

  abrechnen(): void {
    if (this.aktionLaedt()) return;
    this.form.markAllAsTouched();
    if (this.form.invalid) return;
    this.fehler.set(null);
    this.lauf({});
  }

  /** Aus der Klärungsmaske: **derselbe** Aufruf, diesmal mit genannten Preisen. */
  klaerungAbsenden(preise: Record<string, string>): void {
    this.klaerungFehler.set(null);
    this.lauf(preise);
  }

  klaerungAbbrechen(): void {
    if (this.aktionLaedt()) return;
    this.klaerungOffen.set(false);
    this.klaerungPositionen.set([]);
    this.klaerungFehler.set(null);
  }

  /**
   * Der Nachtragslauf — einmal für den ersten Versuch, einmal für die Klärung.
   *
   * Ein 422 mit `preis_unbekannt` ist **kein Fehler, sondern eine Aufgabe**: Er
   * öffnet die Klärungsmaske. Ein 422 ohne Liste ist ein echter Fehler und wird
   * wörtlich gezeigt — der Server begründet die Sperre (Doppelabrechnung,
   * Abrechnungsart), und eine eigene Formulierung wäre hier eine Lüge.
   */
  private lauf(preise: Record<string, string>): void {
    this.aktionLaedt.set(true);
    this.belegSvc
      .rechnungAusNachtrag({
        work_order_id: this.auftrag().id,
        tax_code: this.form.controls.tax_code.value,
        preise,
      })
      .subscribe({
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
          const text =
            fehlerDetail(err) ?? 'Der Nachtrag konnte nicht abgerechnet werden.';
          if (this.klaerungOffen()) this.klaerungFehler.set(text);
          else this.fehler.set(text);
          this.laden(this.auftrag().id);
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
  artLabel(a: NachtragPosition['art']): string {
    return a === 'MEHRVERBRAUCH' ? 'Mehrverbrauch' : 'Zusatz';
  }

  artSymbol(a: NachtragPosition['art']): string {
    return a === 'MEHRVERBRAUCH' ? '▲' : '＋';
  }

  unbekannt(p: NachtragPosition): boolean {
    return p.preis_status === 'UNBEKANNT';
  }

  /** Reine Anzeige — mit Tausenderpunkt (nie in ein Eingabefeld). */
  euro(betrag: string | null): string {
    if (betrag === null || betrag === '') return '—';
    return `${apiZuDeAnzeige(betrag, 2)} €`;
  }

  menge(wert: string | null, einheit?: string | null): string {
    if (wert === null || wert === '') return '—';
    const zahl = apiZuDeAnzeige(wert);
    return einheit ? `${zahl} ${einheit}` : zahl;
  }
}
