import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { map } from 'rxjs';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { ProjektService } from '../../core/projekt.service';
import { AuftragService } from '../../core/auftrag.service';
import { EinsatzService } from '../../core/einsatz.service';
import { BelegService } from '../../core/beleg.service';
import { PartyService } from '../../core/party.service';
import { AuthService } from '../../core/auth.service';
import { QuoteCreate } from '../../core/beleg.model';
import {
  CasePriority,
  ServiceCaseDetail,
  ServiceCaseStatus,
  ServiceCaseTransition,
} from '../../core/projekt.model';
import { OrderPriority, WorkOrderCreate } from '../../core/auftrag.model';
import { ServiceJobCreate } from '../../core/einsatz.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { Dateien } from '../../shared/dateien/dateien';
import { Belege, BelegKontext } from '../../shared/belege/belege';
import { ZielFilter } from '../../core/datei.model';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { ReferenzWahl, RefSuche } from '../../shared/formular/referenz-wahl';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: ServiceCaseDetail }
  | VerbotenState
  | { kind: 'error' };

type Meldung = { art: 'erfolg' | 'fehler'; text: string };

// Schwer umkehrbare Ziele: auch ohne Begründungspflicht hinter eine
// hervorgehobene Bestätigung (Amber) stellen.
const SCHWER_UMKEHRBAR: ReadonlySet<ServiceCaseStatus> = new Set<ServiceCaseStatus>([
  'ABGELEHNT',
  'ABGESCHLOSSEN',
]);

