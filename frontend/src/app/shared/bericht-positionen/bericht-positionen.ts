import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Subject, debounceTime, distinctUntilChanged } from 'rxjs';
import { ArtikelService } from '../../core/artikel.service';
import { SiteReportService } from '../../core/site-report.service';
import {
  SiteReportLine,
  SiteReportLineIn,
  SiteReportLineType,
  SiteReportStatus,
  VorbelegbaresAngebot,
  siteReportLineTypeLabel,
} from '../../core/site-report.model';
import { Dialog } from '../dialog/dialog';
import { fehlerDetail } from '../http-fehler';
import {
  DEZIMAL_UNGUELTIG,
  apiZuDeAnzeige,
  apiZuDeEingabe,
  deZuApiDezimal,
} from '../formular/dezimal';

/** Palette-Modus: Artikelstamm oder Leistungen (Stücklisten). */
type PaletteModus = 'artikel' | 'leistungen';

type Zustand = 'loading' | 'ready' | 'error';

/**
 * Eine Position im Editor. **Ohne Preisfeld** — und zwar nicht „noch nicht",
 * sondern grundsätzlich: der Bericht führt keine Preise (Migration 0080).
 *
 * `menge` ist die **Eingabeform** (deutsches Komma, OHNE Tausenderpunkt —
 * `apiZuDeEingabe`). `planned_quantity` bleibt dagegen der rohe API-String: die
 * Sollmenge ist eingefroren und wird nie editiert, nur angezeigt.
 */
interface Pos {
  uid: string;
  line_type: SiteReportLineType;
  description: string;
  menge: string;
  unit: string;
  planned_quantity: string | null;
  source_article_id: string | null;
  source_assembly_id: string | null;
  source_quote_line_id: string | null;
  note: string | null;
}

interface Treffer {
  id: string;
  zeile1: string;
  zeile2: string;
  line_type: SiteReportLineType;
  unit: string;
}

/** Abweichung Ist gegen Soll — reine MENGEN-Anzeige, niemals ein Geldbetrag. */
interface Abweichung {
  soll: string;
  ist: string;
  differenz: string;
  richtung: 'mehr' | 'weniger' | 'gleich';
  label: string;
  symbol: string;
  klasse: string;
}

let uidSeq = 0;
const neueUid = () => `p${++uidSeq}`;

/**
 * Positionen eines Baustellenberichts: was tatsächlich verbaut/geleistet wurde
 * (Ist) — und, wo aus einem Angebot vorbelegt wurde, gegen die eingefrorene
 * Sollmenge gestellt.
 *
 * **Keine Preise.** Nirgends, auch nicht „nur zur Info": ein unterschriebener
 * Bericht mit Preisen wäre eine Preisvereinbarung. Der Preis entsteht in der
 * Rechnung.
 *
 * Bearbeitbar nur im **ENTWURF** (und mit `workflow/AENDERN`). Ist der Bericht
 * unterzeichnet, zeigt die Komponente die Positionen nur an — durchgesetzt wird
 * das ohnehin von den DB-Triggern; das UI spiegelt es.
 *
 * Gespeichert wird über `PUT …/positionen`, das **alle** Positionen ersetzt: der
 * Editor schickt deshalb immer den ganzen Satz.
 */
@Component({
  selector: 'app-bericht-positionen',
  imports: [Dialog],
  templateUrl: './bericht-positionen.html',
  styleUrl: './bericht-positionen.scss',
})
export class BerichtPositionen {
  private readonly svc = inject(SiteReportService);
  private readonly artikelSvc = inject(ArtikelService);

  readonly berichtId = input.required<string>();
  readonly status = input.required<SiteReportStatus>();
  /** Hängt der Bericht an einem Auftrag? Nur dann gibt es Angebote (kein freier Termin). */
  readonly hatAuftrag = input(false);
  readonly darfAendern = input(false);

  protected readonly zustand = signal<Zustand>('loading');
  protected readonly positionen = signal<Pos[]>([]);
  protected readonly geaendert = signal(false);
  protected readonly speichert = signal(false);
  protected readonly fehler = signal<string | null>(null);
  /** Statusmeldung für Screenreader (aria-live). */
  protected readonly ansage = signal('');

