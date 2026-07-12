import { Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { LohngruppeService } from '../../core/lohngruppe.service';
import { AuthService } from '../../core/auth.service';
import { WageGroup, WageGroupKind } from '../../core/lohngruppe.model';
import { Feld, FeldOption } from '../../shared/formular/feld';
import {
  apiZuDeAnzeige,
  apiZuDeEingabe,
  deZuApiDezimal,
  dezimalValidator,
} from '../../shared/formular/dezimal';
import { EinstellungenNav } from '../einstellungen-nav/einstellungen-nav';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';

type ViewState =
  { kind: 'loading' } | { kind: 'ready'; data: WageGroup[] } | VerbotenState | { kind: 'error' };

/**
 * Lohn-/Maschinengruppen (pricing.wage_group) — anlegen, bearbeiten,
 * deaktivieren (kein Löschen). `hourly_rate` ist der Verrechnungssatz (VK/Std.),
 * `cost_rate` der interne Kostensatz (für die Marge; optional). Ohne
 * `pricing/AENDERN` schreibgeschützt.
 */
@Component({
  selector: 'app-lohngruppen',
  imports: [ReactiveFormsModule, Feld, EinstellungenNav, KeinZugriff],
  templateUrl: './lohngruppen.html',
  styleUrl: './lohngruppen.scss',
})
export class Lohngruppen {
  private readonly svc = inject(LohngruppeService);
  private readonly fb = inject(FormBuilder);
  private readonly auth = inject(AuthService);

  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly darfAendern = computed(() => this.auth.darf('pricing', 'AENDERN'));
  protected readonly laedt = signal(false);
  protected readonly meldung = signal<string | null>(null);
  /** 'neu' beim Anlegen, WageGroup-id beim Bearbeiten, sonst null. */
  protected readonly modus = signal<string | null>(null);

  protected readonly arten: FeldOption[] = [
    { wert: 'LOHN', label: 'Lohn (Personal)' },
    { wert: 'MASCHINE', label: 'Maschine / Gerät' },
  ];

  protected readonly form = this.fb.group({
    name: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    kind: this.fb.control<WageGroupKind>('LOHN', { nonNullable: true }),
    hourly_rate: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, dezimalValidator],
    }),
    cost_rate: this.fb.control('', { nonNullable: true, validators: [dezimalValidator] }),
  });

  constructor() {
    this.laden();
  }

  private laden(): void {
    this.state.set({ kind: 'loading' });
    this.svc.list().subscribe({
      next: (data) => this.state.set({ kind: 'ready', data }),
      error: (err: unknown) => this.state.set(fehlerState(err)),
    });
  }

  protected artLabel(kind: WageGroupKind): string {
    return kind === 'MASCHINE' ? 'Maschine / Gerät' : 'Lohn (Personal)';
  }

  /** Betrag als „65,00 €" bzw. „—", wenn nicht hinterlegt (z. B. Kostensatz). */
  protected euro(wert: string | null): string {
    if (wert == null || String(wert).trim() === '') return '—';
    return `${apiZuDeAnzeige(wert, 2)} €`;
  }

  neu(): void {
    if (!this.darfAendern()) return;
    this.meldung.set(null);
    this.form.reset({ name: '', kind: 'LOHN', hourly_rate: '', cost_rate: '' });
    this.modus.set('neu');
  }

  starteBearbeiten(g: WageGroup): void {
    if (!this.darfAendern()) return;
    this.meldung.set(null);
    this.form.reset({
      name: g.name,
      kind: g.kind,
      hourly_rate: apiZuDeEingabe(g.hourly_rate, 2),
      cost_rate: apiZuDeEingabe(g.cost_rate, 2),
    });
    this.modus.set(g.id);
  }

  abbrechen(): void {
    this.modus.set(null);
    this.meldung.set(null);
  }

  speichern(): void {
    if (this.laedt()) return;
    this.meldung.set(null);
    this.form.markAllAsTouched();
    if (this.form.invalid) return;
    const v = this.form.getRawValue();
    const cost = deZuApiDezimal(v.cost_rate);
    const payload = {
      name: v.name,
      kind: v.kind,
      hourly_rate: deZuApiDezimal(v.hourly_rate),
      cost_rate: cost === '' ? null : cost,
    };
    const modus = this.modus();
    this.laedt.set(true);
    const request$ = modus === 'neu' ? this.svc.create(payload) : this.svc.update(modus!, payload);
    request$.subscribe({
      next: () => {
        this.laedt.set(false);
        this.modus.set(null);
        this.laden();
      },
      error: (err: unknown) => {
        this.laedt.set(false);
        this.meldung.set(fehlerDetail(err) ?? 'Die Lohngruppe konnte nicht gespeichert werden.');
      },
    });
  }

  umschalten(g: WageGroup): void {
    if (this.laedt() || !this.darfAendern()) return;
    this.meldung.set(null);
    this.laedt.set(true);
    const status = g.status === 'AKTIV' ? 'INAKTIV' : 'AKTIV';
    this.svc.update(g.id, { status }).subscribe({
      next: () => {
        this.laedt.set(false);
        this.laden();
      },
      error: (err: unknown) => {
        this.laedt.set(false);
        this.meldung.set(fehlerDetail(err) ?? 'Der Status konnte nicht geändert werden.');
      },
    });
  }
}
