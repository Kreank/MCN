import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Subject, debounceTime, distinctUntilChanged, map } from 'rxjs';
import { AuftragService } from '../../core/auftrag.service';
import { PropertyService } from '../../core/property.service';
import { AuthService } from '../../core/auth.service';
import {
  OrderPriority,
  WorkOrderCreate,
  WorkOrderPage,
  WorkOrderStatus,
  workOrderStatusClass,
  workOrderStatusLabel,
} from '../../core/auftrag.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { ReferenzWahl, RefSuche } from '../../shared/formular/referenz-wahl';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';
import { VerbotenState, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: WorkOrderPage }
  | VerbotenState
  | { kind: 'error' };

type Segment = { value: WorkOrderStatus | null; label: string };
type Meldung = { art: 'erfolg' | 'fehler'; text: string };

/**
 * Globale Auftragsliste (work_order) — der Auftrag als zentrales Arbeitsobjekt,
 * nicht mehr nur als Detail-Hub. Durchsuchbar (Nummer/Titel), nach Status
 * gefiltert, seitenweise über alle Liegenschaften/Projekte hinweg.
 *
 * Der „＋ Neuer Auftrag"-Dialog legt einen Auftrag OHNE Vorgang an: die
 * Liegenschaft wird hier frei gewählt (`property_id` Pflicht), `service_case_id`
 * bleibt weg. Nach der Anlage geht es direkt in die Auftragsmappe.
 *
 * Statusfilter: je EIN Status pro Segment (kein Bündel), weil der Endpunkt nach
 * einem einzelnen Status filtert und serverseitig paginiert — eine clientseitige
 * Bündelung würde Gesamtzahl und Seiten verfälschen.
 */
@Component({
  selector: 'app-auftrag-liste',
  imports: [RouterLink, ReactiveFormsModule, KeinZugriff, Dialog, Feld, ReferenzWahl],
  templateUrl: './auftrag-liste.html',
  styleUrl: './auftrag-liste.scss',
})
export class AuftragListe {
  private readonly svc = inject(AuftragService);
  private readonly propertySvc = inject(PropertyService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  protected readonly pageSize = 20;
  protected readonly segments: Segment[] = [
    { value: null, label: 'Alle' },
    { value: 'ENTWURF', label: 'Entwurf' },
    { value: 'FREIGABE_AUSSTEHEND', label: 'Freigabe' },
    { value: 'FREIGEGEBEN', label: 'Freigegeben' },
    { value: 'IN_PLANUNG', label: 'In Planung' },
    { value: 'IN_AUSFUEHRUNG', label: 'In Ausführung' },
    { value: 'TECHNISCH_ABGESCHLOSSEN', label: 'Techn. fertig' },
    { value: 'KAUFMAENNISCH_GEPRUEFT', label: 'Kfm. geprüft' },
    { value: 'ABGERECHNET', label: 'Abgerechnet' },
    { value: 'STORNIERT', label: 'Storniert' },
  ];

  protected readonly query = signal('');
  protected readonly status = signal<WorkOrderStatus | null>(null);
  protected readonly page = signal(1);
  protected readonly state = signal<ViewState>({ kind: 'loading' });

  protected readonly skeletons = Array.from({ length: 8 });

  /**
   * `darfAlle`, nicht `darf`: `POST /api/workflow/work_orders` ist fail-closed
   * (`permissions.require`). Ein Konto mit row_scope EIGENE bekommt dort 403 —
   * ein Knopf, der nur 403 kann, wird nicht angeboten.
   */
  protected readonly darfAnlegen = computed(() => this.auth.darfAlle('workflow', 'ANLEGEN'));

  // --- Anlage-Dialog -------------------------------------------------------
  protected readonly meldung = signal<Meldung | null>(null);
  protected readonly neuOffen = signal(false);
  protected readonly neuLaedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);
  protected readonly neuForm = this.fb.group({
    property_id: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
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

  protected readonly prioOptionen: FeldOption[] = [
    { wert: 'NORMAL', label: 'Normal' },
    { wert: 'DRINGEND', label: 'Dringend' },
    { wert: 'NOTFALL', label: 'Notfall' },
  ];

  /** Liegenschaftssuche für den Pflicht-Objektbezug (Serversuche, debounced). */
  protected readonly liegenschaftSuche: RefSuche = (q) =>
    this.propertySvc.list({ page: 1, page_size: 20, q }).pipe(
      map((p) =>
        p.items.map((o) => ({ id: o.id, label: o.name, sub: `${o.property_number} · ${o.city}` })),
      ),
    );

  private readonly searchInput$ = new Subject<string>();
  private reqId = 0;

  private readonly dateFmt = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  });

  protected readonly totalPages = computed(() => {
    const s = this.state();
    if (s.kind !== 'ready') return 1;
    return Math.max(1, Math.ceil(s.data.total / s.data.page_size));
  });

