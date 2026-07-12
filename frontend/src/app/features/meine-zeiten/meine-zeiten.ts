import { DestroyRef, Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { ZeiterfassungService } from '../../core/zeiterfassung.service';
import {
  Arbeitstag,
  ArbeitstagDetail,
  StempelZustand,
  TAG_STATUS_LABEL,
  TAG_STATUS_ZEICHEN,
  Zeiteintrag,
  Zeitkategorie,
  ZUSTAND_LABEL,
  dauerText,
  fromLocalInput,
  tagStatusClass,
  tagText,
  uhrzeit,
} from '../../core/zeiterfassung.model';
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
  imports: [ReactiveFormsModule, Dialog, Bestaetigung, Feld, KeinZugriff],
  templateUrl: './meine-zeiten.html',
  styleUrl: './meine-zeiten.scss',
})
export class MeineZeiten {
  private readonly svc = inject(ZeiterfassungService);
  private readonly fb = inject(FormBuilder);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly stempel = signal<StempelZustand | null>(null);
  protected readonly heute = signal<ArbeitstagDetail | null>(null);
  protected readonly tage = signal<Arbeitstag[]>([]);
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
    note: this.fb.control(''),
  });

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

  private tageLaden(): void {
    this.svc.meineTage().subscribe({
      next: (t) => this.tage.set(t),
      error: () => this.tage.set([]),
    });
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

  protected eintragDialogOeffnen(): void {
    const heute = new Date();
    const pad = (n: number) => String(n).padStart(2, '0');
    this.eintragForm.reset({
      category_id: this.startKategorie(),
      datum: `${heute.getFullYear()}-${pad(heute.getMonth() + 1)}-${pad(heute.getDate())}`,
      von: '08:00',
      bis: '16:00',
      note: '',
    });
    this.eintragDialogOffen.set(true);
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
