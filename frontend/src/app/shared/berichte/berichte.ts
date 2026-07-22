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
import { AuthService } from '../../core/auth.service';
import { EinsatzService } from '../../core/einsatz.service';
import { SiteReportService } from '../../core/site-report.service';
import {
  SiteReport,
  SiteReportCreate,
  SiteReportKopf,
  SiteReportUpdate,
  siteReportStatusClass,
  siteReportStatusLabel,
} from '../../core/site-report.model';
import { ZielFilter } from '../../core/datei.model';
import { Dialog } from '../dialog/dialog';
import { Feld } from '../formular/feld';
import { ReferenzWahl, RefSuche } from '../formular/referenz-wahl';
import { UnterschriftPad } from '../unterschrift-pad/unterschrift-pad';
import { Dateien } from '../dateien/dateien';
import { DokumentBlatt } from '../dokument-blatt/dokument-blatt';
import { Dokumentkopf } from '../../core/beleg.model';
import { BerichtPositionen } from '../bericht-positionen/bericht-positionen';
import { EinsatzZeiten } from '../einsatz-zeiten/einsatz-zeiten';
import { VerbotenState, fehlerDetail, fehlerState } from '../http-fehler';
import { KeinZugriff } from '../kein-zugriff/kein-zugriff';
import { apiFehlerZuweisen } from '../formular/api-fehler';
import { deZuApiDezimal, dezimalValidator } from '../formular/dezimal';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../formular/formular.util';

type Zustand =
  | { kind: 'loading' }
  | { kind: 'ready'; items: SiteReport[] }
  | VerbotenState
  | { kind: 'error' };

type Meldung = { art: 'erfolg' | 'fehler'; text: string };
type DialogArt = 'neu' | 'bearbeiten' | 'unterschrift';

/** Der Bezug, an dem die Berichte hängen — genau einer von beiden. */
type Anker = { art: 'auftrag' | 'einsatz'; id: string };

/**
 * Baustellenberichte an einem **Anker**: an einem Auftrag (Baustelle) oder an
 * einem Einsatz (Termin — auch am **freien Termin** ohne Auftrag, dem
 * Begehungsprotokoll). Liste, Anlegen/Ändern (nur im ENTWURF), Fotos (über die
 * Datei-Ablage mit `site_report_id`) und die Kundenunterschrift, die den Bericht
 * besiegelt (ENTWURF → UNTERZEICHNET). Danach ist er unveränderlich — das setzt
 * die Datenbank durch; das UI spiegelt es nur.
 *
 * **Ein** Baustein für beide Einstiege (Auftrags-Mappe und Einsatz-Mappe): der
 * Bericht ist derselbe, nur sein Bezug wechselt. Am Einsatz entfällt die Auswahl
 * „Einsatz" im Formular (er steht ja fest), und beim freien Termin gibt es keinen
 * Auftrag — also auch keinen toten Verweis darauf.
 *
 * Rechte werden hier nur für die Sichtbarkeit geprüft (der Server setzt sie
 * ohnehin durch): ANLEGEN/AENDERN aus dem Modul `workflow`.
 */
@Component({
  selector: 'app-berichte',
  imports: [
    ReactiveFormsModule,
    Dialog,
    Feld,
    ReferenzWahl,
    UnterschriftPad,
    Dateien,
    BerichtPositionen,
    DokumentBlatt,
    EinsatzZeiten,
    KeinZugriff,
  ],
  templateUrl: './berichte.html',
  styleUrl: './berichte.scss',
})
export class Berichte {
  /** Bericht-PDF (Markenlayout): ENTWURF mit Aufdruck, unterzeichnet mit
   *  Unterschriftsblock — on-the-fly vom Server, wird nie archiviert. */
  pdfUrl(id: string): string {
    return `/api/workflow/site_reports/${id}/pdf`;
  }

  private readonly svc = inject(SiteReportService);
  private readonly einsatzSvc = inject(EinsatzService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);
  private readonly destroyRef = inject(DestroyRef);

  /** Auftrag, dessen Berichte gezeigt werden (Auftrags-Mappe). */
  readonly workOrderId = input<string | null>(null);
  /** Einsatz, dessen Berichte gezeigt werden (Einsatz-Mappe). Hat Vorrang. */
  readonly serviceJobId = input<string | null>(null);

  /** Genau ein Bezug. Der Einsatz gewinnt: er ist die genauere Angabe. */
  protected readonly anker = computed<Anker | null>(() => {
    const job = this.serviceJobId();
    if (job) return { art: 'einsatz', id: job };
    const order = this.workOrderId();
    return order ? { art: 'auftrag', id: order } : null;
  });

