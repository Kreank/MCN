import { Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { FirmaService } from '../../core/firma.service';
import { AuthService } from '../../core/auth.service';
import { DunningLevel } from '../../core/firma.model';
import { Feld } from '../../shared/formular/feld';
import { EinstellungenNav } from '../einstellungen-nav/einstellungen-nav';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: DunningLevel[] }
  | VerbotenState
  | { kind: 'error' };

/**
 * Mahnstufen-Konfiguration (bis 6 Stufen). Pflegbar sind Bezeichnung, Frist
 * (Tage nach Fälligkeit) und Aktivierung — je Stufe inline. Gebühren/Zinsen
 * bleiben bewusst leer (STB-Vorbehalt B-22).
 *
 * Lücken-Regel: eine mittlere Stufe zu deaktivieren, während eine höhere aktiv
 * bleibt, lehnt der Server mit 422 ab; die Meldung erscheint an der Zeile. Ohne
 * `invoicing/AENDERN` ist die Tabelle schreibgeschützt.
 */
@Component({
  selector: 'app-mahnstufen',
  imports: [ReactiveFormsModule, Feld, EinstellungenNav, KeinZugriff],
  templateUrl: './mahnstufen.html',
  styleUrl: './mahnstufen.scss',
})
export class Mahnstufen {
  private readonly svc = inject(FirmaService);
  private readonly fb = inject(FormBuilder);
  private readonly auth = inject(AuthService);

  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly darfAendern = computed(() => this.auth.darf('invoicing', 'AENDERN'));

  /** Aktuell bearbeitete Stufe (oder null). */
  protected readonly bearbeite = signal<number | null>(null);
  protected readonly laedt = signal(false);
  protected readonly zeilenMeldung = signal<string | null>(null);
  protected readonly erfolg = signal<string | null>(null);

  protected readonly form = this.fb.group({
    label: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    days_after_due: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.pattern(/^\d+$/)],
    }),
    active: this.fb.control(true, { nonNullable: true }),
  });

  constructor() {
    this.laden();
  }

  private laden(): void {
    this.state.set({ kind: 'loading' });
    this.svc.listDunningLevels().subscribe({
      next: (data) => this.state.set({ kind: 'ready', data }),
      error: (err: unknown) => this.state.set(fehlerState(err)),
    });
  }

  starteBearbeiten(lv: DunningLevel): void {
    if (!this.darfAendern()) return;
    this.zeilenMeldung.set(null);
    this.erfolg.set(null);
    this.form.reset({
      label: lv.label,
      days_after_due: String(lv.days_after_due),
      active: lv.active,
    });
    this.bearbeite.set(lv.level);
  }

  abbrechen(): void {
    this.bearbeite.set(null);
    this.zeilenMeldung.set(null);
  }

  speichern(level: number): void {
    if (this.laedt()) return;
    this.zeilenMeldung.set(null);
    this.form.markAllAsTouched();
    if (this.form.invalid) return;
    const v = this.form.getRawValue();
    this.laedt.set(true);
    this.svc
      .updateDunningLevel(level, {
        label: v.label.trim(),
        days_after_due: Number(v.days_after_due),
        active: v.active,
      })
      .subscribe({
        next: () => {
          this.laedt.set(false);
          this.bearbeite.set(null);
          this.erfolg.set(`Mahnstufe ${level} gespeichert.`);
          this.laden();
        },
        error: (err: unknown) => {
          this.laedt.set(false);
          this.zeilenMeldung.set(
            fehlerDetail(err) ?? 'Die Mahnstufe konnte nicht gespeichert werden.',
          );
        },
      });
  }
}
