import {
  Component,
  DestroyRef,
  computed,
  effect,
  inject,
  input,
  signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { map } from 'rxjs';
import { AuthService } from '../../../core/auth.service';
import { EinsatzService } from '../../../core/einsatz.service';
import { SiteReportService } from '../../../core/site-report.service';
import {
  SiteReport,
  SiteReportCreate,
  SiteReportUpdate,
  siteReportStatusClass,
  siteReportStatusLabel,
} from '../../../core/site-report.model';
import { ZielFilter } from '../../../core/datei.model';
import { Dialog } from '../../../shared/dialog/dialog';
import { Feld } from '../../../shared/formular/feld';
import { ReferenzWahl, RefSuche } from '../../../shared/formular/referenz-wahl';
import { UnterschriftPad } from '../../../shared/unterschrift-pad/unterschrift-pad';
import { Dateien } from '../../../shared/dateien/dateien';
import { VerbotenState, fehlerDetail, fehlerState } from '../../../shared/http-fehler';
import { KeinZugriff } from '../../../shared/kein-zugriff/kein-zugriff';
import { apiFehlerZuweisen } from '../../../shared/formular/api-fehler';
import { deZuApiDezimal, dezimalValidator } from '../../../shared/formular/dezimal';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../../shared/formular/formular.util';

type Zustand =
  | { kind: 'loading' }
  | { kind: 'ready'; items: SiteReport[] }
  | VerbotenState
  | { kind: 'error' };

type Meldung = { art: 'erfolg' | 'fehler'; text: string };
type DialogArt = 'neu' | 'bearbeiten' | 'unterschrift';

/**
 * Baustellenberichte eines Auftrags: Liste, Anlegen/Ändern (nur im ENTWURF),
 * Fotos (über die Datei-Ablage mit `site_report_id`) und die Kundenunterschrift,
 * die den Bericht besiegelt (ENTWURF → UNTERZEICHNET). Danach ist er
 * unveränderlich — das setzt die Datenbank durch; das UI spiegelt es nur.
 *
 * Eigenständige Mappe im Auftrags-Detail, damit die Auftrags-Komponente schlank
 * bleibt. Rechte werden hier nur für die Sichtbarkeit geprüft (der Server setzt
 * sie ohnehin durch): ANLEGEN/AENDERN aus dem Modul `workflow`.
 */
@Component({
  selector: 'app-auftrag-berichte',
  imports: [
    ReactiveFormsModule,
    Dialog,
    Feld,
    ReferenzWahl,
    UnterschriftPad,
    Dateien,
    KeinZugriff,
  ],
  templateUrl: './auftrag-berichte.html',
  styleUrl: './auftrag-berichte.scss',
})
export class AuftragBerichte {
  private readonly svc = inject(SiteReportService);
  private readonly einsatzSvc = inject(EinsatzService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);
  private readonly destroyRef = inject(DestroyRef);

  /** Auftrag, dessen Berichte gezeigt werden (stabile ID). */
  readonly workOrderId = input.required<string>();

  private ladeReqId = 0;
  private geladenFuer: string | null = null;

  protected readonly zustand = signal<Zustand>({ kind: 'loading' });
  protected readonly meldung = signal<Meldung | null>(null);

  protected readonly darfAnlegen = computed(() => this.auth.darf('workflow', 'ANLEGEN'));
  protected readonly darfAendern = computed(() => this.auth.darf('workflow', 'AENDERN'));

  // Auswahl (Master-Detail innerhalb des Reiters).
  protected readonly ausgewaehltId = signal<string | null>(null);
  protected readonly berichte = computed(() => {
    const z = this.zustand();
    return z.kind === 'ready' ? z.items : [];
  });
  protected readonly ausgewaehlt = computed<SiteReport | null>(
    () => this.berichte().find((r) => r.id === this.ausgewaehltId()) ?? null,
  );

  /** Stabile Zielreferenz für den Fotos-Bereich des gewählten Berichts. */
  protected readonly fotoZiel = computed<ZielFilter>(() => ({
    site_report_id: this.ausgewaehltId() ?? '',
  }));

  // --- Dialoge -------------------------------------------------------------
  protected readonly dialogOffen = signal<DialogArt | null>(null);
  protected readonly dialogLaedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);

  private readonly pad = viewChild<UnterschriftPad>('pad');
  protected readonly unterschriftLeer = signal(true);

  protected readonly berichtForm = this.fb.group({
    report_date: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    service_job_id: this.fb.control('', { nonNullable: true }),
    activity_text: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    weather: this.fb.control('', { nonNullable: true }),
    hours_worked: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
    materials_note: this.fb.control('', { nonNullable: true }),
    remarks: this.fb.control('', { nonNullable: true }),
  });
  protected readonly unterschriftForm = this.fb.group({
    signed_by_name: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
  });

  /** Einsätze dieses Auftrags als optionale Zuordnung (Referenzsuche). */
  protected readonly einsatzSuche: RefSuche = (q) =>
    this.einsatzSvc
      .list({ page: 1, page_size: 20, q, work_order_id: this.workOrderId() })
      .pipe(map((p) => p.items.map((x) => ({ id: x.id, label: `${x.job_number}` }))));

  constructor() {
    // Lädt (neu), sobald sich der Auftrag ändert. `workOrderId` als stabile ID.
    effect(() => {
      const id = this.workOrderId();
      if (id && this.geladenFuer !== id) {
        this.geladenFuer = id;
        this.ausgewaehltId.set(null);
        this.laden(id);
      }
    });
  }

  private laden(workOrderId: string): void {
    const rid = ++this.ladeReqId;
    this.zustand.set({ kind: 'loading' });
    this.svc
      .list(workOrderId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (l) => {
          if (rid !== this.ladeReqId) return;
          this.zustand.set({ kind: 'ready', items: l.items });
          // Auswahl beibehalten, falls noch vorhanden; sonst ersten wählen.
          const cur = this.ausgewaehltId();
          if (!cur || !l.items.some((r) => r.id === cur)) {
            this.ausgewaehltId.set(l.items[0]?.id ?? null);
          }
        },
        error: (err) => {
          if (rid === this.ladeReqId) this.zustand.set(fehlerState(err));
        },
      });
  }

  neuLaden(): void {
    this.laden(this.workOrderId());
  }

  waehlen(id: string): void {
    this.ausgewaehltId.set(id);
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  /** Ersetzt/ergänzt einen Bericht in der Liste ohne kompletten Neuabruf. */
  private uebernehmen(report: SiteReport): void {
    this.zustand.update((z) => {
      if (z.kind !== 'ready') return z;
      const idx = z.items.findIndex((r) => r.id === report.id);
      const items =
        idx >= 0
          ? z.items.map((r) => (r.id === report.id ? report : r))
          : [report, ...z.items];
      return { kind: 'ready', items };
    });
    this.ausgewaehltId.set(report.id);
  }

  // --- Dialog öffnen/schließen ---------------------------------------------
  neuOeffnen(): void {
    this.formularMeldung.set(null);
    this.berichtForm.reset({
      report_date: this.heute(),
      service_job_id: '',
      activity_text: '',
      weather: '',
      hours_worked: '',
      materials_note: '',
      remarks: '',
    });
    this.dialogOffen.set('neu');
  }

  bearbeitenOeffnen(): void {
    const r = this.ausgewaehlt();
    if (!r || r.status !== 'ENTWURF') return;
    this.formularMeldung.set(null);
    this.berichtForm.reset({
      report_date: r.report_date,
      service_job_id: r.service_job_id ?? '',
      activity_text: r.activity_text,
      weather: r.weather ?? '',
      hours_worked: r.hours_worked ? r.hours_worked.replace('.', ',') : '',
      materials_note: r.materials_note ?? '',
      remarks: r.remarks ?? '',
    });
    this.dialogOffen.set('bearbeiten');
  }

  unterschriftOeffnen(): void {
    const r = this.ausgewaehlt();
    if (!r || r.status !== 'ENTWURF') return;
    this.formularMeldung.set(null);
    this.unterschriftForm.reset({ signed_by_name: '' });
    this.dialogOffen.set('unterschrift');
    // Das Pad-Element wird über Dialoge hinweg WIEDERVERWENDET (app-dialog
    // projiziert seinen Inhalt immer, Schließen ist nur display:none) — die
    // Canvas-Bitmap überlebt also und würde sonst die vorige Unterschrift
    // mitschleppen. Deshalb hier explizit leeren, statt auf den ResizeObserver
    // zu vertrauen. leeren() setzt zugleich unterschriftLeer über `veraendert`.
    this.pad()?.leeren();
    this.unterschriftLeer.set(true);
  }

  dialogSchliessen(): void {
    if (this.dialogLaedt()) return;
    this.dialogOffen.set(null);
  }

  onUnterschriftVeraendert(): void {
    this.unterschriftLeer.set(this.pad()?.leer() ?? true);
  }

  // --- Absenden ------------------------------------------------------------
  private nichtBereit(form: Parameters<typeof serverFehlerZuruecksetzen>[0]): boolean {
    if (this.dialogLaedt()) return true;
    serverFehlerZuruecksetzen(form);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(form);
    return form.invalid;
  }

  neuAbsenden(): void {
    if (this.nichtBereit(this.berichtForm)) return;
    const v = this.berichtForm.getRawValue();
    const payload: SiteReportCreate = {
      work_order_id: this.workOrderId(),
      report_date: v.report_date,
      activity_text: v.activity_text.trim(),
      service_job_id: v.service_job_id || null,
      weather: v.weather.trim() || null,
      hours_worked: deZuApiDezimal(v.hours_worked) || null,
      materials_note: v.materials_note.trim() || null,
      remarks: v.remarks.trim() || null,
    };
    this.dialogLaedt.set(true);
    this.svc.create(payload).subscribe({
      next: (r) => {
        this.dialogLaedt.set(false);
        this.dialogOffen.set(null);
        this.uebernehmen(r);
        this.meldung.set({ art: 'erfolg', text: 'Baustellenbericht angelegt.' });
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.berichtForm).formular);
      },
    });
  }

  bearbeitenAbsenden(): void {
    const r = this.ausgewaehlt();
    if (!r || this.nichtBereit(this.berichtForm)) return;
    const v = this.berichtForm.getRawValue();
    const payload: SiteReportUpdate = {
      report_date: v.report_date,
      service_job_id: v.service_job_id || null,
      activity_text: v.activity_text.trim(),
      weather: v.weather.trim() || null,
      hours_worked: deZuApiDezimal(v.hours_worked) || null,
      materials_note: v.materials_note.trim() || null,
      remarks: v.remarks.trim() || null,
    };
    this.dialogLaedt.set(true);
    this.svc.update(r.id, payload).subscribe({
      next: (res) => {
        this.dialogLaedt.set(false);
        this.dialogOffen.set(null);
        this.uebernehmen(res);
        this.meldung.set({ art: 'erfolg', text: 'Bericht geändert.' });
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.berichtForm).formular);
      },
    });
  }

  unterschriftAbsenden(): void {
    const r = this.ausgewaehlt();
    if (!r || this.nichtBereit(this.unterschriftForm)) return;
    const png = this.pad()?.alsBase64() ?? null;
    if (!png) {
      this.formularMeldung.set('Bitte lassen Sie den Kunden unterschreiben.');
      return;
    }
    const name = this.unterschriftForm.getRawValue().signed_by_name.trim();
    this.dialogLaedt.set(true);
    this.svc.sign(r.id, { signed_by_name: name, signature_png_base64: png }).subscribe({
      next: (res) => {
        this.dialogLaedt.set(false);
        this.dialogOffen.set(null);
        this.uebernehmen(res);
        this.meldung.set({ art: 'erfolg', text: 'Bericht unterzeichnet und besiegelt.' });
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.unterschriftForm).formular);
      },
    });
  }

  // --- Darstellungshelfer --------------------------------------------------
  private heute(): string {
    const d = new Date();
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${d.getFullYear()}-${mm}-${dd}`;
  }

  statusLabel(s: SiteReport['status']): string {
    return siteReportStatusLabel(s);
  }
  statusClass(s: SiteReport['status']): string {
    return siteReportStatusClass(s);
  }

  private readonly datumFmt = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit', month: '2-digit', year: 'numeric',
  });
  datum(iso: string): string {
    const d = new Date(iso + 'T00:00:00');
    return isNaN(d.getTime()) ? iso : this.datumFmt.format(d);
  }
  private readonly zeitFmt = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
  });
  zeitpunkt(iso: string | null): string {
    return iso ? this.zeitFmt.format(new Date(iso)) : '—';
  }
  stunden(wert: string | null): string {
    return wert ? wert.replace('.', ',') : '—';
  }

  downloadFehlerText(err: unknown): string {
    return fehlerDetail(err) ?? 'Unbekannter Fehler.';
  }
}