  protected readonly amEinsatz = computed(() => this.anker()?.art === 'einsatz');

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
  /**
   * Der ausgewählte Bericht **mit Briefkopf**.
   *
   * Der Listen-Endpunkt liefert bewusst keinen Kopf (`mit_kopf=False`) — bei
   * dreißig Berichten wäre er ein N+1 für Angaben, die in einer Liste niemand
   * liest. Den Kopf trägt allein die Detailantwort, und die muss deshalb beim
   * Auswählen nachgeladen werden.
   *
   * Ohne dieses Nachladen war der gesamte Briefkopf im Frontend tot: `r.kopf`
   * blieb `null`, das Blatt zeigte weder Absender noch Anschriftfeld, und der
   * Informationsblock trug nur das Berichtsdatum. Die API war korrekt, die
   * Oberfläche rief sie nie.
   */
  private readonly detail = signal<SiteReport | null>(null);

  protected readonly ausgewaehlt = computed<SiteReport | null>(() => {
    const id = this.ausgewaehltId();
    if (!id) return null;
    // Das Detail gewinnt (es trägt den Kopf); bis es da ist, zeigt die
    // Listenzeile den Bericht bereits an — kein leerer Bereich beim Umschalten.
    const geladen = this.detail();
    if (geladen && geladen.id === id) return geladen;
    return this.berichte().find((r) => r.id === id) ?? null;
  });

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

  /** Einsätze dieses Auftrags als optionale Zuordnung (nur in der Auftragssicht). */
  protected readonly einsatzSuche: RefSuche = (q) =>
    this.einsatzSvc
      .list({ page: 1, page_size: 20, q, work_order_id: this.workOrderId() ?? undefined })
      .pipe(map((p) => p.items.map((x) => ({ id: x.id, label: `${x.job_number}` }))));

  constructor() {
    // Lädt (neu), sobald sich der Anker ändert.
    effect(() => {
      const a = this.anker();
      if (!a) return;
      const key = `${a.art}:${a.id}`;
      if (this.geladenFuer !== key) {
        this.geladenFuer = key;
        this.ausgewaehltId.set(null);
        this.detail.set(null);
        this.laden(a);
      }
    });
  }

