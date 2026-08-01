import { Component, ElementRef, computed, inject, signal, viewChild } from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import { HttpErrorResponse } from '@angular/common/http';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { OeffentlichesAngebotService } from '../../core/oeffentliches-angebot.service';
import {
  Entscheidung,
  EntscheidungErgebnis,
  OeffentlichePosition,
  // Alias: Die Komponente heißt genauso wie der Datentyp — der Name passt für
  // beides, und der Typ tritt hier nur wenige Male auf.
  OeffentlichesAngebot as AngebotDaten,
  ausgangLabel,
} from '../../core/oeffentliches-angebot.model';
import { isoDatumDe } from '../../shared/datum';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: AngebotDaten }
  | { kind: 'fertig'; data: EntscheidungErgebnis }
  | { kind: 'ungueltig'; detail: string }
  | { kind: 'gebremst'; detail: string }
  | { kind: 'error'; detail: string };

/** Ein Abschnitt des Belegs mit seinen Positionen (rein fürs Rendern). */
interface Block {
  titel: string | null;
  beschreibung: string | null;
  positionen: OeffentlichePosition[];
}

const TEXT_TITEL: Record<Entscheidung, string> = {
  ANGENOMMEN: 'Angebot verbindlich annehmen?',
  ABGELEHNT: 'Angebot ablehnen?',
};
const TEXT_INHALT: Record<Entscheidung, string> = {
  ANGENOMMEN:
    'Sie beauftragen uns damit zu den Bedingungen dieses Angebots. Die Zusage lässt sich über diesen Link nicht zurücknehmen — bitte rufen Sie uns an, wenn sich etwas ändert.',
  ABGELEHNT:
    'Sie teilen uns mit, dass Sie das Angebot nicht beauftragen. Auch das lässt sich über diesen Link nicht zurücknehmen.',
};
const TEXT_KNOPF: Record<Entscheidung, string> = {
  ANGENOMMEN: 'Verbindlich annehmen',
  ABGELEHNT: 'Ablehnen',
};

/** Positionsarten, die keinen Betrag tragen — sie bekommen keine Zahlenspalten. */
const OHNE_BETRAG = new Set(['TEXT']);

/**
 * Die öffentliche Angebotsseite (`/angebot/:token`).
 *
 * Läuft **außerhalb** des Leitstands: kein Auth-Guard, keine Bereichsnavigation,
 * kein Profil. Wer sie sieht, ist ein Kunde mit einem Link aus einer E-Mail —
 * möglicherweise auf dem Handy, möglicherweise mit Screenreader. Deshalb:
 *
 * * **Status nie allein über Farbe** (jeder Ausgang trägt Wort und Zeichen),
 * * eine echte Tabelle mit `<caption>` und `scope`-Kopfzellen statt Divs,
 * * die unumkehrbare Entscheidung hinter `shared/bestaetigung` (dort ist
 *   „Bestätigen" nie der Startfokus — ein reflexartiges Enter beauftragt nichts),
 * * Meldungen in `role="alert"`/`role="status"` mit Fokus, damit ein
 *   Screenreader-Nutzer den Ausgang mitbekommt und nicht ins Leere tabbt.
 *
 * Ein widerrufener oder abgelaufener Link führt zur **gleichen** Seite wie ein
 * unbekannter — das ist keine Nachlässigkeit, sondern die serverseitige Zusage
 * „kein Orakel", die hier nicht unterlaufen werden darf (auch nicht im
 * Kleingedruckten der Fehlerseite).
 *
 * Ein bereits **eingelöster** Link gehört ausdrücklich nicht dazu: Er führt bis
 * zum Ablaufdatum weiter auf den Beleg, nur mit dem Abschnitt „Stand" statt der
 * Rückmeldung. Wer geantwortet hat, soll seine Antwort wiederfinden — sonst
 * zweifelt er daran, dass sie angekommen ist, und ruft an.
 */
