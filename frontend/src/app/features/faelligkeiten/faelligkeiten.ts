import { Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { AuthService } from '../../core/auth.service';
import { FaelligkeitService } from '../../core/faelligkeit.service';
import {
  DueItem,
  DueItemPage,
  FOLGEAKTIONEN,
  FaelligkeitArt,
  FaelligkeitStatus,
  Folgeaktion,
  artClass,
  artLabel,
  folgeaktionLabel,
  fristText,
  statusLabel,
} from '../../core/faelligkeit.model';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { WartungNav } from '../wartung-nav/wartung-nav';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: DueItemPage }
  | VerbotenState
  | { kind: 'error' };

type Meldung = { art: 'erfolg' | 'fehler' | 'hinweis'; text: string };

type ArtSegment = { value: FaelligkeitArt | null; label: string };
type StatusSegment = { value: FaelligkeitStatus; label: string };

/**
 * „Was steht an?" — die Fälligkeiten-Ansicht über alle drei Fristenarten
 * (Wartung, Prüffrist, Gewährleistung).
 *
 * Aktionen je Eintrag: **Erledigen** (mit Folgeobjekt: Termin → Plantafel-
 * Rückstand, Auftrag, Angebot, Aufgabe, Projekt — oder nur vermerken) und
 * **Verwerfen** (begründungspflichtig, kein Löschen; Recht STORNIEREN).
 *
 * Das Produkt gibt **keine Rechtsauskunft** — das steht auch im UI.
 */
