import {
  Component,
  DestroyRef,
  ElementRef,
  computed,
  effect,
  inject,
  input,
  model,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Subscription, catchError, of, switchMap, timer } from 'rxjs';
import { AnbindungService } from '../../../core/anbindung.service';
import {
  PunchoutForm,
  ResolvedPosition,
  SupplierConnection,
} from '../../../core/anbindung.model';
import { Dialog } from '../../../shared/dialog/dialog';
import { fehlerDetail } from '../../../shared/http-fehler';

type Phase = 'auswahl' | 'wartet' | 'fertig' | 'fehler';

/**
 * IDS-Connect „Bei Händler bestellen": öffnet aus einem Angebot heraus den
 * Webshop eines Großhändlers (Punchout), lässt den Handwerker dort einen
 * Warenkorb zusammenstellen und holt ihn — token-gesichert — zurück. Die
 * aufgelösten Positionen werden per `(uebernehmen)` an den Editor gereicht, der
 * daraus Belegpositionen macht.
 *
 * Ablauf: Anbindung wählen → Shop öffnet in neuem Tab (auto-submittendes
 * POST-Formular) → dieser Dialog pollt die Session, bis der Shop den Warenkorb
 * an die hookurl zurückgemeldet hat → Positionen anzeigen und übernehmen.
 *
 * Sicherheit: Das Punchout-Formular trägt das Klartext-Passwort (dem IDS-Verfahren
 * inhärent) — es wird nur zum Submit an den Shop aufgebaut und sofort wieder aus
 * dem DOM entfernt, nie angezeigt oder geloggt.
 */
@Component({
  selector: 'app-ids-bestellung',
  imports: [Dialog],
  templateUrl: './ids-bestellung.html',
  styleUrl: './ids-bestellung.scss',
})
export class IdsBestellung {
  private readonly svc = inject(AnbindungService);
  private readonly destroyRef = inject(DestroyRef);

  readonly quoteId = input<string | null>(null);
  readonly offen = model(false);
  /** Aufgelöste Warenkorb-Positionen zum Einfügen in den Beleg. */
  readonly uebernehmen = output<ResolvedPosition[]>();

  protected readonly phase = signal<Phase>('auswahl');
  protected readonly fehler = signal<string | null>(null);
  protected readonly busy = signal(false);

  protected readonly connections = signal<SupplierConnection[]>([]);
  protected readonly gewaehlt = signal<string | null>(null);
  protected readonly positionen = signal<ResolvedPosition[]>([]);

  private sessionId: string | null = null;
  private poll?: Subscription;
  private readonly uebernehmenBtn =
    viewChild<ElementRef<HTMLButtonElement>>('uebernehmenBtn');

  protected readonly matchedCount = computed(
    () => this.positionen().filter((p) => p.matched).length,
  );

  constructor() {
    // Beim Öffnen: aktive IDS-Anbindungen laden und in die Auswahl gehen.
    effect(() => {
      if (this.offen()) {
        this.zuruecksetzen();
        this.ladeAnbindungen();
      } else {
        this.pollStoppen();
      }
    });
    this.destroyRef.onDestroy(() => this.pollStoppen());
  }

  private zuruecksetzen(): void {
    this.pollStoppen();
    this.phase.set('auswahl');
    this.fehler.set(null);
    this.busy.set(false);
    this.gewaehlt.set(null);
    this.positionen.set([]);
    this.sessionId = null;
  }

  private ladeAnbindungen(): void {
    this.svc.list(false).subscribe({
      next: (alle) => {
        const ids = alle.filter(
          (c) => c.source_system === 'IDS_CONNECT' && c.status === 'ACTIVE',
        );
        this.connections.set(ids);
        if (ids.length === 1) this.gewaehlt.set(ids[0].id);
      },
      error: (err: unknown) =>
        this.fehler.set(fehlerDetail(err) ?? 'Anbindungen konnten nicht geladen werden.'),
    });
  }

  waehlen(id: string): void {
    this.gewaehlt.set(id);
  }

  schliessen(): void {
    if (this.busy()) return;
    this.offen.set(false);
  }

  /** Startet den Punchout: Session anlegen, Shop-Formular in neuem Tab submitten. */
  shopOeffnen(): void {
    const connId = this.gewaehlt();
    if (!connId || this.busy()) return;
    // Das Zielfenster SYNCHRON im Klick-Handler öffnen: nur so lässt der
    // Popup-Blocker es durch (User-Aktivierung). Der eigentliche Submit folgt
    // erst nach dem Session-Request; wir submitten dann in genau dieses Fenster.
    const shopFenster = window.open('', '_blank');
    this.busy.set(true);
    this.fehler.set(null);
    this.svc
      .startPunchoutSession(connId, { action: 'WKE', quote_id: this.quoteId() })
      .subscribe({
        next: (start) => {
          this.busy.set(false);
          this.sessionId = start.session_id;
          if (!this.formularSubmitten(start.punchout, shopFenster)) {
            shopFenster?.close();
            this.fehler.set(
              'Der Shop konnte nicht geöffnet werden (Popup-Blocker?). Bitte Popups ' +
                'für diese Seite erlauben und erneut versuchen.',
            );
            return;
          }
          this.phase.set('wartet');
          this.pollStarten();
        },
        error: (err: unknown) => {
          this.busy.set(false);
          shopFenster?.close();
          this.fehler.set(
            fehlerDetail(err) ??
              'Der Punchout konnte nicht gestartet werden. Sind Shop-URL und Zugangsdaten hinterlegt?',
          );
        },
      });
  }