@Component({
  selector: 'app-oeffentliches-angebot',
  imports: [Bestaetigung],
  templateUrl: './oeffentliches-angebot.html',
  styleUrl: './oeffentliches-angebot.scss',
})
export class OeffentlichesAngebot {
  private readonly route = inject(ActivatedRoute);
  private readonly api = inject(OeffentlichesAngebotService);

  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly fragen = signal<Entscheidung | null>(null);
  protected readonly sendet = signal(false);
  protected readonly fehler = signal<string | null>(null);

  private readonly meldeBox = viewChild<ElementRef<HTMLElement>>('meldeBox');
  private token = '';

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  protected readonly ergebnis = computed(() => {
    const s = this.state();
    return s.kind === 'fertig' ? s.data : null;
  });

  /**
   * Positionen nach Abschnitten gruppiert. Ohne Abschnitte bleibt genau ein
   * namenloser Block — die Vorlage braucht dann keinen Sonderfall.
   */
  protected readonly bloecke = computed<Block[]>(() => {
    const d = this.daten();
    if (!d) return [];
    if (!d.rubriken.length) {
      return [{ titel: null, beschreibung: null, positionen: d.positionen }];
    }
    const blocks: Block[] = [];
    const ohne = d.positionen.filter((p) => p.rubrik === null);
    if (ohne.length) blocks.push({ titel: null, beschreibung: null, positionen: ohne });
    for (const r of d.rubriken) {
      blocks.push({
        titel: r.title,
        beschreibung: r.description,
        positionen: d.positionen.filter((p) => p.rubrik === r.position_number),
      });
    }
    return blocks;
  });

  constructor() {
    this.token = this.route.snapshot.paramMap.get('token') ?? '';
    this.laden();
  }

  protected laden(): void {
    this.state.set({ kind: 'loading' });
    this.api.laden(this.token).subscribe({
      next: (data) => this.state.set({ kind: 'ready', data }),
      error: (err: HttpErrorResponse) => this.state.set(this.zuFehler(err)),
    });
  }

  private zuFehler(err: HttpErrorResponse): ViewState {
    const detail =
      typeof err?.error?.detail === 'string' && err.error.detail.trim()
        ? err.error.detail
        : 'Die Seite konnte nicht geladen werden.';
    if (err.status === 404) return { kind: 'ungueltig', detail };
    if (err.status === 429) return { kind: 'gebremst', detail };
    return { kind: 'error', detail };
  }

  // --- Entscheiden ---------------------------------------------------------

  protected fragenOeffnen(was: Entscheidung): void {
    this.fehler.set(null);
    this.fragen.set(was);
  }

  protected fragenSchliessen(): void {
    if (this.sendet()) return;
    this.fragen.set(null);
  }

  protected bestaetigen(): void {
    const was = this.fragen();
    if (!was || this.sendet()) return;
    this.sendet.set(true);
    this.api.entscheiden(this.token, was).subscribe({
      next: (data) => {
        this.sendet.set(false);
        this.fragen.set(null);
        this.state.set({ kind: 'fertig', data });
      },
      error: (err: HttpErrorResponse) => {
        this.sendet.set(false);
        this.fragen.set(null);
        // 404 heißt hier: der Link wurde zwischenzeitlich widerrufen oder ist
        // abgelaufen. Das ist kein Formularfehler, sondern ein anderer
        // Seitenzustand — sonst stünde der Kunde vor Knöpfen, die nichts mehr tun.
        if (err.status === 404 || err.status === 429) {
          this.state.set(this.zuFehler(err));
          return;
        }
        // 409: In derselben Sekunde ist schon eine Rückmeldung eingegangen
        // (zweiter Klick, zweites Gerät). Neu laden zeigt den tatsächlichen
        // Ausgang — eine Fehlermeldung ließe den Kunden im Unklaren, ob nun
        // etwas passiert ist oder nicht.
        if (err.status === 409) {
          this.laden();
          return;
        }
        const detail =
          typeof err?.error?.detail === 'string' && err.error.detail.trim()
            ? err.error.detail
            : 'Ihre Rückmeldung konnte nicht gespeichert werden. Bitte versuchen Sie es erneut.';
        this.fehlerZeigen(detail);
      },
    });
  }

