import { Component, ElementRef, computed, inject, signal, viewChild } from '@angular/core';
import { RouterLink } from '@angular/router';
import { KiService } from '../../core/ki.service';
import {
  AssistentIntent,
  FrageAntwort,
  Gespraech,
  GespraechDetail,
  Quelle,
  intentLabel,
  quelleDossierPfad,
  quelleTypLabel,
} from '../../core/ki.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { VerbotenState, fehlerDetail, fehlerState, istVerboten } from '../../shared/http-fehler';

/** Zustand der Gesprächsliste (linke Spalte) — sie ist das Rechte-Tor der Ansicht. */
type ListState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: Gespraech[] }
  | VerbotenState
  | { kind: 'error' };

/**
 * „Frag das CRM" — der konversationelle Assistent. Links die eigenen Gespräche,
 * rechts der Verlauf des aktiven Gesprächs plus Eingabe. Jede Frage läuft
 * serverseitig gegroundet über Suche + Dossier (mit den Rechten des Anmelders);
 * fällt das Modell aus, kommt eine deterministische Trefferzusammenfassung.
 */
@Component({
  selector: 'app-ki-assistent',
  imports: [RouterLink, KeinZugriff, Bestaetigung],
  templateUrl: './ki-assistent.html',
  styleUrl: './ki-assistent.scss',
})
export class KiAssistent {
  private readonly svc = inject(KiService);

  protected readonly state = signal<ListState>({ kind: 'loading' });
  protected readonly aktiv = signal<GespraechDetail | null>(null);
  protected readonly detailLaedt = signal(false);
  protected readonly eingabe = signal('');
  protected readonly sendet = signal(false);
  protected readonly fehler = signal<string | null>(null);

  // Löschbestätigung
  protected readonly loeschAktion = signal<Gespraech | null>(null);
  protected readonly loeschLaedt = signal(false);

  private readonly verlaufEl = viewChild<ElementRef<HTMLElement>>('verlauf');

  private reqId = 0;
  private detailReq = 0;

  private readonly zeitFmt = new Intl.DateTimeFormat('de-DE', {
    hour: '2-digit',
    minute: '2-digit',
  });

