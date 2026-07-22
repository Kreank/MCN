import { DestroyRef, Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { EinsatzService } from '../../core/einsatz.service';
import { ServiceJob } from '../../core/einsatz.model';
import { ZeiterfassungService } from '../../core/zeiterfassung.service';
import {
  ArbeitstagDetail,
  StempelZustand,
  TAG_STATUS_LABEL,
  TAG_STATUS_ZEICHEN,
  Stundenkonto,
  Zeiteintrag,
  Zeitkategorie,
  ZUSTAND_LABEL,
  dauerText,
  fromLocalInput,
  kontoZeitraum,
  saldoArt,
  saldoText,
  tagStatusClass,
  tagText,
  uhrzeit,
} from '../../core/zeiterfassung.model';
import { RouterLink } from '@angular/router';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld } from '../../shared/formular/feld';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';

type ViewState = { kind: 'loading' } | { kind: 'ready' } | VerbotenState | { kind: 'error' };

/**
 * „Meine Zeiten" — die **Stempeluhr**.
 *
 * Das Erfassungsparadigma ist der Knopf, nicht das Formular: Der Monteur steigt
 * ins Auto, drückt **Start**; bei der Pause **Pause**, danach **Weiter**, am
 * Ende **Stopp**. Alles andere (Arbeitstag, Kategorie, Summen) leitet der Server
 * daraus ab.
 *
 * Bedienbarkeit auf dem Handy ist die Vorgabe, nicht der Bonus: ein einziger,
 * großer primärer Knopf (min. 64 px hoch, weit über der WCAG-2.2-Zielgröße von
 * 24 px), wenig Text, klare Zustände.
 *
 * WCAG 2.2 AA:
 * - Der Zustand steht als **Text** da (nicht nur als Farbe) und trägt zusätzlich
 *   ein Symbol.
 * - Jeder Zustandswechsel wird über eine `aria-live`-Region angesagt.
 * - Volle Tastaturbedienung; die Knöpfe sind echte `<button>`.
 */
@Component({
  selector: 'app-meine-zeiten',
  imports: [ReactiveFormsModule, RouterLink, Dialog, Bestaetigung, Feld, KeinZugriff],
  templateUrl: './meine-zeiten.html',
  styleUrl: './meine-zeiten.scss',
})
export class MeineZeiten {
  private readonly svc = inject(ZeiterfassungService);
  private readonly einsatzSvc = inject(EinsatzService);
  private readonly fb = inject(FormBuilder);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly stempel = signal<StempelZustand | null>(null);
  protected readonly heute = signal<ArbeitstagDetail | null>(null);
  protected readonly kategorien = signal<Zeitkategorie[]>([]);
  protected readonly fehler = signal<string | null>(null);
  protected readonly laeuftAktion = signal(false);
  /** Screenreader-Ansage des Zustandswechsels. */
  protected readonly ansage = signal('');

  /** Tickt, damit die laufende Dauer mitläuft (ohne Animation, nur Zahl). */
  private readonly tick = signal(Date.now());

  protected readonly dauerText = dauerText;
  protected readonly uhrzeit = uhrzeit;
  protected readonly tagText = tagText;
  protected readonly tagStatusClass = tagStatusClass;
  protected readonly TAG_STATUS_LABEL = TAG_STATUS_LABEL;
  protected readonly TAG_STATUS_ZEICHEN = TAG_STATUS_ZEICHEN;
  protected readonly ZUSTAND_LABEL = ZUSTAND_LABEL;

  /**
   * Das eigene Stundenkonto — hier nur als **eine Zeile** (Saldo + Verweis).
   *
   * Sascha (Befund E4): „Zeitenübersicht gefällt mir nicht, zu unübersichtlich."
   * Was fehlte, war nicht Gestaltung, sondern die **Aussage**: Tagessummen
   * beantworten nicht die Frage, die man an eine Zeitübersicht hat — „liege ich
   * vor oder zurück?". Der Endpunkt dafür stand längst und war nirgends
   * angebunden.
   *
   * Der ausführliche Block samt Historie steht unter „Mein Verlauf" (Befund E2,
   * „Dichte"). Hier bleibt die eine Zahl, die man auch beim Stempeln wissen
   * will — Stempeluhr und Auswertung sind zwei Fragen zu zwei Zeitpunkten.
   */
  protected readonly konto = signal<Stundenkonto | null>(null);

  protected readonly startKategorie = signal<string>('');
  protected readonly einreichenOffen = signal(false);
  protected readonly eintragDialogOffen = signal(false);
  protected readonly begruendungOffen = signal(false);