  private ladeReq = 0;

  protected readonly bearbeitbar = computed(
    () => this.status() === 'ENTWURF' && this.darfAendern(),
  );

  /** Vorbelegen lohnt nur bei leerem Bericht am Auftrag (der Server erzwingt es). */
  protected readonly vorbelegenMoeglich = computed(
    () => this.bearbeitbar() && this.hatAuftrag() && this.positionen().length === 0,
  );

  /** Führt der Bericht Positionen MIT Herkunft? Nur dann ist die Sperr-Legende ein Thema. */
  protected readonly hatVorbelegte = computed(() =>
    this.positionen().some((p) => !!p.source_quote_line_id),
  );

  // --- Palette -------------------------------------------------------------
  protected readonly paletteModus = signal<PaletteModus>('artikel');
  protected readonly paletteTreffer = signal<Treffer[]>([]);
  protected readonly paletteLaedt = signal(false);
  protected readonly paletteFehler = signal(false);
  private readonly paletteSuche$ = new Subject<string>();
  private paletteReq = 0;

  // --- Vorbelegen-Dialog ---------------------------------------------------
  protected readonly vorbelegenOffen = signal(false);
  protected readonly angebote = signal<VorbelegbaresAngebot[]>([]);
  protected readonly angeboteLaden = signal(false);
  protected readonly angebotWahl = signal<string>('');
  protected readonly dialogFehler = signal<string | null>(null);
  protected readonly dialogLaedt = signal(false);

  constructor() {
    // Neu laden, wenn der Bericht wechselt — UND wenn sich sein Status ändert
    // (Unterschrift besiegelt ihn: ab dann gilt der Serverstand, nicht ein
    // womöglich ungespeicherter Editorstand).
    effect(() => {
      const id = this.berichtId();
      this.status();
      if (id) this.laden(id);
    });

    this.paletteSuche$
      .pipe(debounceTime(300), distinctUntilChanged(), takeUntilDestroyed())
      .subscribe((q) => this.paletteFetch(q));
  }

  private laden(id: string): void {
    const rid = ++this.ladeReq;
    this.zustand.set('loading');
    this.fehler.set(null);
    this.svc.get(id).subscribe({
      next: (d) => {
        if (rid !== this.ladeReq) return;
        this.positionen.set((d.lines ?? []).map((l) => this.ausApi(l)));
        this.geaendert.set(false);
        this.zustand.set('ready');
        // Palette einmal vorbefüllen, sobald bearbeitet werden darf.
        if (this.bearbeitbar()) this.paletteFetch('');
      },
      error: () => {
        if (rid === this.ladeReq) this.zustand.set('error');
      },
    });
  }

  neuLaden(): void {
    this.laden(this.berichtId());
  }

  private ausApi(l: SiteReportLine): Pos {
    return {
      uid: neueUid(),
      line_type: l.line_type,
      description: l.description,
      // OHNE Tausenderpunkt — ein gruppierter Wert im Eingabefeld ist beim
      // Zurücklesen mehrdeutig („1.200" = 1200 oder 1,2?).
      menge: apiZuDeEingabe(l.quantity),
      unit: l.unit ?? '',
      planned_quantity: l.planned_quantity,
      source_article_id: l.source_article_id,
      source_assembly_id: l.source_assembly_id,
      source_quote_line_id: l.source_quote_line_id,
      note: l.note,
    };
  }

  // --- Bearbeiten ----------------------------------------------------------
  private aendern(uid: string, patch: Partial<Pos>): void {
    this.positionen.update((ps) => ps.map((p) => (p.uid === uid ? { ...p, ...patch } : p)));
    this.geaendert.set(true);
    this.fehler.set(null);
  }