  /**
   * Submittet ein verstecktes POST-Formular in das zuvor synchron geöffnete
   * `fenster`. Gibt `false` zurück, wenn kein Fenster verfügbar ist (Popup
   * geblockt) oder das Ziel keine http(s)-URL ist — dann wird NICHT submittet.
   */
  private formularSubmitten(punchout: PunchoutForm, fenster: Window | null): boolean {
    if (!fenster) return false;
    // Nur http(s)-Ziele: die Shop-URL ist zwar admin-konfiguriert, aber ein
    // javascript:/data:-Ziel würde sonst im geöffneten Fenster ausgeführt.
    if (!/^https?:\/\//i.test(punchout.url)) return false;

    const target = `ids-shop-${this.sessionId ?? ''}`;
    try {
      fenster.name = target;
      // Rückwärts-Tabnabbing verhindern: das Shop-Fenster darf nicht auf uns
      // zugreifen (bevor es cross-origin navigiert, ist es noch same-origin).
      fenster.opener = null;
    } catch {
      /* Fenster ggf. schon fremd-navigiert — dann greift target trotzdem. */
    }
    const form = document.createElement('form');
    form.method = punchout.method || 'POST';
    form.action = punchout.url;
    form.enctype = punchout.enctype || 'multipart/form-data';
    form.target = target;
    form.style.display = 'none';
    for (const [name, wert] of Object.entries(punchout.fields)) {
      const input = document.createElement('input');
      input.type = 'hidden';
      input.name = name;
      input.value = wert;
      form.appendChild(input);
    }
    document.body.appendChild(form);
    form.submit();
    form.remove();
    return true;
  }

  private pollStarten(): void {
    this.pollStoppen();
    const id = this.sessionId;
    if (!id) return;
    // Alle 4 s den Session-Status abfragen, bis der Shop den Warenkorb liefert.
    // Ein transienter HTTP-Fehler wird INNERHALB des switchMap abgefangen (→ null),
    // damit er den äußeren Stream nicht beendet und das Polling weiterläuft.
    this.poll = timer(4000, 4000)
      .pipe(
        switchMap(() =>
          this.svc.punchoutSession(id).pipe(catchError(() => of(null))),
        ),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((s) => {
        if (s && s.status === 'EINGELOEST') this.fertigSetzen(s.positions);
      });
  }

  /** Manuelles Nachladen (falls der Nutzer nicht auf das Intervall warten will). */
  jetztPruefen(): void {
    const id = this.sessionId;
    if (!id || this.busy()) return;
    this.busy.set(true);
    this.svc.punchoutSession(id).subscribe({
      next: (s) => {
        this.busy.set(false);
        if (s.status === 'EINGELOEST') this.fertigSetzen(s.positions);
      },
      error: (err: unknown) => {
        this.busy.set(false);
        this.fehler.set(fehlerDetail(err) ?? 'Der Warenkorb konnte nicht geprüft werden.');
      },
    });
  }

  /** Positionen sind da: Poll stoppen, in die Vorschau wechseln und den Fokus auf
   * die Übernehmen-Schaltfläche setzen (Screenreader-/Tastatur-Kontext nach dem
   * automatischen Phasenwechsel). Die Vorschau ist zusätzlich `role="status"`. */
  private fertigSetzen(positions: ResolvedPosition[]): void {
    this.pollStoppen();
    this.positionen.set(positions);
    this.phase.set('fertig');
    // Nach dem Rendern der Schaltfläche fokussieren.
    setTimeout(() => this.uebernehmenBtn()?.nativeElement.focus(), 0);
  }

  private pollStoppen(): void {
    this.poll?.unsubscribe();
    this.poll = undefined;
  }

  uebernehmenBestaetigen(): void {
    const pos = this.positionen();
    if (pos.length === 0) return;
    this.uebernehmen.emit(pos);
    this.offen.set(false);
  }

  // --- Darstellungshelfer ----------------------------------------------------
  preis(p: ResolvedPosition): string {
    if (!p.net_price) return '—';
    const n = Number(p.net_price);
    return isNaN(n)
      ? p.net_price
      : new Intl.NumberFormat('de-DE', { style: 'currency', currency: 'EUR' }).format(n);
  }
}
