import { Component, ElementRef, computed, inject, signal, viewChild } from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Observable } from 'rxjs';
import { AufgabeService } from '../../core/aufgabe.service';
import { AuthService } from '../../core/auth.service';
import { BenachrichtigungService } from '../../core/benachrichtigung.service';
import { Task, TaskComment, TaskStatus } from '../../core/aufgabe.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { VerbotenState, fehlerDetail, fehlerState, istVerboten } from '../../shared/http-fehler';
import { isoDatumDe } from '../../shared/datum';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: Task }
  | VerbotenState
  | { kind: 'error' };

/**
 * Die Aufgabe als eigene Seite — mit dem Faden, in dem Rückfragen stehen.
 *
 * **Warum es sie überhaupt gibt.** Bis hierhin war die Aufgabe nur eine Zeile
 * in einer Liste. Eine Benachrichtigung („Marius hat eine Frage") hatte damit
 * kein Ziel, das man anspringen kann, und eine Rückfrage keinen Ort, an dem sie
 * stehen konnte. Beides zusammen war der Grund, warum die Abstimmung zu einer
 * Aufgabe zwangsläufig über Telefon oder WhatsApp lief.
 *
 * **Der Faden ist append-only** (DB-Trigger, Migration 0137): Geschriebenes
 * bleibt. Das UI bietet deshalb bewusst kein Bearbeiten und kein Löschen an —
 * eine Schaltfläche, die der Server verweigert, wäre schlimmer als keine.
 */