  /**
   * Bezeichnung ändern — **nur ohne Herkunft**. Trägt die Zeile eine
   * Angebotsposition als Herkunft, ist ihre Bezeichnung eingefroren (sie ist
   * Identität, nicht Anzeige: sonst stünde die Sollmenge einer fremden Position
   * neben frei getipptem Text auf dem unterschriebenen Nachweis). Das Template
   * rendert dort gar kein Eingabefeld; die Wächter hier sind die zweite Schicht.
   */
  setBezeichnung(uid: string, wert: string): void {
    const p = this.positionen().find((x) => x.uid === uid);
    if (!p || p.source_quote_line_id) return;
    this.aendern(uid, { description: wert });
  }
  setMenge(uid: string, wert: string): void {
    this.aendern(uid, { menge: wert });
  }
  setEinheit(uid: string, wert: string): void {
    const p = this.positionen().find((x) => x.uid === uid);
    if (!p || p.source_quote_line_id) return;
    this.aendern(uid, { unit: wert });
  }
  /** Die Notiz ist das freie Feld — auch bei einer Position aus dem Angebot. */
  setNotiz(uid: string, wert: string): void {
    this.aendern(uid, { note: wert.trim() ? wert : null });
  }

  entfernen(uid: string): void {
    const weg = this.positionen().find((p) => p.uid === uid);
    this.positionen.update((ps) => ps.filter((p) => p.uid !== uid));
    this.geaendert.set(true);
    this.fehler.set(null);
    this.ansage.set(`Position „${weg?.description ?? ''}" entfernt.`);
  }

  freieTextzeile(): void {
    this.positionen.update((ps) => [
      ...ps,
      {
        uid: neueUid(),
        line_type: 'TEXT',
        description: '',
        menge: '',
        unit: '',
        planned_quantity: null,
        source_article_id: null,
        source_assembly_id: null,
        source_quote_line_id: null,
        note: null,
      },
    ]);
    this.geaendert.set(true);
    this.ansage.set('Freie Textzeile hinzugefügt.');
  }

  verwerfen(): void {
    this.neuLaden();
    this.ansage.set('Änderungen verworfen.');
  }

  /** Ob die Mengeneingabe dieser Zeile unbrauchbar ist (mehrdeutig oder keine Zahl). */
  mengeUngueltig(p: Pos): boolean {
    if (p.line_type === 'TEXT') return false;
    const api = deZuApiDezimal(p.menge);
    return api === DEZIMAL_UNGUELTIG || api === '';
  }

  // --- Speichern -----------------------------------------------------------
  speichern(): void {
    if (this.speichert() || !this.bearbeitbar()) return;
    const rows: SiteReportLineIn[] = [];
    for (const [i, p] of this.positionen().entries()) {
      const nr = i + 1;
      const text = p.description.trim();
      if (!text) {
        this.fehler.set(`Position ${nr}: Die Bezeichnung darf nicht leer sein.`);
        return;
      }
      if (p.line_type === 'TEXT') {
        rows.push({ line_type: 'TEXT', description: text, note: p.note ?? null });
        continue;
      }
      // Die Menge wird NIE geraten: „1.500" ist mehrdeutig und wird abgelehnt
      // (deZuApiDezimal → DEZIMAL_UNGUELTIG), statt still als 1,5 zu speichern.
      const menge = deZuApiDezimal(p.menge);
      if (menge === DEZIMAL_UNGUELTIG) {
        this.fehler.set(
          `Position ${nr}: Die Menge „${p.menge}" ist nicht eindeutig. ` +
            'Bitte ohne Tausenderpunkt eingeben (z. B. 1500 oder 1,5).',
        );
        return;
      }
      if (!menge) {
        this.fehler.set(`Position ${nr}: Die Menge ist erforderlich.`);
        return;
      }
      const einheit = p.unit.trim();
      if (!einheit) {
        this.fehler.set(`Position ${nr}: Die Einheit ist erforderlich.`);
        return;
      }
      rows.push({
        line_type: p.line_type,
        description: text,
        quantity: menge,
        unit: einheit,
        source_article_id: p.source_article_id,
        source_assembly_id: p.source_assembly_id,
        // KEIN planned_quantity: das Soll leitet der Server aus der Herkunft ab
        // (source_quote_line_id). Ein vom Client gesetztes Soll wäre eine frei
        // behauptete Zahl auf einem unterschriebenen Nachweis.
        source_quote_line_id: p.source_quote_line_id,
        note: p.note ?? null,
      });
    }

    this.speichert.set(true);
    this.fehler.set(null);
    this.svc.setLines(this.berichtId(), rows).subscribe({
      next: (res) => {
        this.speichert.set(false);
        this.positionen.set(res.items.map((l) => this.ausApi(l)));
        this.geaendert.set(false);
        this.ansage.set(`${res.total} Positionen gespeichert.`);
      },
      error: (err) => {
        this.speichert.set(false);
        this.fehler.set(fehlerDetail(err) ?? 'Die Positionen konnten nicht gespeichert werden.');
      },
    });
  }