  protected readonly begruendungForm = this.fb.group({
    grund: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
  });

  protected readonly eintragForm = this.fb.group({
    category_id: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    datum: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    von: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    bis: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    /** Optionale Zuordnung zum Einsatz — leer = allgemeine Zeit (Werkstatt, Büro). */
    service_job_id: this.fb.control('', { nonNullable: true }),
    note: this.fb.control(''),
  });

  /**
   * Die Einsätze des im Dialog gewählten Tages — zur Auswahl beim Nachtragen.
   *
   * Sascha (Befund E): Beim Stempeln über die Knöpfe steht die Zuordnung schon
   * (der Server hängt die laufende Buchung an den Einsatz), beim Nachtragen von
   * Hand fehlte sie. Ohne sie landet die Zeit im Tagesbrei und lässt sich später
   * keinem Auftrag zuordnen — die Nachkalkulation greift ins Leere.
   *
   * Der Server liefert dem Monteur (row_scope EIGENE) ohnehin nur die eigenen
   * Einsätze; die Liste ist deshalb keine Fremddatenpreisgabe.
   */
  protected readonly tagesEinsaetze = signal<ServiceJob[]>([]);

  protected readonly einsatzOptionen = computed(() => [
    { wert: '', label: 'Keinem Einsatz zuordnen' },
    ...this.tagesEinsaetze().map((j) => ({
      wert: j.id,
      label: `${j.job_number} · ${j.title}`,
    })),
  ]);

  /** Laufzeit der aktuellen Buchung in Sekunden (live). */
  protected readonly laufendeSekunden = computed(() => {
    const s = this.stempel();
    this.tick(); // Abhängigkeit: erzwingt Neuberechnung im Intervall
    if (!s?.eintrag || s.eintrag.ended_at) return 0;
    return Math.max(0, Math.floor((Date.now() - new Date(s.eintrag.started_at).getTime()) / 1000));
  });

  /** Tagessumme inklusive der noch laufenden Buchung (nur Anzeige). */
  protected readonly tagArbeitLive = computed(() => {
    const s = this.stempel();
    if (!s) return 0;
    const laufend = s.zustand === 'LAEUFT' ? this.laufendeSekunden() : 0;
    return s.tag_arbeit_sekunden + laufend;
  });

  protected readonly tagPauseLive = computed(() => {
    const s = this.stempel();
    if (!s) return 0;
    const laufend = s.zustand === 'PAUSE' ? this.laufendeSekunden() : 0;
    return s.tag_pause_sekunden + laufend;
  });

  /**
   * Bezugstag der Summen. Läuft eine Buchung seit gestern (vergessenes
   * Stoppen), sind es die Summen von GESTERN — dann darf nirgends „heute"
   * stehen. Das Label trägt in dem Fall das Datum.
   */
  protected readonly bezugstag = computed(() => this.stempel()?.tag ?? null);

  protected readonly bezugIstHeute = computed(() => {
    const tag = this.bezugstag();
    if (!tag) return true;
    const j = new Date();
    const pad = (n: number) => String(n).padStart(2, '0');
    return tag === `${j.getFullYear()}-${pad(j.getMonth() + 1)}-${pad(j.getDate())}`;
  });

  protected readonly summenLabel = computed(() =>
    this.bezugIstHeute() ? 'heute' : `am ${tagText(this.bezugstag()!)}`,
  );

  /**
   * Ein bestätigter Arbeitstag ist nicht das Ende der Uhr: der Monteur darf
   * weiter stempeln, wenn er es begründet — der Tag fällt dann auf „Entwurf"
   * zurück (der vorgesehene Weg, 0067). Statt ihn erst in ein 422 laufen zu
   * lassen, fragt das UI die Begründung vorher ab.
   */
  protected readonly brauchtBegruendung = computed(
    () => this.stempel()?.tagesstatus === 'BESTAETIGT',
  );

  protected readonly kannEinreichen = computed(() => {
    const t = this.heute();
    return (
      !!t &&
      t.eintraege.length > 0 &&
      !t.laeuft &&
      (t.status === 'ENTWURF' || t.status === 'ABGELEHNT')
    );
  });

  protected readonly kategorieOptionen = computed(() =>
    this.kategorien()
      .filter((k) => k.is_work_time)
      .map((k) => ({ wert: k.id, label: k.name })),
  );

  protected readonly alleKategorieOptionen = computed(() =>
    this.kategorien().map((k) => ({ wert: k.id, label: k.name })),
  );

