import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { BuchhaltungService } from '../../core/buchhaltung.service';
import { AuthService } from '../../core/auth.service';
import {
  MahnlaufCandidate,
  MahnlaufResult,
  euro,
} from '../../core/buchhaltung.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; stichtag: string; candidates: MahnlaufCandidate[] }
  | VerbotenState
  | { kind: 'error' };

/**
 * Mahnlauf (semi-automatisch): zeigt alle Rechnungen, die zum Stichtag für ihre
 * nächste Mahnstufe fällig sind, lässt sie auswählen und stellt die Stufen im
 * Stapel aus — optional gleich per E-Mail an den Schuldner. Der Nutzer bestätigt
 * (nichts läuft automatisch). Recht invoicing/VERSENDEN für das Ausführen.
 */
@Component({
  selector: 'app-mahnlauf',
  imports: [FormsModule, RouterLink, KeinZugriff],
  templateUrl: './mahnlauf.html',
  styleUrl: './mahnlauf.scss',
})
export class Mahnlauf {
  private readonly svc = inject(BuchhaltungService);
  private readonly auth = inject(AuthService);

  protected readonly euro = euro;
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly darfVersenden = computed(() =>
    this.auth.darf('invoicing', 'VERSENDEN'),
  );

  /** Heute als ISO-Datum (Vorgabe-Stichtag); vom Nutzer änderbar. */
  protected readonly stichtag = signal<string>(new Date().toISOString().slice(0, 10));
  protected readonly sendEmail = signal(true);
  /** Ausgewählte Rechnungen (invoice_id). */
  protected readonly ausgewaehlt = signal<Set<string>>(new Set());
  protected readonly laeuft = signal(false);
  protected readonly meldung = signal<string | null>(null);
  protected readonly ergebnis = signal<MahnlaufResult | null>(null);

  private readonly dateFmt = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  });

  protected readonly anzahlAusgewaehlt = computed(() => this.ausgewaehlt().size);

  constructor() {
    this.laden();
  }

  protected datum(iso: string | null): string {
    return iso ? this.dateFmt.format(new Date(iso)) : '—';
  }

  protected hatHinweise(res: MahnlaufResult): boolean {
    return res.results.some((r) => r.detail);
  }

  protected statusLabel(status: string): string {
    switch (status) {
      case 'sent':
        return 'Versendet';
      case 'issued':
        return 'Ausgestellt';
      case 'skipped':
        return 'Übersprungen';
      case 'failed':
        return 'Fehlgeschlagen';
      default:
        return status;
    }
  }

  private laden(): void {
    this.state.set({ kind: 'loading' });
    this.ergebnis.set(null);
    this.meldung.set(null);
    this.svc.mahnlaufVorschau(this.stichtag()).subscribe({
      next: (v) => {
        this.state.set({ kind: 'ready', stichtag: v.stichtag, candidates: v.candidates });
        // Standard: alle Kandidaten vorausgewählt.
        this.ausgewaehlt.set(new Set(v.candidates.map((c) => c.invoice_id)));
      },
      error: (err: unknown) => this.state.set(fehlerState(err)),
    });
  }

  protected aktualisieren(): void {
    this.laden();
  }

  protected istAusgewaehlt(id: string): boolean {
    return this.ausgewaehlt().has(id);
  }

  protected umschalten(id: string): void {
    const menge = new Set(this.ausgewaehlt());
    if (menge.has(id)) menge.delete(id);
    else menge.add(id);
    this.ausgewaehlt.set(menge);
  }

  protected alleUmschalten(candidates: MahnlaufCandidate[]): void {
    if (this.ausgewaehlt().size === candidates.length) {
      this.ausgewaehlt.set(new Set());
    } else {
      this.ausgewaehlt.set(new Set(candidates.map((c) => c.invoice_id)));
    }
  }

  protected starten(candidates: MahnlaufCandidate[]): void {
    if (this.laeuft() || !this.darfVersenden()) return;
    const gewaehlt = candidates.filter((c) => this.ausgewaehlt().has(c.invoice_id));
    if (gewaehlt.length === 0) return;
    this.meldung.set(null);
    this.laeuft.set(true);
    this.svc
      .mahnlaufAusfuehren({
        items: gewaehlt.map((c) => ({ invoice_id: c.invoice_id, level: c.next_level })),
        send_email: this.sendEmail(),
        stichtag: this.stichtag(),
      })
      .subscribe({
        next: (res) => {
          this.laeuft.set(false);
          this.ergebnis.set(res);
        },
        error: (err: unknown) => {
          this.laeuft.set(false);
          this.meldung.set(
            fehlerDetail(err) ?? 'Der Mahnlauf konnte nicht ausgeführt werden.',
          );
        },
      });
  }
}