@Component({
  selector: 'app-vorgang-detail',
  imports: [
    Mappe,
    RouterLink,
    KeinZugriff,
    Dateien,
    Belege,
    Bestaetigung,
    ReactiveFormsModule,
    Dialog,
    Feld,
    ReferenzWahl,
  ],
  templateUrl: './vorgang-detail.html',
  styleUrl: './vorgang-detail.scss',
})
export class VorgangDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly svc = inject(ProjektService);
  private readonly auftragSvc = inject(AuftragService);
  private readonly einsatzSvc = inject(EinsatzService);
  private readonly belegSvc = inject(BelegService);
  private readonly partySvc = inject(PartyService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  protected readonly tab = signal('uebersicht');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  protected readonly tabs: MappeTab[] = [
    { id: 'uebersicht', label: 'Übersicht' },
    { id: 'verlauf', label: 'Verlauf' },
    { id: 'dokumente', label: 'Dokumente' },
    { id: 'dateien', label: 'Dateien' },
  ];

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  // ---- Statusaktionen -----------------------------------------------------
  protected readonly uebergaenge = signal<ServiceCaseTransition[]>([]);
  protected readonly meldung = signal<Meldung | null>(null);
  /** Der gewählte Zielübergang, für den der Bestätigungsdialog offen ist. */
  protected readonly gewaehlt = signal<ServiceCaseTransition | null>(null);
  protected readonly statusLaedt = signal(false);

  /**
   * Nur Übergänge, für die der Benutzer das nötige Recht hat — mit row_scope ALLE.
   *
   * `POST /api/workflow/service_cases/{id}/status` ist fail-closed
   * (`require(request, "workflow", action)`): ein Konto mit EIGENE bekommt 403,
   * auch für die Übergänge, deren Recht es nominell trägt. Der Statusautomat des
   * Vorgangs ist Disposition, nicht Monteursarbeit.
   */
  protected readonly erlaubteUebergaenge = computed(() =>
    this.uebergaenge().filter((t) => this.auth.darfAlle('workflow', t.recht)),
  );

  /** Ein gewählter Übergang ist folgenreich (Amber), wenn begründungspflichtig
   *  oder schwer umkehrbar (ABGELEHNT/ABGESCHLOSSEN). */
  protected readonly gewaehltGefahr = computed(() => {
    const t = this.gewaehlt();
    return !!t && (t.reason_required || SCHWER_UMKEHRBAR.has(t.to_status));
  });

  protected readonly dialogTitel = computed(() => {
    const t = this.gewaehlt();
    return t ? `Status auf „${t.label}“ ändern?` : '';
  });

  protected readonly dialogText = computed(() => {
    const t = this.gewaehlt();
    const d = this.daten();
    if (!t || !d) return '';
    return (
      `Der Vorgang wechselt von „${this.statusLabel(d.status)}“ auf „${t.label}“. ` +
      'Der Wechsel wird im Statusverlauf protokolliert.'
    );
  });

  /** Stabile Zielreferenz fuer den Dateien-Tab (nur bei Vorgangswechsel neu). */
  protected readonly dateienZiel = computed<ZielFilter>(() => ({
    service_case_id: this.daten()?.id ?? '',
  }));

  /** Stabiler Beleg-Kontext für den Dokumente-Tab (Belege dieses Vorgangs). */
  protected readonly belegKontext = computed<BelegKontext>(() => ({
    service_case_id: this.daten()?.id ?? '',
  }));

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.tab.set('uebersicht');
      this.meldung.set(null);
      this.gewaehlt.set(null);
      if (!id) {
        this.state.set({ kind: 'error' });
        return;
      }
      this.load(id);
    });
  }

  retry(): void {
    const id = this.route.snapshot.paramMap.get('id');
    if (id) this.load(id);
  }

  private load(id: string): void {
    const rid = ++this.reqId;
    this.state.set({ kind: 'loading' });
    this.uebergaenge.set([]);
    this.svc.getServiceCase(id).subscribe({
      next: (data) => {
        if (rid === this.reqId) {
          this.state.set({ kind: 'ready', data });
          this.ladeUebergaenge(data.id);
        }
      },
      error: (err) => {
        if (rid === this.reqId) this.state.set(fehlerState(err));
      },
    });
  }

  private ladeUebergaenge(id: string): void {
    const rid = this.reqId;
    this.svc.getServiceCaseTransitions(id).subscribe({
      next: (ts) => {
        if (rid === this.reqId) this.uebergaenge.set(ts);
      },
      // Fehlschlag der Übergangsliste ist nicht fatal: Detail bleibt sichtbar,
      // es werden nur keine Statusknöpfe angeboten.
      error: () => {
        if (rid === this.reqId) this.uebergaenge.set([]);
      },
    });
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  statusFragen(t: ServiceCaseTransition): void {
    this.meldung.set(null);
    this.gewaehlt.set(t);
  }

  statusAbbrechen(): void {
    if (!this.statusLaedt()) this.gewaehlt.set(null);
  }

  statusBestaetigen(grund: string | null): void {
    const ziel = this.gewaehlt();
    const d = this.daten();
    if (!ziel || !d) return;
    this.statusLaedt.set(true);
    this.svc
      .advanceServiceCaseStatus(d.id, { to_status: ziel.to_status, reason: grund })
      .subscribe({
        next: (res) => {
          this.statusLaedt.set(false);
          this.gewaehlt.set(null);
          // reqId erhöhen, damit ein noch laufender load() das nicht überschreibt.
          ++this.reqId;
          this.state.set({ kind: 'ready', data: res });
          this.ladeUebergaenge(res.id);
          this.meldung.set({
            art: 'erfolg',
            text: `Status geändert auf „${this.statusLabel(res.status)}".`,
          });
        },
        error: (err) => {
          this.statusLaedt.set(false);
          this.gewaehlt.set(null);
          this.meldung.set({
            art: 'fehler',
            text: fehlerDetail(err) ?? 'Der Statuswechsel ist fehlgeschlagen.',
          });
        },
      });
  }

  // ---- Zum Projekt hochstufen ---------------------------------------------
  /** Eingabepanel für den Projektnamen offen? */
  protected readonly hochstufenOffen = signal(false);
  protected readonly hochstufenLaedt = signal(false);
  /** Vorbelegt mit dem Vorgangsbetreff, vom Nutzer änderbar. */
  protected readonly projektName = signal('');

  /**
   * Sichtbar nur, wenn der Vorgang noch kein Projekt hat UND der Nutzer das
   * Recht workflow.ANLEGEN besitzt. Hängt er schon an einem Projekt, zeigt die
   * Mappe ohnehin den Projekt-Link. Die Durchsetzung liegt beim Server.
   */
  protected readonly kannHochstufen = computed(
    () =>
      !this.daten()?.project &&
      // `darfAlle`: `promote-to-project` ist fail-closed (`require`) — EIGENE → 403.
      this.auth.darfAlle('workflow', 'ANLEGEN') &&
      this.auth.darfAlle('workflow', 'AENDERN'),
  );

  /** Absenden erst mit nicht-leerem Namen und außerhalb eines laufenden Requests. */
  protected readonly hochstufenBereit = computed(
    () => !this.hochstufenLaedt() && this.projektName().trim().length > 0,
  );

  hochstufenFragen(): void {
    const d = this.daten();
    if (!d) return;
    this.meldung.set(null);
    this.projektName.set(d.subject);
    this.hochstufenOffen.set(true);
  }

  hochstufenAbbrechen(): void {
    if (!this.hochstufenLaedt()) this.hochstufenOffen.set(false);
  }

  hochstufenName(wert: string): void {
    this.projektName.set(wert);
  }

  hochstufenBestaetigen(): void {
    const d = this.daten();
    if (!d || !this.hochstufenBereit()) return;
    const name = this.projektName().trim();
    this.hochstufenLaedt.set(true);
    this.svc.promoteToProject(d.id, { name: name.length ? name : null }).subscribe({
      next: (res) => {
        this.hochstufenLaedt.set(false);
        this.hochstufenOffen.set(false);
        this.router.navigate(['/projekte', res.id]);
      },
      error: (err) => {
        this.hochstufenLaedt.set(false);
        // Panel bleibt offen, damit der Nutzer den Namen anpassen/erneut senden
        // kann. 404/422/403 werden vom Server als deutsche detail-Meldung geliefert.
        this.meldung.set({
          art: 'fehler',
          text: fehlerDetail(err) ?? 'Das Hochstufen zum Projekt ist fehlgeschlagen.',
        });
      },
    });
  }

  // ---- Drehscheibe: Auftrag / Termin direkt aus dem Vorgang ---------------
  // Der Vorgang ist der Dreh- und Angelpunkt: von hier entstehen Auftrag und
  // (freier) Termin OHNE Umweg über ein Projekt. Die DB verlangt kein Projekt
  // (work_order.service_case_id / .project_id sind NULL-fähig; ein freier Termin
  // hat work_order_id = NULL). Anlegen ist Disposition → ANLEGEN mit Scope ALLE.
  protected readonly darfAnlegen = computed(() => this.auth.darfAlle('workflow', 'ANLEGEN'));

  /**
   * „+ Angebot" direkt aus dem Vorgang. `POST /invoicing/quotes` (Anlegen) und der
   * Angebots-Editor (Ändern/Speichern) sind fail-closed (`require`) — EIGENE → 403.
   * Beide Tore mit Scope ALLE prüfen, damit der Knopf nur erscheint, wenn die ganze
   * Kette (anlegen → im Editor bearbeiten) durchläuft.
   */
  // Volle Kette abbilden: anlegen (POST) → im Editor laden (GET → invoicing/LESEN)
  // → speichern (AENDERN). Rechte sind nicht hierarchisch — ohne LESEN landete der
  // Nutzer nach der Anlage im Editor auf 403.
  protected readonly darfAngebot = computed(
    () =>
      this.auth.darfAlle('invoicing', 'LESEN') &&
      this.auth.darfAlle('invoicing', 'ANLEGEN') &&
      this.auth.darfAlle('invoicing', 'AENDERN'),
  );
  protected readonly angebotLaedt = signal(false);

  protected readonly dialogOffen = signal<'auftrag' | 'termin' | null>(null);
  protected readonly dialogLaedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);

  protected readonly auftragForm = this.fb.group({
    title: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(200)],
    }),
    priority: this.fb.control('NORMAL', { nonNullable: true, validators: [Validators.required] }),
    desired_date: this.fb.control('', { nonNullable: true }),
    customer_reference: this.fb.control('', { nonNullable: true }),
    description: this.fb.control('', { nonNullable: true }),
    is_emergency: this.fb.control(false, { nonNullable: true }),
  });
  // Freier Termin (Begehung/Besichtigung) — die Liegenschaft erbt er vom Vorgang,
  // der Titel ist Pflicht (work_order_id bleibt NULL).
  protected readonly terminForm = this.fb.group({
    title: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    scheduled_start: this.fb.control('', { nonNullable: true }),
    scheduled_end: this.fb.control('', { nonNullable: true }),
    on_site_contact_party_id: this.fb.control('', { nonNullable: true }),
    access_instructions: this.fb.control('', { nonNullable: true }),
  });

  protected readonly partySuche: RefSuche = (q) =>
    this.partySvc.list({ page: 1, page_size: 20, q }).pipe(
      map((p) => p.items.map((x) => ({ id: x.id, label: x.display_name }))),
    );

  protected readonly prioOptionen: FeldOption[] = [
    { wert: 'NORMAL', label: 'Normal' },
    { wert: 'DRINGEND', label: 'Dringend' },
    { wert: 'NOTFALL', label: 'Notfall' },
  ];

  dialogOeffnen(art: 'auftrag' | 'termin'): void {
    const d = this.daten();
    if (!d) return;
    this.meldung.set(null);
    this.formularMeldung.set(null);
    if (art === 'auftrag') {
      // Titel mit dem Vorgangsbetreff vorbelegen (änderbar) — spart Tippen und
      // hält Auftrag und Vorgang thematisch beieinander.
      this.auftragForm.reset({
        title: d.subject,
        priority: d.priority,
        desired_date: '',
        customer_reference: '',
        description: '',
        is_emergency: d.priority === 'NOTFALL',
      });
    } else {
      this.terminForm.reset({
        title: d.subject,
        scheduled_start: '',
        scheduled_end: '',
        on_site_contact_party_id: '',
        access_instructions: '',
      });
    }
    this.dialogOffen.set(art);
  }

  dialogSchliessen(): void {
    if (this.dialogLaedt()) return;
    this.dialogOffen.set(null);
  }

  private nichtBereit(form: Parameters<typeof serverFehlerZuruecksetzen>[0]): boolean {
    if (this.dialogLaedt()) return true;
    serverFehlerZuruecksetzen(form);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(form);
    return form.invalid;
  }

  auftragAbsenden(): void {
    const d = this.daten();
    if (!d || this.nichtBereit(this.auftragForm)) return;
    const v = this.auftragForm.getRawValue();
    const payload: WorkOrderCreate = {
      property_id: d.property.id,
      title: v.title.trim(),
      // Direkt am Vorgang verankern; hängt der Vorgang an einem Projekt, erbt der
      // Auftrag diese Klammer mit. Kein Projekt-Zwang.
      service_case_id: d.id,
      project_id: d.project?.id ?? null,
      priority: v.priority as OrderPriority,
      desired_date: v.desired_date || null,
      customer_reference: v.customer_reference.trim() || null,
      description: v.description.trim() || null,
      is_emergency: v.is_emergency,
    };
    this.dialogLaedt.set(true);
    this.auftragSvc.create(payload).subscribe({
      next: (res) => {
        this.dialogLaedt.set(false);
        this.dialogOffen.set(null);
        // Weiter zur frischen Auftragsmappe — dort geht es mit „+ Termin" nahtlos
        // weiter (die Drehscheibe führt den Nutzer zum nächsten Schritt).
        this.router.navigate(['/auftraege', res.id]);
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.auftragForm).formular);
      },
    });
  }

  terminAbsenden(): void {
    const d = this.daten();
    if (!d || this.nichtBereit(this.terminForm)) return;
    const v = this.terminForm.getRawValue();
    const payload: ServiceJobCreate = {
      work_order_id: null,
      title: v.title.trim(),
      property_id: d.property.id,
      scheduled_start: v.scheduled_start || null,
      scheduled_end: v.scheduled_end || null,
      on_site_contact_party_id: v.on_site_contact_party_id || null,
      access_instructions: v.access_instructions.trim() || null,
    };
    this.dialogLaedt.set(true);
    this.einsatzSvc.create(payload).subscribe({
      next: (res) => {
        this.dialogLaedt.set(false);
        this.dialogOffen.set(null);
        this.router.navigate(['/planung', res.id]);
      },
      error: (err) => {
        this.dialogLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.terminForm).formular);
      },
    });
  }

  // --- Angebot direkt aus dem Vorgang --------------------------------------
  // Kein Umweg über die kontextlose /dokumente-Liste: das Angebot erbt die
  // Liegenschaft des Vorgangs und — falls vorhanden — das Projekt. Kein
  // Auftragsbezug (der Vorgang ist noch keiner). Ohne Zwischendialog, weil der
  // einzige Pflichtwert (Titel) sinnvoll vom Vorgangsbetreff geerbt und im Editor
  // ohnehin änderbar ist. Die Positionen erfasst der Nutzer direkt im Editor.
  angebotAnlegen(): void {
    const d = this.daten();
    if (!d || this.angebotLaedt()) return;
    this.meldung.set(null);
    const payload: QuoteCreate = {
      property_id: d.property.id,
      title: d.subject,
      // Direkt am Vorgang verankern; hat der Vorgang ein Projekt, erbt das Angebot
      // es serverseitig automatisch. project_id trotzdem mitgeben, wo bekannt.
      service_case_id: d.id,
      project_id: d.project?.id ?? null,
      lines: [],
    };
    this.angebotLaedt.set(true);
    this.belegSvc.createQuote(payload).subscribe({
      next: (q) => {
        this.angebotLaedt.set(false);
        this.router.navigate(['/dokumente/angebot', q.id]);
      },
      error: (err) => {
        this.angebotLaedt.set(false);
        this.meldung.set({
          art: 'fehler',
          text: fehlerDetail(err) ?? 'Das Angebot konnte nicht angelegt werden.',
        });
      },
    });
  }

  // ---- Darstellungshelfer -------------------------------------------------
  statusLabel(s: ServiceCaseStatus): string {
    const map: Record<ServiceCaseStatus, string> = {
      NEU: 'Neu',
      IN_PRUEFUNG: 'In Prüfung',
      RUECKFRAGE: 'Rückfrage',
      FREIGABE_AUSSTEHEND: 'Freigabe ausstehend',
      BEAUFTRAGT: 'Beauftragt',
      ABGESCHLOSSEN: 'Abgeschlossen',
      ABGELEHNT: 'Abgelehnt',
    };
    return map[s] ?? s;
  }
  statusClass(s: ServiceCaseStatus): string {
    if (s === 'ABGESCHLOSSEN') return 'stamp--positive';
    if (s === 'ABGELEHNT') return 'stamp--warn';
    return '';
  }
  // Auch für Verlaufseinträge (String-Status).
  statusLabelStr(s: string | null): string {
    if (s === null) return 'Anlage';
    return this.statusLabel(s as ServiceCaseStatus);
  }

  priorityLabel(p: CasePriority): string {
    const map: Record<CasePriority, string> = {
      NORMAL: 'Normal',
      DRINGEND: 'Dringend',
      NOTFALL: 'Notfall',
    };
    return map[p] ?? p;
  }
  priorityClass(p: CasePriority): string {
    return p === 'NORMAL' ? '' : 'stamp--warn';
  }

  scopeLabel(s: string): string {
    const map: Record<string, string> = {
      UNKNOWN: 'Ungeklärt',
      COMMON_PROPERTY: 'Gemeinschaftseigentum',
      PRIVATE_UNIT: 'Sondereigentum',
      MIXED: 'Gemischt',
    };
    return map[s] ?? s;
  }
}