  // --- Palette -------------------------------------------------------------
  setPaletteModus(m: PaletteModus): void {
    if (this.paletteModus() === m) return;
    this.paletteModus.set(m);
    this.paletteTreffer.set([]);
    this.paletteSuche$.next('');
    this.paletteFetch('');
  }

  onPaletteSuche(wert: string): void {
    this.paletteSuche$.next(wert.trim());
  }

  private paletteFetch(q: string): void {
    const rid = ++this.paletteReq;
    this.paletteLaedt.set(true);
    this.paletteFehler.set(false);
    const fertig = (t: Treffer[]) => {
      if (rid !== this.paletteReq) return;
      this.paletteLaedt.set(false);
      this.paletteTreffer.set(t);
    };
    const daneben = () => {
      if (rid !== this.paletteReq) return;
      this.paletteLaedt.set(false);
      this.paletteTreffer.set([]);
      this.paletteFehler.set(true);
    };
    if (this.paletteModus() === 'artikel') {
      this.artikelSvc.listArticles({ page: 1, page_size: 20, q }).subscribe({
        next: (p) =>
          fertig(
            p.items.map((a) => ({
              id: a.id,
              zeile1: a.description,
              zeile2: `${a.article_number} · ${a.unit}`,
              line_type: a.line_type as SiteReportLineType,
              unit: a.unit,
            })),
          ),
        error: daneben,
      });
    } else {
      this.artikelSvc.listAssemblies({ page: 1, page_size: 20, q }).subscribe({
        next: (p) =>
          fertig(
            p.items.map((a) => ({
              id: a.id,
              zeile1: a.name,
              zeile2: `${a.assembly_number} · ${a.unit}`,
              // Eine Leistung (Stückliste) ist im Beleg wie im Bericht eine
              // Pauschale — dieselbe Zuordnung wie im Angebots-Editor.
              line_type: 'PAUSCHALE' as SiteReportLineType,
              unit: a.unit,
            })),
          ),
        error: daneben,
      });
    }
  }

  /**
   * Treffer als neue Position übernehmen (Klick — kein Drag&Drop nötig).
   * Bezeichnung und Einheit werden aus dem Stamm **kopiert**, nicht verwiesen:
   * ein späterer Stammtext darf einen unterschriebenen Nachweis nicht rückwirkend
   * verändern. Menge startet bei 1 und wird vor Ort korrigiert.
   */
  uebernehmen(t: Treffer): void {
    const artikel = this.paletteModus() === 'artikel';
    this.positionen.update((ps) => [
      ...ps,
      {
        uid: neueUid(),
        line_type: t.line_type,
        description: t.zeile1,
        menge: '1',
        unit: t.unit,
        planned_quantity: null,
        source_article_id: artikel ? t.id : null,
        source_assembly_id: artikel ? null : t.id,
        source_quote_line_id: null,
        note: null,
      },
    ]);
    this.geaendert.set(true);
    this.fehler.set(null);
    this.ansage.set(`„${t.zeile1}" als Position übernommen. Menge 1 — bitte anpassen.`);
  }

  // --- Vorbelegen aus Angebot ---------------------------------------------
  vorbelegenOeffnen(): void {
    this.dialogFehler.set(null);
    this.angebotWahl.set('');
    this.vorbelegenOffen.set(true);
    this.angeboteLaden.set(true);
    this.svc.vorbelegbareAngebote(this.berichtId()).subscribe({
      next: (a) => {
        this.angeboteLaden.set(false);
        this.angebote.set(a);
        if (a.length === 1) this.angebotWahl.set(a[0].id);
      },
      error: (err) => {
        this.angeboteLaden.set(false);
        this.angebote.set([]);
        this.dialogFehler.set(
          fehlerDetail(err) ?? 'Die Angebote des Auftrags konnten nicht geladen werden.',
        );
      },
    });
  }