  constructor() {
    const timer = setInterval(() => this.tick.set(Date.now()), 15_000);
    this.destroyRef.onDestroy(() => clearInterval(timer));
    // Datiert man im Dialog um, gehören dazu die Einsätze des NEUEN Tages.
    // Über `valueChanges` statt eines DOM-`change`-Listeners auf `app-feld`:
    // Die Komponente hat kein solches Output, ein Listener finge nur zufällig
    // das durchgereichte native Event des Hosts.
    this.eintragForm.controls.datum.valueChanges
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((tag) => this.einsaetzeLaden(tag));
    this.laden();
  }

  protected laden(): void {
    this.state.set({ kind: 'loading' });
    this.svc
      .kategorien()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (ks) => {
          this.kategorien.set(ks);
          const arbeit = ks.find((k) => k.code === 'ARBEITSZEIT') ?? ks[0];
          if (arbeit) this.startKategorie.set(arbeit.id);
          this.aktuellLaden();
        },
        error: (err) => this.state.set(fehlerState(err)),
      });
  }

  private aktuellLaden(): void {
    this.svc.aktuell().subscribe({
      next: (z) => {
        this.uebernehmen(z);
        this.state.set({ kind: 'ready' });
        this.tageLaden();
      },
      error: (err) => this.state.set(fehlerState(err)),
    });
  }

  /**
   * Lädt den Saldo für die kompakte Zeile.
   *
   * Die Arbeitstage der letzten 30 Tage werden hier NICHT mehr geholt: Diese
   * Liste steht unter „Mein Verlauf" und lud die Stempeluhr nur noch zu, ohne
   * dass sie jemand las.
   */
  private tageLaden(): void {
    // Der Zeitraum MUSS mitgegeben werden. Ohne ihn nimmt der Server den
    // laufenden Monat bis zum **Monatsletzten** — er summiert also das Soll
    // aller noch nicht gearbeiteten Zukunftstage gegen ein Ist, das nur bis
    // heute reichen kann. Ein Vollzeit-Monteur sähe am Monatsersten „−184 h
    // Minusstunden", obwohl er nichts falsch gemacht hat.
    const [von, bis] = kontoZeitraum();
    // Ein Fehler hier blendet die Karte aus, statt die Seite zu kippen — wer
    // keinen Personalsatz hat (404), stempelt trotzdem.
    this.svc.stundenkonto(undefined, von, bis).subscribe({
      next: (k) => this.konto.set(k),
      error: () => this.konto.set(null),
    });
  }

  // `kontoZeitraum`, `saldoText` und `saldoArt` stehen im Modell, nicht in der
  // Nachbarkomponente: Ein Import aus einer lazy geladenen Route zöge deren
  // ganzen Chunk mit — und zwei Kopien der Zeitraumlogik wären die Sorte
  // Doppelpflege, bei der eine Seite später still auf die alte Regel zurückfällt.
  protected readonly saldoText = saldoText;
  protected readonly saldoArt = saldoArt;

  /**
   * Balkenbreite einer Buchung in Prozent der längsten Buchung des Tages
   * (Befund E2: „Tagesliste ist reiner Text").
   *
   * Bezug ist bewusst die **längste Buchung**, nicht die Tagessumme oder ein
   * fester 24-Stunden-Maßstab: Der Blick soll die Buchungen untereinander
   * vergleichen („der Vormittag war doppelt so lang wie der Nachmittag"). Ein
   * Tagesmaßstab drückte jede einzelne Buchung auf ein paar Prozent zusammen
   * und zeigte gar nichts mehr.
   *
   * Ein Mindestwert verhindert, dass eine Fünf-Minuten-Pause zu einem
   * unsichtbaren Strich wird — sie soll klein aussehen, aber vorhanden sein.
   * Eine noch laufende Buchung hat keine Dauer und bekommt keinen Balken.
   */
  protected balkenBreite(e: Zeiteintrag): number {
    const dauer = e.dauer_sekunden;
    if (!dauer || dauer <= 0) return 0;
    const laengste = (this.heute()?.eintraege ?? []).reduce(
      (max, x) => Math.max(max, x.dauer_sekunden ?? 0),
      0,
    );
    if (laengste <= 0) return 0;
    return Math.max(4, Math.round((dauer / laengste) * 100));
  }

  private uebernehmen(z: StempelZustand): void {
    this.stempel.set(z);
    if (z.work_day_id) {
      this.svc.tag(z.work_day_id).subscribe({
        next: (d) => this.heute.set(d),
        error: () => this.heute.set(null),
      });
    } else {
      this.heute.set(null);
    }
  }

  // --- Die vier Knöpfe ----------------------------------------------------

  protected start(): void {
    // Bestätigter Tag → erst die Begründung, dann die Uhr (sonst 422).
    if (this.brauchtBegruendung()) {
      this.begruendungForm.reset({ grund: '' });
      this.begruendungOffen.set(true);
      return;
    }
    this.aktion(this.svc.start({ category_id: this.startKategorie() || null }), 'Zeit läuft.');
  }

  protected startMitBegruendung(): void {
    if (this.begruendungForm.invalid) {
      this.begruendungForm.markAllAsTouched();
      return;
    }
    const grund = this.begruendungForm.getRawValue().grund.trim();
    this.begruendungOffen.set(false);
    this.aktion(
      this.svc.start({
        category_id: this.startKategorie() || null,
        correction_reason: grund,
      }),
      'Zeit läuft. Der Arbeitstag steht wieder auf Entwurf.',
    );
  }

  protected pause(): void {
    this.aktion(this.svc.pause(), 'Pause läuft.');
  }

  protected weiter(): void {
    this.aktion(this.svc.weiter(), 'Zeit läuft wieder.');
  }

  protected stopp(): void {
    this.aktion(this.svc.stopp(), 'Zeit gestoppt.');
  }

  private aktion(obs: ReturnType<ZeiterfassungService['pause']>, ansage: string): void {
    this.fehler.set(null);
    this.laeuftAktion.set(true);
    obs.subscribe({
      next: (z) => {
        this.laeuftAktion.set(false);
        this.uebernehmen(z);
        this.ansage.set(ansage);
        this.tageLaden();
      },
      error: (err) => {
        this.laeuftAktion.set(false);
        const detail = fehlerDetail(err);
        this.fehler.set(detail ?? 'Die Aktion ist fehlgeschlagen.');
        this.ansage.set(detail ?? 'Die Aktion ist fehlgeschlagen.');
      },
    });
  }

  // --- Arbeitstag einreichen ---------------------------------------------

  protected einreichen(): void {
    const t = this.heute();
    if (!t) return;
    this.laeuftAktion.set(true);
    this.svc.einreichen(t.id).subscribe({
      next: (d) => {
        this.laeuftAktion.set(false);
        this.einreichenOffen.set(false);
        this.heute.set(d);
        this.ansage.set('Arbeitstag eingereicht.');
        this.tageLaden();
      },
      error: (err) => {
        this.laeuftAktion.set(false);
        this.einreichenOffen.set(false);
        this.fehler.set(fehlerDetail(err) ?? 'Einreichen fehlgeschlagen.');
      },
    });
  }

  // --- Zeit von Hand nachtragen ------------------------------------------

  /**
   * Öffnet den Nachtrag-Dialog und setzt sinnvolle Vorgaben.
   *
   * Sascha (Befund E): „Merkt sich keine bereits eingetragenen Zeiten." Der
   * Dialog stand fest auf 08:00–16:00 — auch dann, wenn für den Tag schon von
   * 08:00 bis 12:00 gebucht war. Wer nachmittags nachtrug, musste beide Felder
   * von Hand korrigieren und lief andernfalls in eine Überlappung.
   *
   * Jetzt setzt „Von" auf das **Ende der letzten Buchung des Tages**; „Bis"
   * folgt vier Stunden später als Vorschlag, den man nur noch anpasst. Gibt es
   * für den Tag noch nichts, bleibt es beim Arbeitsbeginn.
   */
  protected eintragDialogOeffnen(): void {
    const heute = new Date();
    const pad = (n: number) => String(n).padStart(2, '0');
    const datum = `${heute.getFullYear()}-${pad(heute.getMonth() + 1)}-${pad(heute.getDate())}`;
    const von = this.letztesEnde(datum) ?? '08:00';
    this.eintragForm.reset({
      category_id: this.startKategorie(),
      datum,
      von,
      bis: this.plusStunden(von, 4),
      service_job_id: '',
      note: '',
    });
    // Die Einsätze des Tages lädt der `valueChanges`-Abonnent, den `reset()`
    // gerade ausgelöst hat — kein zweiter Aufruf hier.
    this.eintragDialogOffen.set(true);
  }

  /**
   * Einsätze des gewählten Tages nachladen (auch beim Umdatieren im Dialog).
   *
   * `reqId` verwirft veraltete Antworten. Ein `input[type=date]` emittiert beim
   * Durchtippen mehrfach, und ohne diese Schranke könnte die langsamere ältere
   * Anfrage als letzte eintreffen: Der Monteur sähe die Einsätze eines anderen
   * Tages und hängte seine Zeit an den falschen Auftrag — ein Fehler, der still
   * bleibt und erst in der Nachkalkulation auffällt.
   */
  private einsatzReqId = 0;

  protected einsaetzeLaden(tag: string): void {
    const meine = ++this.einsatzReqId;
    if (!tag) {
      this.tagesEinsaetze.set([]);
      return;
    }
    this.einsatzSvc
      .list({
        page: 1,
        page_size: 50,
        scheduled_from: `${tag}T00:00:00`,
        scheduled_to: `${tag}T23:59:59`,
      })
      .subscribe({
        next: (seite) => {
          if (meine !== this.einsatzReqId) return;
          this.tagesEinsaetze.set(seite.items);
        },
        // Ein Fehler hier darf das Nachtragen nicht blockieren: Die Zuordnung
        // ist optional, die Zeiterfassung ist es nicht.
        error: () => {
          if (meine === this.einsatzReqId) this.tagesEinsaetze.set([]);
        },
      });
  }

  /**
   * Ende der spätesten abgeschlossenen Buchung des gegebenen Tages als „HH:MM"
   * — oder null, wenn für ihn noch nichts gebucht ist. Eine noch laufende
   * Buchung zählt nicht: Sie hat kein Ende, an das sich etwas anschließen ließe.
   *
   * Der Tagesvergleich ist nicht überflüssig: `heute()` ist der **Bezugstag**
   * der Stempeluhr, bei vergessenem Stoppen also der Vortag. Ohne den Abgleich
   * schlüge der Dialog für heute eine Zeit vor, die aus gestrigen Buchungen
   * stammt.
   */
  private letztesEnde(tag: string): string | null {
    if (this.bezugstag() !== tag) return null;
    const enden = (this.heute()?.eintraege ?? [])
      .map((e) => e.ended_at)
      .filter((e): e is string => !!e);
    if (enden.length === 0) return null;
    const spaetestes = enden.reduce((a, b) => (a > b ? a : b));
    return uhrzeit(spaetestes);
  }

  /**
   * „12:00" + 4 → „16:00". Deckelt am Tagesende, statt in den Folgetag zu
   * laufen — der Server nähme eine Buchung über Mitternacht nicht an.
   *
   * Gedeckelt wird nur beim **echten** Überlauf. „19:00" + 4 ergibt exakt
   * 23:00 und bleibt genau das; eine pauschale Klemmung auf 23:59 hätte den
   * Vorschlag hier stillschweigend um eine Stunde verlängert.
   */
  private plusStunden(zeit: string, stunden: number): string {
    const [h, m] = zeit.split(':').map(Number);
    if (!Number.isFinite(h) || !Number.isFinite(m)) return '16:00';
    const minuten = h * 60 + m + stunden * 60;
    const gedeckelt = Math.min(minuten, 23 * 60 + 59);
    const zh = Math.floor(gedeckelt / 60);
    const zm = gedeckelt % 60;
    return `${String(zh).padStart(2, '0')}:${String(zm).padStart(2, '0')}`;
  }

  protected eintragSpeichern(): void {
    if (this.eintragForm.invalid) {
      this.eintragForm.markAllAsTouched();
      return;
    }
    const v = this.eintragForm.getRawValue();
    this.laeuftAktion.set(true);
    this.svc
      .eintragAnlegen({
        category_id: v.category_id,
        started_at: fromLocalInput(`${v.datum}T${v.von}`),
        ended_at: fromLocalInput(`${v.datum}T${v.bis}`),
        service_job_id: v.service_job_id || null,
        note: v.note || null,
      })
      .subscribe({
        next: () => {
          this.laeuftAktion.set(false);
          this.eintragDialogOffen.set(false);
          this.ansage.set('Zeit erfasst.');
          this.aktuellLaden();
        },
        error: (err) => {
          this.laeuftAktion.set(false);
          apiFehlerZuweisen(err, this.eintragForm);
          this.fehler.set(fehlerDetail(err));
        },
      });
  }

  protected eintragLoeschen(e: Zeiteintrag): void {
    this.laeuftAktion.set(true);
    this.svc.eintragLoeschen(e.id).subscribe({
      next: () => {
        this.laeuftAktion.set(false);
        this.ansage.set('Zeit gelöscht.');
        this.aktuellLaden();
      },
      error: (err) => {
        this.laeuftAktion.set(false);
        this.fehler.set(fehlerDetail(err) ?? 'Löschen fehlgeschlagen.');
      },
    });
  }
}