  private laden(anker: Anker): void {
    const rid = ++this.ladeReqId;
    this.zustand.set({ kind: 'loading' });
    const quelle =
      anker.art === 'einsatz' ? this.svc.listAmEinsatz(anker.id) : this.svc.list(anker.id);
    quelle.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (l) => {
        if (rid !== this.ladeReqId) return;
        this.zustand.set({ kind: 'ready', items: l.items });
        // Auswahl beibehalten, falls noch vorhanden; sonst ersten wählen.
        const cur = this.ausgewaehltId();
        if (!cur || !l.items.some((r) => r.id === cur)) {
          const naechste = l.items[0]?.id ?? null;
          this.ausgewaehltId.set(naechste);
          this.detail.set(null);
          if (naechste) this.detailLaden(naechste);
        } else {
          // Auswahl blieb bestehen — der Kopf kann sich geändert haben.
          this.detailLaden(cur);
        }
      },
      error: (err) => {
        if (rid === this.ladeReqId) this.zustand.set(fehlerState(err));
      },
    });
  }

  neuLaden(): void {
    const a = this.anker();
    if (a) this.laden(a);
  }

  waehlen(id: string): void {
    this.ausgewaehltId.set(id);
    this.detailLaden(id);
  }

  /**
   * Holt den vollen Bericht (mit Briefkopf) nach.
   *
   * `reqId` verwirft veraltete Antworten: Wer schnell durch die Liste klickt,
   * bekäme sonst den Kopf eines anderen Berichts auf sein Blatt.
   *
   * Ein Fehler hier lässt die Listenfassung stehen, statt die Ansicht zu
   * leeren — der Bericht bleibt lesbar, nur ohne Kopf.
   */
  private detailReqId = 0;

  private detailLaden(id: string): void {
    const rid = ++this.detailReqId;
    this.svc
      .get(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (r) => {
          if (rid === this.detailReqId) this.detail.set(r);
        },
        error: () => {
          if (rid === this.detailReqId) this.detail.set(null);
        },
      });
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
    // Anlegen, Ändern und Unterschreiben antworten mit einem SiteReport OHNE
    // Kopf — den trägt nur die Detailantwort. Ohne dieses Nachziehen
    // verschwände der Briefkopf nach jedem Speichern bis zum Neuladen.
    this.detailLaden(report.id);
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
    const a = this.anker();
    if (!a || this.nichtBereit(this.berichtForm)) return;
    const v = this.berichtForm.getRawValue();
    const payload: SiteReportCreate = {
      report_date: v.report_date,
      activity_text: v.activity_text.trim(),
      weather: v.weather.trim() || null,
      hours_worked: deZuApiDezimal(v.hours_worked) || null,
      materials_note: v.materials_note.trim() || null,
      remarks: v.remarks.trim() || null,
    };
    if (a.art === 'einsatz') {
      // Der Auftrag (falls es einen gibt) wird vom Server aus dem Einsatz
      // abgeleitet — beim freien Termin gibt es keinen.
      payload.service_job_id = a.id;
    } else {
      payload.work_order_id = a.id;
      payload.service_job_id = v.service_job_id || null;
    }
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
      activity_text: v.activity_text.trim(),
      weather: v.weather.trim() || null,
      hours_worked: deZuApiDezimal(v.hours_worked) || null,
      materials_note: v.materials_note.trim() || null,
      remarks: v.remarks.trim() || null,
    };
    // Der Einsatzbezug ist in der Einsatzsicht nicht verhandelbar (und beim
    // freien Termin ohnehin unveränderlich): dort gar nicht erst mitschicken.
    if (!this.amEinsatz()) payload.service_job_id = v.service_job_id || null;
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
  /**
   * Lage der Wohnung als eine Zeile: „Vorderhaus · Einheit WE 12 · 3. OG".
   *
   * Genau die Angabe, nach der der Monteur sucht — sie war bis Befund I11f
   * überhaupt nicht setzbar (`auftrag.unit_id` lag ungenutzt in der DB).
   * Leere Teile fallen weg; ohne jeden Teil gibt es keine Zeile.
   */
  lage(kopf: SiteReportKopf): string {
    return [
      kopf.gebaeude,
      kopf.einheit ? `Einheit ${kopf.einheit}` : null,
      kopf.etage,
    ]
      .filter((t): t is string => !!t)
      .join(' · ');
  }

  // ---- Dokumentblatt (Befunde B1/B2) --------------------------------------
  //
  // Sascha über die alte Berichtsmaske: „Vollkatastrophe! Nicht zu gebrauchen!
  // Sollte genau wie Angebote und Rechnungen denselben Dokumentenkonfigurator
  // verwenden." Der Bericht bekommt deshalb dieselbe Hülle — dasselbe Blatt,
  // derselbe Kopfaufbau, dieselbe Anschrift aus denselben Funktionen.

  /** Absender-/Empfängerblock in der Form, die das Blatt erwartet. */
  blattKopf(kopf: SiteReportKopf | null): Dokumentkopf | null {
    if (!kopf) return null;
    return {
      aussteller: kopf.aussteller ?? [],
      empfaenger: kopf.empfaenger ?? [],
      // Der Bericht friert seinen Kopf ab der Unterschrift ein (Migration
      // 0132) — für die Anzeige ist das aber ohne Belang: Das Blatt zeigt
      // schlicht, was im Kopf steht.
      aus_snapshot: false,
    };
  }

  /**
   * Der Informationsblock rechts neben dem Anschriftfeld.
   *
   * Leere Angaben fallen weg statt „—" zu zeigen: Ein Bericht am freien Termin
   * hat legitim keine Auftragsnummer, und eine Spalte voller Gedankenstriche
   * ist kein Briefkopf.
   */
  blattMeta(r: SiteReport): { label: string; wert: string }[] {
    const k = r.kopf;
    const zeilen: { label: string; wert: string }[] = [
      { label: 'Berichtsdatum', wert: this.datum(r.report_date) },
    ];
    if (!k) return zeilen;
    if (k.order_number) zeilen.push({ label: 'Auftrags-Nr.', wert: k.order_number });
    const objekt = [k.objekt_name, k.objekt_adresse].filter(Boolean).join(' · ');
    if (objekt) zeilen.push({ label: 'Objekt', wert: objekt });
    const lage = this.lage(k);
    if (lage) zeilen.push({ label: 'Lage', wert: lage });
    if (k.mieter.length) {
      zeilen.push({
        label: 'Mieter',
        wert: k.mieter.join(', '),
      });
    }
    if (k.eigentuemer.length) {
      zeilen.push({ label: 'Eigentümer', wert: k.eigentuemer.join(', ') });
    }
    return zeilen;
  }

  /** „Baustellenbericht" bzw. „Begehungsprotokoll" am freien Termin. */
  blattBetreff(r: SiteReport): string {
    return r.work_order_id ? 'Baustellenbericht' : 'Begehungsprotokoll';
  }

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