  protected readonly gespraeche = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : [];
  });

  protected readonly listenText = computed(() => {
    const s = this.state();
    if (s.kind === 'loading') return 'Gespräche werden geladen.';
    if (s.kind === 'forbidden') return 'Keine Berechtigung für den KI-Assistenten.';
    if (s.kind === 'error') return 'Gespräche konnten nicht geladen werden.';
    if (s.data.length === 0) return 'Noch keine Gespräche.';
    return s.data.length === 1 ? '1 Gespräch.' : `${s.data.length} Gespräche.`;
  });

  constructor() {
    this.ladeListe();
  }

  // ---- Gesprächsliste -----------------------------------------------------
  ladeListe(): void {
    this.ladeListeIntern(false);
  }

  /**
   * Stiller Reload nach einer Antwort: aktualisiert die Liste nur bei Erfolg und
   * verdeckt bei einem Fehler NICHT die gerade erhaltene Antwort (kein Sprung in den
   * ganzseitigen Fehler-/Kein-Zugriff-Zweig).
   */
  private aktualisiereListe(): void {
    this.ladeListeIntern(true);
  }

  private ladeListeIntern(stumm: boolean): void {
    const id = ++this.reqId;
    if (!stumm) this.state.set({ kind: 'loading' });
    this.svc.gespraeche().subscribe({
      next: (data) => {
        if (id === this.reqId) this.state.set({ kind: 'ready', data });
      },
      error: (err) => {
        if (id === this.reqId && !stumm) this.state.set(fehlerState(err));
      },
    });
  }

  retry(): void {
    this.ladeListe();
  }

  neuesGespraech(): void {
    // Einen etwaigen laufenden Detail-Load verwerfen, damit er das frische Gespräch
    // nicht nachträglich überschreibt.
    this.detailReq++;
    this.detailLaedt.set(false);
    this.aktiv.set(null);
    this.eingabe.set('');
    this.fehler.set(null);
  }

  istAktiv(g: Gespraech): boolean {
    return this.aktiv()?.id === g.id;
  }

  oeffne(g: Gespraech): void {
    if (this.aktiv()?.id === g.id) return;
    const id = ++this.detailReq;
    this.detailLaedt.set(true);
    this.fehler.set(null);
    this.svc.gespraech(g.id).subscribe({
      next: (d) => {
        if (id === this.detailReq) {
          this.aktiv.set(d);
          this.detailLaedt.set(false);
          this.scrolleAnsEnde();
        }
      },
      error: (err) => {
        if (id === this.detailReq) {
          this.detailLaedt.set(false);
          this.fehler.set(this.fehlerText(err));
        }
      },
    });
  }

  // ---- Fragen -------------------------------------------------------------
  tastatur(ev: KeyboardEvent): void {
    // Enter sendet, Shift+Enter macht einen Zeilenumbruch.
    if (ev.key === 'Enter' && !ev.shiftKey) {
      ev.preventDefault();
      this.senden();
    }
  }

  senden(): void {
    const frage = this.eingabe().trim();
    if (!frage || this.sendet()) return;
    this.sendet.set(true);
    this.fehler.set(null);
    // Das Ziel-Gespräch beim Absenden festhalten (null = neues Gespräch), damit eine
    // spät eintreffende Antwort nicht in eine inzwischen gewechselte Ansicht schreibt.
    const zielId = this.aktiv()?.id ?? null;
    this.svc.frage(frage, zielId ?? undefined).subscribe({
      next: (res) => this.antwortEinfuegen(res, frage, zielId),
      error: (err) => {
        this.sendet.set(false);
        this.fehler.set(this.fehlerText(err));
      },
    });
  }

  private antwortEinfuegen(res: FrageAntwort, frage: string, zielId: string | null): void {
    this.eingabe.set('');
    this.sendet.set(false);
    // Hat der Nutzer während des Flugs das Gespräch gewechselt (oder „Neues Gespräch"
    // geklickt)? Dann ist die Antwort serverseitig gespeichert, darf aber die jetzt
    // sichtbare Ansicht nicht überschreiben.
    const aktuellId = this.aktiv()?.id ?? null;
    if (zielId !== aktuellId) return;

    const aktuell = this.aktiv();
    if (aktuell && aktuell.id === res.conversation_id) {
      this.aktiv.set({
        ...aktuell,
        turns: [...aktuell.turns, res.frage, res.antwort],
        updated_at: res.antwort.created_at,
      });
    } else {
      // Neues Gespräch — Detail lokal aufbauen und die Sidebar still nachladen.
      this.aktiv.set({
        id: res.conversation_id,
        title: frage.slice(0, 120),
        status: 'ACTIVE',
        created_at: res.frage.created_at,
        updated_at: res.antwort.created_at,
        turns: [res.frage, res.antwort],
      });
      this.aktualisiereListe();
    }
    this.scrolleAnsEnde();
  }

  // ---- Löschen ------------------------------------------------------------
  loeschenOeffnen(g: Gespraech, ev: Event): void {
    ev.stopPropagation();
    this.loeschAktion.set(g);
  }

  loeschenAbbrechen(): void {
    if (!this.loeschLaedt()) this.loeschAktion.set(null);
  }

  loeschenBestaetigen(): void {
    const g = this.loeschAktion();
    if (!g || this.loeschLaedt()) return;
    this.loeschLaedt.set(true);
    this.svc.gespraechLoeschen(g.id).subscribe({
      next: () => {
        this.loeschLaedt.set(false);
        this.loeschAktion.set(null);
        if (this.aktiv()?.id === g.id) this.aktiv.set(null);
        this.ladeListe();
      },
      error: (err) => {
        this.loeschLaedt.set(false);
        this.loeschAktion.set(null);
        this.fehler.set(this.fehlerText(err));
      },
    });
  }

  // ---- Helfer -------------------------------------------------------------
  private scrolleAnsEnde(): void {
    // Nach dem Render ans Ende des Verlaufs springen (zoneless: nächster Tick).
    setTimeout(() => {
      const el = this.verlaufEl()?.nativeElement;
      if (el) el.scrollTop = el.scrollHeight;
    }, 0);
  }

  private fehlerText(err: unknown): string {
    if (istVerboten(err)) return fehlerDetail(err) ?? 'Keine Berechtigung.';
    return fehlerDetail(err) ?? 'Das hat nicht geklappt. Bitte erneut versuchen.';
  }

  quellePfad(q: Quelle): string | null {
    return quelleDossierPfad(q);
  }
  quelleTyp(t: string): string {
    return quelleTypLabel(t);
  }
  intentText(i: AssistentIntent | null): string | null {
    return intentLabel(i);
  }
  zeit(iso: string): string {
    return this.zeitFmt.format(new Date(iso));
  }
}