  private fehlerZeigen(text: string): void {
    this.fehler.set(text);
    setTimeout(() => this.meldeBox()?.nativeElement.focus(), 0);
  }

  // --- Texte für die Vorlage ----------------------------------------------

  protected titelFuer(was: Entscheidung | null): string {
    return was ? TEXT_TITEL[was] : '';
  }
  protected textFuer(was: Entscheidung | null): string {
    return was ? TEXT_INHALT[was] : '';
  }
  protected knopfFuer(was: Entscheidung | null): string {
    return was ? TEXT_KNOPF[was] : 'Bestätigen';
  }

  protected ausgangLabel(status: string): string {
    return ausgangLabel(status);
  }

  protected ausgangKlasse(status: string): string {
    if (status === 'ANGENOMMEN') return 'stamp--positive';
    if (status === 'ABGELEHNT') return 'stamp--negativ';
    return 'stamp--warn';
  }

  /** Zeichen neben dem Wort — Status darf nie nur an der Farbe hängen. */
  protected ausgangZeichen(status: string): string {
    if (status === 'ANGENOMMEN') return '✓';
    if (status === 'ABGELEHNT') return '✕';
    return '!';
  }

  protected hatBetrag(p: OeffentlichePosition): boolean {
    return !OHNE_BETRAG.has(p.line_type);
  }

  protected istNachrichtlich(p: OeffentlichePosition): boolean {
    return p.line_kind === 'ALTERNATIV' || p.line_kind === 'BEDARF';
  }

  protected kindLabel(p: OeffentlichePosition): string {
    if (p.line_kind === 'ALTERNATIV') return 'Alternative — nicht in der Summe';
    if (p.line_kind === 'BEDARF') return 'Bedarfsposition — nur bei Bedarf';
    return '';
  }

  protected datum(iso: string | null): string {
    return iso ? isoDatumDe(iso) : '–';
  }

  protected datumZeit(iso: string | null): string {
    if (!iso) return '–';
    return isoDatumDe(iso.slice(0, 10));
  }

  /**
   * Zeitpunkt der Entscheidung, in der Zeitzone des Lesers.
   *
   * Hier ist `new Date(...)` richtig — anders als bei einem Belegdatum: Der
   * Server liefert einen echten Zeitstempel MIT Offset, und der Kunde soll
   * lesen, wann *er* geantwortet hat.
   */
  protected datumZeitGenau(iso: string | null): string {
    if (!iso) return '–';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return this.datumZeit(iso);
    return new Intl.DateTimeFormat('de-DE', {
      dateStyle: 'long',
      timeStyle: 'short',
    }).format(d);
  }

  /** Geld nur zur ANZEIGE aus dem String lesen — nie zurück ins Datenmodell. */
  protected geld(wert: string | null, waehrung = 'EUR'): string {
    if (wert === null || wert === '') return '–';
    const zahl = Number(wert);
    if (!Number.isFinite(zahl)) return wert;
    return new Intl.NumberFormat('de-DE', {
      style: 'currency',
      currency: waehrung || 'EUR',
    }).format(zahl);
  }

  protected menge(wert: string | null): string {
    if (wert === null || wert === '') return '';
    const zahl = Number(wert);
    if (!Number.isFinite(zahl)) return wert;
    return new Intl.NumberFormat('de-DE', { maximumFractionDigits: 3 }).format(zahl);
  }

  protected prozent(wert: string | null): string {
    if (wert === null || wert === '') return '';
    const zahl = Number(wert);
    if (!Number.isFinite(zahl)) return wert;
    return `${new Intl.NumberFormat('de-DE', { maximumFractionDigits: 2 }).format(zahl)} %`;
  }
}
