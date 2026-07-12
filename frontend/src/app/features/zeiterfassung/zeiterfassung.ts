import { DestroyRef, Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { ZeiterfassungService } from '../../core/zeiterfassung.service';
import { AuthService } from '../../core/auth.service';
import {
  Arbeitstag,
  ArbeitstagDetail,
  Stundenkonto,
  TAG_STATUS_LABEL,
  TAG_STATUS_ZEICHEN,
  TagStatus,
  ZeitMitarbeiter,
  Zeiteintrag,
  Zeitkategorie,
  Zeitraum,
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
      next: (blob) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'stundenliste.csv';
        a.click();
        URL.revokeObjectURL(url);
        this.meldung.set('Stundenliste heruntergeladen.');
      },
      error: (err) => this.fehler.set(fehlerDetail(err) ?? 'Export fehlgeschlagen.'),
    });
  }
}
