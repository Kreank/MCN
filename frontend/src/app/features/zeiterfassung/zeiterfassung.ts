import { DestroyRef, Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { ZeiterfassungService } from '../../core/zeiterfassung.service';
import { MitarbeiterService } from '../../core/mitarbeiter.service';
import { AuthService } from '../../core/auth.service';
import { CarryoverRow } from '../../core/mitarbeiter.model';
import {
  AUSGLEICHSARTEN,
  AUSGLEICHSART_LABEL,
  Arbeitstag,
  ArbeitstagDetail,
  Ausgleich,
  Ausgleichsart,
  Stundenkonto,
  TAG_STATUS_LABEL,
  TAG_STATUS_ZEICHEN,
  TagStatus,
  ZeitMitarbeiter,
  Zeiteintrag,
  Zeitkategorie,
  Zeitraum,
  ausgleichText,
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

/** Die drei Sichten der Verwaltung. */
type Reiter = 'tage' | 'ausgleich' | 'resturlaub';

/**
 * Zeiterfassung (Verwaltung) — die Arbeitgeber-Sicht.
 *
 * Sie erfüllt die Aufzeichnungspflicht: § 17 MiLoG verlangt für Baugewerbe und
 * Gebäudedienstleistung Beginn, Ende und Dauer der täglichen Arbeitszeit,
 * aufgezeichnet binnen sieben Kalendertagen, zwei Jahre aufbewahrt und dem Zoll
 * vorzulegen — dafür der CSV-Export.
 *
 * Rechte: `hr/LESEN` mit row_scope ALLE (der Server antwortet für EIGENE mit
 * 403 → „Kein Zugriff"). Bestätigen/Ablehnen verlangt `hr/FREIGEBEN`; den
 * eigenen Tag darf niemand bestätigen (Vier-Augen, DB-Trigger).
 */
@Component({
  selector: 'app-zeiterfassung',
  imports: [ReactiveFormsModule, Dialog, Bestaetigung, Feld, KeinZugriff],
  templateUrl: './zeiterfassung.html',
  styleUrl: './zeiterfassung.scss',
})
export class Zeiterfassung {
  private readonly svc = inject(ZeiterfassungService);
  private readonly hrSvc = inject(MitarbeiterService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly tage = signal<Arbeitstag[]>([]);
  protected readonly detail = signal<ArbeitstagDetail | null>(null);
  protected readonly konto = signal<Stundenkonto | null>(null);
  protected readonly kategorien = signal<Zeitkategorie[]>([]);
  protected readonly mitarbeiter = signal<ZeitMitarbeiter[]>([]);
  protected readonly fehler = signal<string | null>(null);
  protected readonly laeuftAktion = signal(false);
  protected readonly meldung = signal('');

  protected readonly zeitraum = signal<Zeitraum>('monat');
  protected readonly statusFilter = signal<TagStatus | ''>('');
  protected readonly userFilter = signal<string>('');

  protected readonly ablehnenOffen = signal(false);
  protected readonly bestaetigenOffen = signal(false);
  protected readonly eintragDialogOffen = signal(false);
  protected readonly loeschenOffen = signal<Zeiteintrag | null>(null);

  protected readonly dauerText = dauerText;
  protected readonly uhrzeit = uhrzeit;
  protected readonly tagText = tagText;
  protected readonly tagStatusClass = tagStatusClass;
  protected readonly TAG_STATUS_LABEL = TAG_STATUS_LABEL;
  protected readonly TAG_STATUS_ZEICHEN = TAG_STATUS_ZEICHEN;

  protected readonly darfFreigeben = computed(() => this.auth.darf('hr', 'FREIGEBEN'));
  protected readonly darfAendern = computed(() => this.auth.darf('hr', 'AENDERN'));
  protected readonly darfExportieren = computed(() => this.auth.darf('hr', 'EXPORTIEREN'));

  /** Der eigene app_user — der eigene Tag ist nicht selbst bestätigbar. */
  private readonly eigenerActor = computed(() => this.auth.user()?.app_user_id ?? null);

  protected readonly kannBestaetigen = computed(() => {
    const d = this.detail();
    return (
      !!d &&
      d.status === 'EINGEREICHT' &&
      this.darfFreigeben() &&
      d.user_id !== this.eigenerActor()
    );
  });

  /** Der eigene Tag ist eingereicht — erklären, warum nichts geht (statt nur auszublenden). */
  protected readonly eigenerTag = computed(() => {
    const d = this.detail();
    return !!d && d.user_id === this.eigenerActor();
  });

  protected readonly zeitraeume: { wert: Zeitraum; label: string }[] = [
    { wert: 'heute', label: 'Heute' },
    { wert: 'woche', label: 'Woche' },
    { wert: 'monat', label: 'Monat' },
    { wert: 'jahr', label: 'Jahr' },
  ];

  protected readonly statusOptionen: { wert: TagStatus | ''; label: string }[] = [
    { wert: '', label: 'Alle' },
    { wert: 'ENTWURF', label: 'Entwurf' },
    { wert: 'EINGEREICHT', label: 'Eingereicht' },
    { wert: 'BESTAETIGT', label: 'Bestätigt' },
    { wert: 'ABGELEHNT', label: 'Abgelehnt' },
  ];

  protected readonly kategorieOptionen = computed(() =>
    this.kategorien().map((k) => ({ wert: k.id, label: k.name })),
  );

  protected readonly eintragForm = this.fb.group({
    category_id: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    datum: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    von: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    bis: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    note: this.fb.control(''),
    correction_reason: this.fb.control(''),
  });

  // =========================================================================
  // Reiter
  // =========================================================================

  protected readonly reiter = signal<Reiter>('tage');
  protected readonly reiterListe: { wert: Reiter; label: string }[] = [
    { wert: 'tage', label: 'Arbeitstage' },
    { wert: 'ausgleich', label: 'Stundenausgleich' },
    { wert: 'resturlaub', label: 'Resturlaub' },
  ];

  protected reiterWechseln(r: Reiter): void {
    this.reiter.set(r);
    this.fehler.set(null);
    if (r === 'ausgleich') this.ausgleicheLaden();
    if (r === 'resturlaub' && this.uebertragZeilen() === null) this.vorschauLaden();
  }

  // =========================================================================
  // Stundenausgleich
  // =========================================================================
  // Der Saldo bleibt ABGELEITET: Ist − Soll + Σ Ausgleich. Eine Buchung hier
  // schreibt keinen Saldo fort — sie ist die dritte Größe der Formel.

  protected readonly ausgleiche = signal<Ausgleich[]>([]);
  protected readonly ausgleichDialogOffen = signal(false);
  protected readonly ausgleichStornoZiel = signal<Ausgleich | null>(null);
  protected readonly ausgleichText = ausgleichText;
  protected readonly AUSGLEICHSART_LABEL = AUSGLEICHSART_LABEL;
  protected readonly ausgleichsarten = AUSGLEICHSARTEN;

  protected readonly ausgleichArtOptionen = AUSGLEICHSARTEN.map((a) => ({
    wert: a.wert,
    label: a.label,
  }));

  protected readonly vorzeichenOptionen = [
    { wert: 'plus', label: '+ Gutschrift (Konto steigt)' },
    { wert: 'minus', label: '− Belastung (Konto sinkt)' },
  ];

  protected readonly mitarbeiterOptionen = computed(() =>
    this.mitarbeiter().map((m) => ({
      wert: m.employee_id,
      label: `${m.name} (${m.employee_number})`,
    })),
  );

  protected readonly ausgleichForm = this.fb.group({
    employee_id: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    adjustment_type: this.fb.control<Ausgleichsart>('EINBEHALT', { nonNullable: true }),
    effective_on: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    vorzeichen: this.fb.control('plus', { nonNullable: true }),
    // Strings: `app-feld` typ='zahl' ist ein Textfeld (deutsche Eingabe). Die
    // Minuten sind ganzzahlig — sie sind die Wahrheit, nicht eine Dezimalstunde.
    stunden: this.fb.control('0', { nonNullable: true }),
    minuten: this.fb.control('0', { nonNullable: true }),
    // Pflicht. Ohne Begründung ist eine Kontobewegung nicht nachvollziehbar —
    // der Server lehnt sie ohnehin ab (422).
    reason: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
  });

  /** Der Hinweistext zur gewählten Art (Vorzeichen-Erwartung).
   *
   * Über `toSignal`, nicht direkt aus dem Control: Ein `computed`, das nur
   * `control.value` liest, hat keinen Signal-Producer und friert auf dem Wert
   * seiner ersten Auswertung ein — der Hinweis bliebe für immer der der
   * Startauswahl und sagte damit für jede andere Art das Falsche. Bei einem
   * Text, der die Vorzeichen-Erwartung erklärt, ist das schlimmer als keiner. */
  private readonly ausgleichsart = toSignal(
    this.ausgleichForm.controls.adjustment_type.valueChanges,
    { initialValue: this.ausgleichForm.controls.adjustment_type.value },
  );
  protected readonly artHinweis = computed(
    () => AUSGLEICHSARTEN.find((a) => a.wert === this.ausgleichsart())?.hinweis ?? '',
  );

  private ausgleicheLaden(): void {
    this.svc.ausgleiche(this.ausgleichEmployeeFilter()).subscribe({
      next: (a) => this.ausgleiche.set(a),
      error: (err) => this.fehler.set(fehlerDetail(err) ?? 'Konnte nicht geladen werden.'),
    });
  }

  /** Der Mitarbeiterfilter der Kopfzeile — er filtert über die app_user-ID. */
  private ausgleichEmployeeFilter(): string | undefined {
    const uid = this.userFilter();
    if (!uid) return undefined;
    return this.mitarbeiter().find((m) => m.user_id === uid)?.employee_id;
  }

  protected ausgleichDialogOeffnen(): void {
    const heute = new Date();
    const iso = `${heute.getFullYear()}-${String(heute.getMonth() + 1).padStart(2, '0')}-${String(
      heute.getDate(),
    ).padStart(2, '0')}`;
    this.ausgleichForm.reset({
      employee_id: this.ausgleichEmployeeFilter() ?? '',
      adjustment_type: 'EINBEHALT',
      effective_on: iso,
      vorzeichen: 'plus',
      stunden: '0',
      minuten: '0',
      reason: '',
    });
    this.ausgleichDialogOffen.set(true);
  }

  /** „7" → 7, „" → 0, „x" → 0. Ganzzahlig; alles andere wäre erfunden. */
  private ganzzahl(wert: string): number {
    const n = Number.parseInt((wert ?? '').trim().replace(',', '.'), 10);
    return Number.isFinite(n) && n >= 0 ? n : 0;
  }

  protected ausgleichSpeichern(): void {
    const v = this.ausgleichForm.getRawValue();
    const betrag = this.ganzzahl(v.stunden) * 60 + this.ganzzahl(v.minuten);
    if (this.ausgleichForm.invalid || betrag <= 0) {
      this.ausgleichForm.markAllAsTouched();
      if (betrag <= 0) {
        this.fehler.set('Der Ausgleich muss größer als null sein (Stunden und/oder Minuten).');
      }
      return;
    }
    this.fehler.set(null);
    this.laeuftAktion.set(true);
    this.svc
      .ausgleichBuchen({
        employee_id: v.employee_id,
        adjustment_type: v.adjustment_type,
        effective_on: v.effective_on,
        minutes: v.vorzeichen === 'minus' ? -betrag : betrag,
        reason: v.reason,
      })
      .subscribe({
        next: () => {
          this.laeuftAktion.set(false);
          this.ausgleichDialogOffen.set(false);
          this.meldung.set('Ausgleich gebucht.');
          this.ausgleicheLaden();
          this.kontoLaden();
        },
        error: (err) => {
          this.laeuftAktion.set(false);
          apiFehlerZuweisen(err, this.ausgleichForm);
          this.fehler.set(fehlerDetail(err) ?? 'Die Buchung ist fehlgeschlagen.');
        },
      });
  }

  /** Storno statt Löschen (GoBD): beide Zeilen bleiben stehen. */
  protected ausgleichStornieren(grund: string | null): void {
    const a = this.ausgleichStornoZiel();
    if (!a || !grund) return;
    this.laeuftAktion.set(true);
    this.svc.ausgleichStornieren(a.id, grund).subscribe({
      next: () => {
        this.laeuftAktion.set(false);
        this.ausgleichStornoZiel.set(null);
        this.meldung.set('Ausgleichsbuchung storniert.');
        this.ausgleicheLaden();
        this.kontoLaden();
      },
      error: (err) => {
        this.laeuftAktion.set(false);
        this.ausgleichStornoZiel.set(null);
        this.fehler.set(fehlerDetail(err) ?? 'Das Storno ist fehlgeschlagen.');
      },
    });
  }

  // =========================================================================
  // Resturlaub — Übertrag ins Folgejahr
  // =========================================================================
  // Der Übertrag SETZT den Wert des Folgejahres (er addiert nicht) — genau das
  // macht ihn idempotent. Die Vorschau zeigt „alt → neu", bevor irgendetwas
  // geschrieben wird.

  protected readonly uebertragJahr = signal(new Date().getFullYear() - 1);
  protected readonly uebertragZeilen = signal<CarryoverRow[] | null>(null);
  protected readonly uebertragLaedt = signal(false);
  protected readonly uebertragOffen = signal(false);
  protected readonly uebertragFertig = signal(false);

  protected readonly jahre = computed(() => {
    const j = new Date().getFullYear();
    return [j - 2, j - 1, j, j + 1].map((y) => ({ wert: String(y), label: String(y) }));
  });

  protected readonly zuUebertragen = computed(
    () => this.uebertragZeilen()?.filter((z) => z.changes) ?? [],
  );

  protected vorschauLaden(): void {
    this.uebertragLaedt.set(true);
    this.uebertragFertig.set(false);
    this.fehler.set(null);
    this.hrSvc.carryoverVorschau(this.uebertragJahr()).subscribe({
      next: (z) => {
        this.uebertragZeilen.set(z);
        this.uebertragLaedt.set(false);
      },
      error: (err) => {
        this.uebertragLaedt.set(false);
        this.uebertragZeilen.set([]);
        this.fehler.set(fehlerDetail(err) ?? 'Die Vorschau konnte nicht geladen werden.');
      },
    });
  }

  protected jahrWechseln(jahr: string): void {
    this.uebertragJahr.set(Number(jahr));
    this.vorschauLaden();
  }

  protected uebertragen(): void {
    this.laeuftAktion.set(true);
    this.hrSvc.carryoverUebertragen(this.uebertragJahr()).subscribe({
      next: (z) => {
        this.laeuftAktion.set(false);
        this.uebertragOffen.set(false);
        this.uebertragZeilen.set(z);
        this.uebertragFertig.set(true);
        this.meldung.set('Resturlaub übertragen.');
      },
      error: (err) => {
        this.laeuftAktion.set(false);
        this.uebertragOffen.set(false);
        this.fehler.set(fehlerDetail(err) ?? 'Der Übertrag ist fehlgeschlagen.');
      },
    });
  }

  /** Deutsche Tageszahl aus dem Decimal-String („26.50" → „26,5"). */
  protected urlaubstage(wert: string): string {
    const n = Number(wert);
    return Number.isFinite(n)
      ? new Intl.NumberFormat('de-DE', { maximumFractionDigits: 2 }).format(n)
      : wert;
  }

  protected datum(iso: string): string {
    const [y, m, d] = iso.split('-').map(Number);
    return new Intl.DateTimeFormat('de-DE').format(new Date(y, m - 1, d));
  }

  constructor() {
    this.svc.kategorien().subscribe({ next: (k) => this.kategorien.set(k) });
    this.svc.mitarbeitende().subscribe({
      next: (m) => this.mitarbeiter.set(m),
      error: () => this.mitarbeiter.set([]),
    });
    this.laden();
  }

  protected laden(): void {
    this.state.set({ kind: 'loading' });
    this.svc
      .liste({
        zeitraum: this.zeitraum(),
        status: this.statusFilter() || undefined,
        user_id: this.userFilter() || undefined,
      })
      .subscribe({
        next: (t) => {
          this.tage.set(t);
          this.state.set({ kind: 'ready' });
          this.kontoLaden();
        },
        error: (err) => this.state.set(fehlerState(err)),
      });
  }

  /** Das Stundenkonto ist immer personenbezogen — ohne Mitarbeiterfilter kein Konto. */
  private kontoLaden(): void {
    const uid = this.userFilter();
    if (!uid) {
      this.konto.set(null);
      return;
    }
    this.svc.stundenkonto(uid).subscribe({
      next: (k) => this.konto.set(k),
      error: () => this.konto.set(null),
    });
  }

  protected filterSetzen(): void {
    this.detail.set(null);
    this.laden();
    if (this.reiter() === 'ausgleich') this.ausgleicheLaden();
  }

  protected tagOeffnen(t: Arbeitstag): void {
    this.fehler.set(null);
    this.svc.tag(t.id).subscribe({
      next: (d) => this.detail.set(d),
      error: (err) => this.fehler.set(fehlerDetail(err) ?? 'Der Tag konnte nicht geladen werden.'),
    });
  }

  // --- Freigabe -----------------------------------------------------------

  protected bestaetigen(): void {
    const d = this.detail();
    if (!d) return;
    this.laeuftAktion.set(true);
    this.svc.bestaetigen(d.id).subscribe({
      next: (neu) => this.nachAktion(neu, 'Arbeitstag bestätigt.'),
      error: (err) => this.aktionFehler(err),
    });
  }

  protected ablehnen(note: string | null): void {
    const d = this.detail();
    if (!d || !note) return;
    this.laeuftAktion.set(true);
    this.svc.ablehnen(d.id, note).subscribe({
      next: (neu) => this.nachAktion(neu, 'Arbeitstag abgelehnt.'),
      error: (err) => this.aktionFehler(err),
    });
  }

  protected pausenAnwenden(): void {
    const d = this.detail();
    if (!d) return;
    this.laeuftAktion.set(true);
    this.svc.pausenAnwenden(d.id).subscribe({
      next: (neu) => this.nachAktion(neu, 'Pflichtpausen eingerechnet.'),
      error: (err) => this.aktionFehler(err),
    });
  }

  private nachAktion(neu: ArbeitstagDetail, meldung: string): void {
    this.laeuftAktion.set(false);
    this.bestaetigenOffen.set(false);
    this.ablehnenOffen.set(false);
    this.loeschenOffen.set(null);
    this.detail.set(neu);
    this.meldung.set(meldung);
    this.laden();
  }

  private aktionFehler(err: unknown): void {
    this.laeuftAktion.set(false);
    this.bestaetigenOffen.set(false);
    this.ablehnenOffen.set(false);
    this.loeschenOffen.set(null);
    this.fehler.set(fehlerDetail(err) ?? 'Die Aktion ist fehlgeschlagen.');
  }

  // --- Korrektur ----------------------------------------------------------

  protected eintragDialogOeffnen(): void {
    const d = this.detail();
    if (!d) return;
    const arbeit = this.kategorien().find((k) => k.code === 'ARBEITSZEIT');
    this.eintragForm.reset({
      category_id: arbeit?.id ?? '',
      datum: d.day,
      von: '08:00',
      bis: '16:00',
      note: '',
      correction_reason: '',
    });
    this.eintragDialogOffen.set(true);
  }

  protected eintragSpeichern(): void {
    const d = this.detail();
    if (!d || this.eintragForm.invalid) {
      this.eintragForm.markAllAsTouched();
      return;
    }
    const v = this.eintragForm.getRawValue();
    this.laeuftAktion.set(true);
    this.svc
      .eintragAnlegen({
        category_id: v.category_id,
        user_id: d.user_id,
        started_at: fromLocalInput(`${v.datum}T${v.von}`),
        ended_at: fromLocalInput(`${v.datum}T${v.bis}`),
        note: v.note || null,
        correction_reason: v.correction_reason || null,
      })
      .subscribe({
        next: () => {
          this.laeuftAktion.set(false);
          this.eintragDialogOffen.set(false);
          this.meldung.set('Zeit erfasst.');
          this.svc.tag(d.id).subscribe({ next: (neu) => this.detail.set(neu) });
          this.laden();
        },
        error: (err) => {
          this.laeuftAktion.set(false);
          apiFehlerZuweisen(err, this.eintragForm);
          this.fehler.set(fehlerDetail(err));
        },
      });
  }

  protected eintragLoeschen(grund: string | null): void {
    const e = this.loeschenOffen();
    const d = this.detail();
    if (!e || !d) return;
    this.laeuftAktion.set(true);
    this.svc.eintragLoeschen(e.id, grund ?? undefined).subscribe({
      next: () => {
        this.laeuftAktion.set(false);
        this.loeschenOffen.set(null);
        this.meldung.set('Zeitbuchung gelöscht.');
        this.svc.tag(d.id).subscribe({ next: (neu) => this.detail.set(neu) });
        this.laden();
      },
      error: (err) => this.aktionFehler(err),
    });
  }

  // --- Export -------------------------------------------------------------

  /**
   * Download über Blob statt `window.open` — der Auth-Cookie und der
   * CSRF-Header müssen mit (Repo-Muster aus `core/datei.service.ts`).
   */
  protected exportieren(): void {
    this.svc.stundenlisteCsv(undefined, undefined, this.userFilter() || undefined).subscribe({
      next: (blob) => this.blobLaden(blob, 'stundenliste.csv', 'Stundenliste heruntergeladen.'),
      error: (err) => this.fehler.set(fehlerDetail(err) ?? 'Export fehlgeschlagen.'),
    });
  }

  /**
   * Abwesenheiten als CSV — **mit** Abwesenheitsart (Krankheit!). Das ist ein
   * Gesundheitsdatum (DSGVO Art. 9); der Server verlangt dafür `hr/EXPORTIEREN`.
   * Der Knopf erscheint nur, wenn das Recht da ist — durchgesetzt wird es aber
   * ohnehin serverseitig.
   */
  protected abwesenheitenExportieren(): void {
    const employeeId = this.mitarbeiter().find(
      (m) => m.user_id === this.userFilter(),
    )?.employee_id;
    this.hrSvc.abwesenheitenCsv(undefined, undefined, employeeId).subscribe({
      next: (blob) =>
        this.blobLaden(blob, 'abwesenheiten.csv', 'Abwesenheitsliste heruntergeladen.'),
      error: (err) => this.fehler.set(fehlerDetail(err) ?? 'Export fehlgeschlagen.'),
    });
  }

  private blobLaden(blob: Blob, name: string, meldung: string): void {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
    this.meldung.set(meldung);
  }
}