@Component({
  selector: 'app-faelligkeiten',
  imports: [
    RouterLink,
    ReactiveFormsModule,
    WartungNav,
    KeinZugriff,
    Dialog,
    Feld,
    Bestaetigung,
  ],
  templateUrl: './faelligkeiten.html',
  styleUrl: './faelligkeiten.scss',
})
export class Faelligkeiten {
  private readonly svc = inject(FaelligkeitService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  protected readonly darfErledigen = computed(() =>
    this.auth.darf('maintenance', 'AENDERN'),
  );
  /** Verwerfen ist ein eigenes Tor: eine Frist bewusst verstreichen lassen. */
  protected readonly darfVerwerfen = computed(() =>
    this.auth.darf('maintenance', 'STORNIEREN'),
  );

  protected readonly arten: ArtSegment[] = [
    { value: null, label: 'Alle Arten' },
    { value: 'WARTUNG', label: 'Wartung' },
    { value: 'PRUEFUNG', label: 'Prüffristen' },
    { value: 'GEWAEHRLEISTUNG', label: 'Gewährleistung' },
  ];
  protected readonly statusSegmente: StatusSegment[] = [
    { value: 'OFFEN', label: 'Offen' },
    { value: 'ERLEDIGT', label: 'Erledigt' },
    { value: 'VERWORFEN', label: 'Verworfen' },
  ];

  protected readonly folgeaktionen: FeldOption[] = FOLGEAKTIONEN.map((a) => ({
    wert: a,
    label: folgeaktionLabel(a),
  }));

  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly art = signal<FaelligkeitArt | null>(null);
  protected readonly status = signal<FaelligkeitStatus>('OFFEN');
  protected readonly bis = signal<string>('');
  protected readonly meldung = signal<Meldung | null>(null);

  /** Der Eintrag, für den gerade der Erledigen-Dialog offen ist. */
  protected readonly erledigenFuer = signal<DueItem | null>(null);
  /** Der Eintrag, für den gerade die Verwerfen-Bestätigung offen ist. */
  protected readonly verwerfenFuer = signal<DueItem | null>(null);
  protected readonly laeuft = signal(false);
  protected readonly formFehler = signal<string | null>(null);

  protected readonly form = this.fb.nonNullable.group({
    folgeaktion: 'TERMIN' as Folgeaktion,
    termin_datum: '',
    notiz: '',
  });

  /**
   * Bei „Termin" ist das Datum ein **Wunschtermin** (Vermerk am Einsatz), kein
   * Zeitraum: der Einsatz landet immer im Rückstand der Plantafel, weil die
   * Fälligkeit niemanden zuweist — und eine Kachel ohne Bahn wäre unsichtbar.
   */
  protected readonly istTermin = computed(
    () => this.form.controls.folgeaktion.value === 'TERMIN',
  );

  private reqId = 0;

  protected readonly artLabel = artLabel;
  protected readonly artClass = artClass;
  protected readonly statusLabel = statusLabel;
  protected readonly fristText = fristText;
  protected readonly folgeaktionLabel = folgeaktionLabel;

  constructor() {
    this.laden();
  }

  protected artWaehlen(a: FaelligkeitArt | null): void {
    this.art.set(a);
    this.laden();
  }

  protected statusWaehlen(s: FaelligkeitStatus): void {
    this.status.set(s);
    this.laden();
  }

  protected bisSetzen(wert: string): void {
    this.bis.set(wert);
    this.laden();
  }

  protected laden(): void {
    const id = ++this.reqId;
    this.state.set({ kind: 'loading' });
    this.svc
      .list({
        page: 1,
        page_size: 100,
        status: this.status(),
        kind: this.art(),
        bis: this.bis() || null,
      })
      .subscribe({
        next: (data) => {
          if (id !== this.reqId) return; // Race-Guard
          this.state.set({ kind: 'ready', data });
        },
        error: (err) => {
          if (id !== this.reqId) return;
          this.state.set(fehlerState(err));
        },
      });
  }

  // --- Erledigen ------------------------------------------------------------

  protected erledigenOeffnen(item: DueItem): void {
    this.formFehler.set(null);
    this.form.reset({
      folgeaktion: 'TERMIN',
      // Kein vorbelegtes Datum: der Termin geht so oder so in den Rückstand.
      // Ein Wunschtermin ist eine bewusste Angabe, keine Vorgabe des Systems.
      termin_datum: '',
      notiz: '',
    });
    this.erledigenFuer.set(item);
  }

  /** Übernimmt den vom Server berechneten Werktags-Vorschlag ins Datumsfeld. */
  protected vorschlagUebernehmen(): void {
    const item = this.erledigenFuer();
    if (item) this.form.controls.termin_datum.setValue(item.termin_vorschlag);
  }

  protected erledigen(): void {
    const item = this.erledigenFuer();
    if (!item || this.laeuft()) return;
    const werte = this.form.getRawValue();
    this.laeuft.set(true);
    this.formFehler.set(null);
    this.svc
      .erledigen(item.id, {
        folgeaktion: werte.folgeaktion,
        termin_datum:
          werte.folgeaktion === 'TERMIN' && werte.termin_datum
            ? werte.termin_datum
            : null,
        notiz: werte.notiz.trim() || null,
      })
      .subscribe({
        next: (res) => {
          this.laeuft.set(false);
          this.erledigenFuer.set(null);
          const wo = this.folgeZiel(res.item);
          this.meldung.set({
            art: res.hinweis ? 'hinweis' : 'erfolg',
            text: res.hinweis ? `${wo} ${res.hinweis}` : wo,
          });
          this.laden();
        },
        error: (err) => {
          this.laeuft.set(false);
          this.formFehler.set(this.fehlertext(err));
        },
      });
  }

  private folgeZiel(item: DueItem): string {
    switch (item.result_object_type) {
      case 'workflow.service_job':
        return 'Termin angelegt — er liegt jetzt im Rückstand der Plantafel.';
      case 'workflow.work_order':
        return 'Auftrag im Entwurf angelegt.';
      case 'workflow.project':
        return 'Projekt angelegt.';
      case 'workflow.task':
        return 'Aufgabe angelegt.';
      case 'invoicing.quote':
        return 'Angebot im Entwurf angelegt.';
      default:
        return 'Fälligkeit als erledigt vermerkt.';
    }
  }

  // --- Verwerfen ------------------------------------------------------------

  protected verwerfenOeffnen(item: DueItem): void {
    this.verwerfenFuer.set(item);
  }

  protected verwerfen(begruendung: string | null): void {
    const item = this.verwerfenFuer();
    if (!item || !begruendung) return;
    this.laeuft.set(true);
    this.svc.verwerfen(item.id, begruendung).subscribe({
      next: () => {
        this.laeuft.set(false);
        this.verwerfenFuer.set(null);
        this.meldung.set({
          art: 'erfolg',
          text: 'Fälligkeit verworfen. Der Eintrag bleibt mit Begründung erhalten.',
        });
        this.laden();
      },
      error: (err) => {
        this.laeuft.set(false);
        this.verwerfenFuer.set(null);
        this.meldung.set({ art: 'fehler', text: this.fehlertext(err) });
      },
    });
  }

  private fehlertext(err: unknown): string {
    const e = err as { error?: { detail?: string }; status?: number };
    if (e?.status === 403) {
      return 'Dafür fehlt Ihnen die Berechtigung.';
    }
    return e?.error?.detail ?? 'Die Aktion ist fehlgeschlagen.';
  }
}