@Component({
  selector: 'app-aufgabe-detail',
  imports: [RouterLink, ReactiveFormsModule, KeinZugriff, Bestaetigung],
  templateUrl: './aufgabe-detail.html',
  styleUrl: './aufgabe-detail.scss',
})
export class AufgabeDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(AufgabeService);
  private readonly auth = inject(AuthService);
  private readonly benachrichtigungen = inject(BenachrichtigungService);
  private readonly fb = inject(FormBuilder);

  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly faden = signal<TaskComment[]>([]);
  protected readonly fadenLaedt = signal(true);
  protected readonly fadenFehler = signal(false);
  protected readonly ansage = signal('');
  protected readonly aktionsFehler = signal<string | null>(null);
  protected readonly aktionBusy = signal(false);
  protected readonly sendeBusy = signal(false);
  protected readonly verwerfenOffen = signal(false);

  protected readonly darfAendern = computed(() => this.auth.darf('workflow', 'AENDERN'));

  /** Deckungsgleich mit `MAX_KOMMENTAR_ZEICHEN` im Aufgaben-Service. */
  protected readonly maxZeichen = 4000;

  protected readonly form = this.fb.group({
    body: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(this.maxZeichen)],
    }),
  });

  /** Restliche Zeichen — sagt aus, warum „Senden" irgendwann nicht mehr geht. */
  private readonly bodyWert = toSignal(this.form.controls.body.valueChanges, {
    initialValue: '',
  });
  protected readonly restZeichen = computed(() =>
    Math.max(0, this.maxZeichen - this.bodyWert().length),
  );

  private readonly fadenEnde = viewChild<ElementRef<HTMLElement>>('fadenEnde');

  private reqId = 0;
  private id: string | null = null;

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.aktionsFehler.set(null);
      this.form.reset({ body: '' });
      if (!id) {
        this.state.set({ kind: 'error' });
        return;
      }
      this.id = id;
      this.laden(id);
      this.fadenLaden(id);
      // Wer die Aufgabe öffnet, hat die Meldung dazu gesehen. Der Zähler in der
      // Kopfzeile wird deshalb frisch geholt — sonst stünde er noch auf der
      // Zahl von vor dem Klick.
      this.benachrichtigungen.zaehler().subscribe({ error: () => {} });
    });
  }

  private laden(id: string): void {
    const rid = ++this.reqId;
    this.state.set({ kind: 'loading' });
    this.svc.get(id).subscribe({
      next: (t) => {
        if (rid === this.reqId) this.state.set({ kind: 'ready', data: t });
      },
      error: (err) => {
        if (rid === this.reqId) this.state.set(fehlerState(err));
      },
    });
  }

  private fadenLaden(id: string, ansEnde = false): void {
    this.fadenLaedt.set(true);
    this.fadenFehler.set(false);
    this.svc.comments(id).subscribe({
      next: (k) => {
        this.faden.set(k);
        this.fadenLaedt.set(false);
        if (ansEnde) this.zumEnde();
      },
      error: () => {
        this.fadenLaedt.set(false);
        this.fadenFehler.set(true);
      },
    });
  }

  retry(): void {
    const id = this.route.snapshot.paramMap.get('id');
    if (id) {
      this.laden(id);
      this.fadenLaden(id);
    }
  }

  fadenErneut(): void {
    if (this.id) this.fadenLaden(this.id);
  }

  /**
   * `setTimeout` statt `queueMicrotask`: Angular zeichnet zonenlos über einen
   * Microtask-Scheduler neu. Ein Microtask liefe also womöglich VOR dem Rendern
   * — der Marker existierte noch nicht, und der Sprung ginge ins Leere.
   */
  private zumEnde(): void {
    setTimeout(
      () => this.fadenEnde()?.nativeElement.scrollIntoView({ block: 'nearest' }),
      0,
    );
  }

  // --- Rückfrage schreiben -------------------------------------------------

  absenden(): void {
    const id = this.id;
    if (!id || this.sendeBusy()) return;
    this.form.markAllAsTouched();
    if (this.form.invalid) return;
    const body = this.form.getRawValue().body.trim();
    if (!body) return;

    this.sendeBusy.set(true);
    this.aktionsFehler.set(null);
    this.svc.comment(id, body).subscribe({
      next: (k) => {
        this.sendeBusy.set(false);
        this.form.reset({ body: '' });
        this.faden.update((liste) => [...liste, k]);
        this.ansage.set('Ihr Beitrag wurde gespeichert.');
        this.zumEnde();
      },
      error: (err) => {
        this.sendeBusy.set(false);
        this.aktionsFehler.set(
          fehlerDetail(err) ??
            (istVerboten(err)
              ? 'Keine Berechtigung, an dieser Aufgabe zu schreiben.'
              : 'Der Beitrag konnte nicht gespeichert werden. Bitte erneut versuchen.'),
        );
      },
    });
  }

  /**
   * Strg/⌘ + Enter sendet. Enter allein bleibt der Zeilenumbruch: Eine
   * Rückfrage ist oft zwei Sätze lang, und ein Feld, das beim ersten Enter
   * absendet, erzeugt genau die halben Nachrichten, die man aus Chats kennt —
   * hier wären sie unlöschbar.
   */
  onFeldKey(event: KeyboardEvent): void {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      this.absenden();
    }
  }

  // --- Statusaktionen ------------------------------------------------------

  erledigen(): void {
    const t = this.daten();
    if (t) this.aktion(this.svc.complete(t.id), 'Aufgabe als erledigt markiert.');
  }

  wiederOeffnen(): void {
    const t = this.daten();
    if (t) this.aktion(this.svc.reopen(t.id), 'Aufgabe wieder geöffnet.');
  }

  verwerfenFragen(): void {
    this.verwerfenOffen.set(true);
  }

  verwerfenAbbrechen(): void {
    if (!this.aktionBusy()) this.verwerfenOffen.set(false);
  }

  verwerfenBestaetigen(): void {
    const t = this.daten();
    if (!t) return;
    this.aktion(this.svc.discard(t.id), 'Aufgabe verworfen.', () =>
      this.verwerfenOffen.set(false),
    );
  }

  private aktion(obs: Observable<Task>, erfolg: string, danach?: () => void): void {
    if (this.aktionBusy()) return;
    this.aktionBusy.set(true);
    this.aktionsFehler.set(null);
    obs.subscribe({
      next: (t) => {
        this.aktionBusy.set(false);
        danach?.();
        this.state.set({ kind: 'ready', data: t });
        this.ansage.set(erfolg);
        // Der Statuswechsel schreibt serverseitig einen Vermerk in den Faden —
        // der muss nachgeladen werden, sonst fehlt er bis zum nächsten Aufruf.
        if (this.id) this.fadenLaden(this.id, true);
      },
      error: (err) => {
        this.aktionBusy.set(false);
        danach?.();
        this.aktionsFehler.set(
          fehlerDetail(err) ??
            (istVerboten(err)
              ? 'Keine Berechtigung für diese Aktion.'
              : 'Die Aktion ist fehlgeschlagen. Bitte erneut versuchen.'),
        );
      },
    });
  }

  // --- Darstellung ---------------------------------------------------------

  protected datum(iso: string | null): string {
    return iso ? isoDatumDe(iso.slice(0, 10)) : '';
  }

  protected zeitpunkt(iso: string): string {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    const uhr = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    return `${isoDatumDe(iso.slice(0, 10))}, ${uhr} Uhr`;
  }

  statusLabel(s: TaskStatus): string {
    switch (s) {
      case 'OFFEN':
        return 'Offen';
      case 'ERLEDIGT':
        return 'Erledigt';
      case 'VERWORFEN':
        return 'Verworfen';
    }
  }

  statusClass(s: TaskStatus): string {
    if (s === 'ERLEDIGT') return 'stamp--positive';
    if (s === 'VERWORFEN') return 'stamp--warn';
    return '';
  }
}
