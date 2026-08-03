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
  VorbelegbaresAngebot,
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
/**
 * `neu` und `bearbeiten` gibt es nicht mehr: Der Bericht entsteht mit einem Klick
 * und wird **im Blatt** bearbeitet, nicht in einem vorgeschalteten Formular
 * (Sascha, 2026-08-02 — siehe `neuAnlegen`). Geblieben sind die drei Dialoge,
 * die echte Weggabelungen sind: womit beginnen, unterschreiben, verwerfen.
 */
type DialogArt = 'startwahl' | 'unterschrift' | 'loeschen';

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

  // --- Blatt-Bearbeitung ---------------------------------------------------
  //
  // Das Formular gehört **einem** Bericht: `formularFuer` merkt sich, welchem.
  // Ohne diesen Merker setzte jedes Nachladen des Details (es passiert nach
  // jedem Speichern und bei jedem Auswahlwechsel) die Eingaben zurück — wer
  // gerade tippt, verlöre seinen Satz mitten im Wort.
  private formularFuer: string | null = null;
  protected readonly neuLaeuft = signal(false);
  protected readonly kopfGeaendert = signal(false);
  protected readonly kopfSpeichert = signal(false);

  /** Startwahl nach dem Anlegen: aus welchem Angebot — oder auf leerem Blatt? */
  protected readonly startAngebote = signal<VorbelegbaresAngebot[]>([]);
  protected readonly startAngebotWahl = signal('');
  protected readonly startLaeuft = signal(false);

  /** Nur der ENTWURF ist ein Blatt zum Schreiben; danach ist er Nachweis. */
  protected readonly blattBearbeitbar = computed(
    () => this.ausgewaehlt()?.status === 'ENTWURF' && this.darfAendern(),
  );

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
          if (rid !== this.detailReqId) return;
          this.detail.set(r);
          // Das Blatt ist bearbeitbar — es braucht die Werte im Formular.
          // `kopfUebernehmen` schützt dabei die laufende Eingabe: Gehört das
          // Formular diesem Bericht schon, rührt es nichts an.
          this.kopfUebernehmen(r);
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

  // --- Neues Protokoll -----------------------------------------------------
  /**
   * „Neues Protokoll" — ein Klick, und der Entwurf **steht da**.
   *
   * Sascha beim Testen (2026-08-02): *„Das Zusammenklicken geht mir tatsächlich
   * bisschen auf die Nerven. Als ich dann fertig war, hab ich den Entwurf
   * gesehen. Können wir das nicht so machen, dass wenn ich auf den Button
   * Protokoll klicke, genau dieses Entwurffenster auftaucht?"*
   *
   * Genau das passiert jetzt: Der Bericht wird **sofort angelegt**, ausgewählt
   * und als bearbeitbares Blatt gezeigt. Der vorgeschaltete Formular-Dialog ist
   * ersatzlos entfallen — er fragte Dinge ab, die entweder feststehen (Termin,
   * Datum) oder genauso gut im Entwurf selbst stehen können.
   *
   * **Warum das vorher nicht ging — und jetzt schon:** Bis Migration 0145 war
   * ein Bericht unlöschbar (Trigger `no_delete`). Ein Klick, der sofort anlegt,
   * hätte nach jedem Fehlgriff eine Karteileiche hinterlassen, die niemand mehr
   * wegbekommt. Seit 0145 ist der **Entwurf** löschbar (ab ABGESCHLOSSEN nicht
   * mehr — dann ist er Abrechnungsgrundlage), und damit ist der Fehlklick
   * folgenlos: „Entwurf löschen" steht direkt daneben.
   *
   * **Die ausgeführten Arbeiten werden vorbelegt** statt leer zu bleiben:
   * `activity_text` ist in der Datenbank Pflicht und darf nicht leer sein
   * (CHECK aus 0054) — ohne Vorbelegung ließe sich der Entwurf gar nicht
   * anlegen. Der Text ist bewusst nichtssagend („Protokoll vom …"), damit
   * niemand versehentlich einen Platzhalter stehen lässt, der wie eine Aussage
   * aussieht.
   */
  neuAnlegen(): void {
    const a = this.anker();
    if (!a || this.neuLaeuft()) return;
    this.meldung.set(null);
    this.neuLaeuft.set(true);
    const heute = this.heute();
    const payload: SiteReportCreate = {
      report_date: heute,
      activity_text: `Protokoll vom ${this.datum(heute)}`,
      weather: null,
      hours_worked: null,
      materials_note: null,
      remarks: null,
    };
    if (a.art === 'einsatz') payload.service_job_id = a.id;
    else payload.work_order_id = a.id;

    this.svc.create(payload).subscribe({
      next: (r) => {
        this.neuLaeuft.set(false);
        this.uebernehmen(r);
        // Das Formular gehört ab jetzt diesem Bericht — ohne das Umsetzen
        // stünden im Blatt die Werte des vorher gewählten.
        this.kopfUebernehmen(r);
        // Und direkt die Frage, mit der jedes Protokoll beginnt: aus dem
        // Angebot heraus oder auf leerem Blatt? Nur wenn es überhaupt etwas
        // zu übernehmen gibt (kein Auftrag ⇒ kein Angebot ⇒ keine Frage).
        this.startwahlPruefen(r);
      },
      error: (err) => {
        this.neuLaeuft.set(false);
        this.meldung.set({
          art: 'fehler',
          text:
            fehlerDetail(err) ??
            'Der Entwurf konnte nicht angelegt werden. Bitte erneut versuchen.',
        });
      },
    });
  }

  /** Abfrage vor dem Loeschen — ein Klick weniger waere einer zu wenig. */
  loeschenOeffnen(): void {
    const r = this.ausgewaehlt();
    if (!r || r.status !== 'ENTWURF') return;
    this.formularMeldung.set(null);
    this.dialogOffen.set('loeschen');
  }

  /**
   * Loescht den Entwurf endgueltig.
   *
   * Sascha, 2026-08-02: „Entwuerfe alle loeschbar … das muellt das System zu."
   * Ab ABGESCHLOSSEN weist der Server es ab (422) — der Knopf steht dann ohnehin
   * nicht mehr da, aber die Regel gehoert auf den Server, nicht in die Ansicht.
   */
  loeschenBestaetigen(): void {
    const r = this.ausgewaehlt();
    if (!r) return;
    this.dialogLaedt.set(true);
    this.svc.loeschen(r.id).subscribe({
      next: () => {
        this.dialogLaedt.set(false);
        this.dialogOffen.set(null);
        this.ausgewaehltId.set(null);
        this.meldung.set({ art: 'erfolg', text: 'Entwurf geloescht.' });
        this.neuLaden();
      },
      error: (e) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(e, this.berichtForm).formular);
      },
    });
  }

  // --- Das Blatt füllen und speichern --------------------------------------
  /**
   * Setzt das Formular auf **diesen** Bericht — aber nur, wenn es ihm noch nicht
   * gehört.
   *
   * Der Vorbehalt ist der eigentliche Inhalt der Methode: `detailLaden` läuft
   * nach jedem Speichern und bei jedem Auswahlwechsel. Ohne den Merker
   * `formularFuer` würfe jede dieser Antworten die laufende Eingabe weg — und
   * zwar genau dann, wenn jemand längere Zeit an den ausgeführten Arbeiten
   * schreibt und nebenher gespeichert wird.
   */
  private kopfUebernehmen(r: SiteReport, erzwingen = false): void {
    if (!erzwingen && this.formularFuer === r.id) return;
    this.formularFuer = r.id;
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
    this.kopfGeaendert.set(false);
  }

  /** Jede Eingabe im Blatt macht den Speichern-Knopf scharf. */
  onKopfEingabe(): void {
    if (!this.kopfGeaendert()) this.kopfGeaendert.set(true);
  }

  /** Zurück auf den zuletzt gespeicherten Stand — ohne Server-Runde. */
  kopfVerwerfen(): void {
    const r = this.ausgewaehlt();
    if (r) this.kopfUebernehmen(r, true);
  }

  /**
   * Speichert den Berichtskopf (der Rest des Blattes hat eigene Knöpfe).
   *
   * **Zwei Speichern-Knöpfe auf einem Blatt sind Absicht, kein Versehen:** Kopf
   * und Positionen sind zwei Endpunkte mit zwei Regelwerken — die Positionen
   * ersetzt `PUT …/positionen` immer vollständig. Ein gemeinsamer Knopf müsste
   * beide Aufrufe verketten und bei einem halben Fehlschlag raten, was nun gilt.
   * Deshalb tragen die Knöpfe ausgeschriebene Beschriftungen („Bericht
   * speichern" / „Positionen speichern") statt eines nackten „Speichern".
   */
  kopfSpeichern(): void {
    const r = this.ausgewaehlt();
    if (!r || this.kopfSpeichert() || this.nichtBereit(this.berichtForm)) return;
    const v = this.berichtForm.getRawValue();
    const payload: SiteReportUpdate = {
      report_date: v.report_date,
      activity_text: v.activity_text.trim(),
      weather: v.weather.trim() || null,
      hours_worked: deZuApiDezimal(v.hours_worked) || null,
      // Die Materialnotiz steht nicht mehr in der Maske (Material gehört in die
      // Positionen), aber sie wird mitgeschickt: `update_report` schreibt jedes
      // übergebene Feld. Ließe man sie weg und der Endpunkt setzte fehlende
      // Felder auf null, verschwände eine Altnotiz beim ersten Speichern.
      materials_note: v.materials_note.trim() || null,
      remarks: v.remarks.trim() || null,
    };
    // Der Einsatzbezug ist in der Einsatzsicht nicht verhandelbar (und beim
    // freien Termin ohnehin unveränderlich): dort gar nicht erst mitschicken.
    if (!this.amEinsatz()) payload.service_job_id = v.service_job_id || null;
    this.kopfSpeichert.set(true);
    this.svc.update(r.id, payload).subscribe({
      next: (res) => {
        this.kopfSpeichert.set(false);
        this.kopfGeaendert.set(false);
        this.uebernehmen(res);
        this.meldung.set({ art: 'erfolg', text: 'Bericht gespeichert.' });
      },
      error: (err) => {
        this.kopfSpeichert.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.berichtForm).formular);
      },
    });
  }

  // --- Startwahl: aus dem Angebot heraus oder auf leerem Blatt? -------------
  /**
   * Fragt direkt nach dem Anlegen, womit begonnen wird.
   *
   * Die Frage stellt sich **nur**, wenn es etwas zu übernehmen gibt: Am freien
   * Termin (kein Auftrag) und bei einem Auftrag ohne hinausgegangenes Angebot
   * hat sie keine Antwortmöglichkeit — dann bleibt das Blatt einfach leer, statt
   * einen Dialog zu zeigen, dessen einzige Option „Weiter" heißt.
   *
   * Schlägt der Abruf fehl, wird ebenfalls nicht gefragt: Der Entwurf steht
   * bereits, und die Vorbelegung lässt sich jederzeit über die Positionen
   * nachholen. Ein Fehlerbalken für eine *optionale* Bequemlichkeit wäre Lärm.
   */
  private startwahlPruefen(r: SiteReport): void {
    if (!r.work_order_id) return;
    this.svc
      .vorbelegbareAngebote(r.id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (angebote) => {
          if (!angebote.length) return;
          this.startAngebote.set(angebote);
          this.startAngebotWahl.set(angebote.length === 1 ? angebote[0].id : '');
          this.formularMeldung.set(null);
          this.dialogOffen.set('startwahl');
        },
        error: () => {
          /* Optionale Bequemlichkeit — der Entwurf steht ja bereits. */
        },
      });
  }

  onStartAngebotWahl(wert: string): void {
    this.startAngebotWahl.set(wert);
  }

  /** „Leer beginnen" — der Dialog schließt, der Entwurf bleibt, wie er ist. */
  startLeer(): void {
    if (this.startLaeuft()) return;
    this.dialogOffen.set(null);
  }

  startUebernehmen(): void {
    const r = this.ausgewaehlt();
    const quote = this.startAngebotWahl();
    if (!r || this.startLaeuft()) return;
    if (!quote) {
      this.formularMeldung.set('Bitte wählen Sie ein Angebot — oder beginnen Sie leer.');
      return;
    }
    this.startLaeuft.set(true);
    this.formularMeldung.set(null);
    this.svc.vorbelegen(r.id, quote).subscribe({
      next: (res) => {
        this.startLaeuft.set(false);
        this.dialogOffen.set(null);
        // Die Positionsliste lebt in einer eigenen Komponente und hat gerade
        // geladen, als es noch nichts gab — sie muss ihren Stand neu holen.
        this.positionenNeuLaden();
        this.meldung.set({
          art: 'erfolg',
          text:
            `${res.total} Positionen aus dem Angebot übernommen. Ist entspricht dem ` +
            'Soll — bitte die Abweichungen korrigieren.',
        });
      },
      error: (err) => {
        this.startLaeuft.set(false);
        this.formularMeldung.set(
          fehlerDetail(err) ?? 'Die Vorbelegung ist fehlgeschlagen.',
        );
      },
    });
  }

  /**
   * Die Positionsliste holt ihren Stand neu.
   *
   * Sie lädt beim Aufbau anhand der `berichtId` — zu diesem Zeitpunkt war der
   * Bericht noch leer. Wird danach von hier aus vorbelegt, merkt sie davon
   * nichts und zeigte weiter „noch keine Positionen", obwohl der Server sie
   * längst hat. Deshalb der direkte Anstoß über die Kindkomponente.
   */
  private readonly positionen = viewChild<BerichtPositionen>('positionen');

  private positionenNeuLaden(): void {
    this.positionen()?.neuLaden();
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
      // Kein Objektbezug: Der Bericht dokumentiert einen Einsatz, er stellt
      // nichts in Rechnung. Wohneinheit/Eigentümer/Mieter gehören auf Angebot
      // und Rechnung — hier wären sie Beiwerk.
      bezug: [],
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