  protected readonly resultSummary = computed(() => {
    const s = this.state();
    if (s.kind === 'loading') return 'Aufträge werden geladen.';
    if (s.kind === 'forbidden') return 'Keine Berechtigung für die Aufträge.';
    if (s.kind === 'error') return 'Aufträge konnten nicht geladen werden.';
    const t = s.data.total;
    if (t === 0) return 'Keine Aufträge gefunden.';
    return `${t} ${t === 1 ? 'Auftrag' : 'Aufträge'} gefunden, Seite ${s.data.page} von ${this.totalPages()}.`;
  });

  constructor() {
    this.searchInput$
      .pipe(debounceTime(300), distinctUntilChanged(), takeUntilDestroyed())
      .subscribe((v) => {
        this.query.set(v);
        this.page.set(1);
        this.fetch();
      });
    this.fetch();
    // `?neu=1` öffnet den Anlage-Dialog beim Betreten. Der frühere Header-CTA ist
    // fort (der globale Knopf ist „Anruf annehmen"); die Query bleibt als
    // Sprungziel für Verweise aus anderen Ansichten. Als Stream, nicht Snapshot:
    // so greift sie auch, wenn man bereits auf /auftraege steht (der Router
    // verwendet dieselbe Instanz weiter, der Konstruktor läuft nicht erneut).
    // Nach dem Öffnen wird der Param entfernt — sonst öffnete Reload/Zurück den
    // Dialog immer wieder.
    this.route.queryParamMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      if (pm.get('neu') && this.darfAnlegen() && !this.neuOffen()) {
        this.neuOeffnen();
        this.router.navigate([], {
          queryParams: { neu: null },
          queryParamsHandling: 'merge',
          replaceUrl: true,
        });
      }
    });
  }

  onSearch(value: string): void {
    this.searchInput$.next(value);
  }

  selectSegment(value: WorkOrderStatus | null): void {
    if (this.status() === value) return;
    this.status.set(value);
    this.page.set(1);
    this.fetch();
  }

  prev(): void {
    if (this.page() <= 1) return;
    this.page.update((p) => p - 1);
    this.fetch();
  }

  next(): void {
    if (this.page() >= this.totalPages()) return;
    this.page.update((p) => p + 1);
    this.fetch();
  }

  retry(): void {
    this.fetch();
  }

  private fetch(): void {
    const id = ++this.reqId;
    this.state.set({ kind: 'loading' });
    this.svc
      .list({
        page: this.page(),
        page_size: this.pageSize,
        q: this.query(),
        status: this.status(),
      })
      .subscribe({
        next: (data) => {
          if (id === this.reqId) this.state.set({ kind: 'ready', data });
        },
        error: (err) => {
          if (id === this.reqId) this.state.set(fehlerState(err));
        },
      });
  }

  // ---- Anlegen ------------------------------------------------------------
  neuOeffnen(): void {
    this.neuForm.reset({
      property_id: '',
      title: '',
      priority: 'NORMAL',
      desired_date: '',
      customer_reference: '',
      description: '',
      is_emergency: false,
    });
    this.formularMeldung.set(null);
    this.neuOffen.set(true);
  }

  neuSchliessen(): void {
    if (this.neuLaedt()) return;
    this.neuOffen.set(false);
  }

  neuAbsenden(): void {
    if (this.neuLaedt()) return;
    serverFehlerZuruecksetzen(this.neuForm);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.neuForm);
    if (this.neuForm.invalid) return;

    const v = this.neuForm.getRawValue();
    // service_case_id wird BEWUSST nicht gesendet: ein Auftrag ohne Vorgang.
    const payload: WorkOrderCreate = {
      property_id: v.property_id,
      title: v.title.trim(),
      priority: v.priority as OrderPriority,
      desired_date: v.desired_date || null,
      customer_reference: v.customer_reference.trim() || null,
      description: v.description.trim() || null,
      is_emergency: v.is_emergency,
    };

    this.neuLaedt.set(true);
    this.svc.create(payload).subscribe({
      next: (res) => {
        this.neuLaedt.set(false);
        this.neuOffen.set(false);
        // Weiter zur frischen Auftragsmappe — dort geht es mit den nächsten
        // Schritten (Beteiligte, Termin, Freigabe) nahtlos weiter.
        this.router.navigate(['/auftraege', res.id]);
      },
      error: (err) => {
        this.neuLaedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.neuForm).formular);
      },
    });
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  // ---- Darstellungshelfer -------------------------------------------------
  termin(iso: string): string {
    return this.dateFmt.format(new Date(iso));
  }

  statusLabel(s: WorkOrderStatus): string {
    return workOrderStatusLabel(s);
  }

  statusClass(s: WorkOrderStatus): string {
    return workOrderStatusClass(s);
  }

  priorityLabel(p: OrderPriority): string {
    const map: Record<OrderPriority, string> = {
      NORMAL: 'Normal',
      DRINGEND: 'Dringend',
      NOTFALL: 'Notfall',
    };
    return map[p] ?? p;
  }

  priorityClass(p: OrderPriority): string {
    if (p === 'NOTFALL') return 'stamp--negativ';
    if (p === 'DRINGEND') return 'stamp--warn';
    return '';
  }
}