  vorbelegenSchliessen(): void {
    if (!this.dialogLaedt()) this.vorbelegenOffen.set(false);
  }

  onAngebotWahl(wert: string): void {
    this.angebotWahl.set(wert);
  }

  vorbelegenAbsenden(): void {
    const quote = this.angebotWahl();
    if (!quote || this.dialogLaedt()) {
      if (!quote) this.dialogFehler.set('Bitte wählen Sie ein Angebot.');
      return;
    }
    this.dialogLaedt.set(true);
    this.dialogFehler.set(null);
    this.svc.vorbelegen(this.berichtId(), quote).subscribe({
      next: (res) => {
        this.dialogLaedt.set(false);
        this.vorbelegenOffen.set(false);
        this.positionen.set(res.items.map((l) => this.ausApi(l)));
        this.geaendert.set(false);
        this.ansage.set(
          `${res.total} Positionen aus dem Angebot übernommen. Ist entspricht dem Soll — ` +
            'bitte die Abweichungen korrigieren.',
        );
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.dialogFehler.set(fehlerDetail(err) ?? 'Die Vorbelegung ist fehlgeschlagen.');
      },
    });
  }

  // --- Darstellung ---------------------------------------------------------
  artLabel(t: SiteReportLineType): string {
    return siteReportLineTypeLabel(t);
  }

  /** Reine Anzeige (mit Tausenderpunkt) — NIE in ein Eingabefeld. */
  anzeige(wert: string | null): string {
    return apiZuDeAnzeige(wert) || '—';
  }

  /**
   * Menge für die **Nur-Lese-Sicht** (unterzeichneter Bericht): mit
   * Tausenderpunkt. Der Editorwert (`p.menge`) bleibt davon unberührt — er ist
   * und bleibt ungruppiert, sonst läse ihn das Formular mehrdeutig zurück.
   */
  mengeAnzeige(p: Pos): string {
    const api = deZuApiDezimal(p.menge);
    if (!api || api === DEZIMAL_UNGUELTIG) return p.menge || '—';
    return apiZuDeAnzeige(api);
  }

  /**
   * Abweichung Ist gegen Soll. Eine **Menge** (numeric(15,3)) — kein Geldbetrag;
   * Beträge werden im Frontend grundsätzlich nicht gerechnet. Ohne Sollmenge gibt
   * es keine Abweichung (null), nicht etwa „0".
   *
   * Die Aussage steht immer als TEXT da („Mehrmenge"); Symbol und Farbe sind nur
   * zusätzliche Kanäle (WCAG 2.2 AA, 1.4.1).
   */
  abweichung(p: Pos): Abweichung | null {
    if (p.planned_quantity == null || p.line_type === 'TEXT') return null;
    const soll = Number(p.planned_quantity);
    const istApi = deZuApiDezimal(p.menge);
    if (!Number.isFinite(soll) || istApi === '' || istApi === DEZIMAL_UNGUELTIG) return null;
    const ist = Number(istApi);
    if (!Number.isFinite(ist)) return null;
    // Auf die DB-Skala (3 Nachkommastellen) runden, damit kein Float-Rauschen
    // wie „+2,9999999" erscheint.
    const diff = Math.round((ist - soll) * 1000) / 1000;
    const richtung = diff > 0 ? 'mehr' : diff < 0 ? 'weniger' : 'gleich';
    const vorzeichen = diff > 0 ? '+' : '';
    return {
      soll: apiZuDeAnzeige(p.planned_quantity),
      ist: apiZuDeAnzeige(istApi),
      differenz: diff === 0 ? '0' : `${vorzeichen}${apiZuDeAnzeige(String(diff))}`,
      richtung,
      label:
        richtung === 'mehr'
          ? 'Mehrmenge'
          : richtung === 'weniger'
            ? 'Mindermenge'
            : 'wie geplant',
      symbol: richtung === 'mehr' ? '▲' : richtung === 'weniger' ? '▼' : '＝',
      klasse:
        richtung === 'mehr'
          ? 'stamp--warn'
          : richtung === 'weniger'
            ? 'stamp--type'
            : 'stamp--positive',
    };
  }
}
