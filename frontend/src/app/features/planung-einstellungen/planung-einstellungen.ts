import { Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { AuthService } from '../../core/auth.service';
import { PlanungStammdatenService } from '../../core/planung-stammdaten.service';
import {
  AppointmentCategory,
  CATEGORY_COLORS,
  CategoryColorToken,
  Resource,
  RESOURCE_TYPES,
  ResourceType,
  categoryColorClass,
  resourceStatusLabel,
  resourceTypeLabel,
} from '../../core/einsatz.model';
import { PlanungNav } from '../planung-nav/planung-nav';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerDetail, fehlerState, istVerboten } from '../../shared/http-fehler';
import { Dialog } from '../../shared/dialog/dialog';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { Feld } from '../../shared/formular/feld';
import { FeldOption } from '../../shared/formular/feld';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';

type Meldung = { art: 'erfolg' | 'fehler'; text: string };

@Component({
  selector: 'app-planung-einstellungen',
  imports: [ReactiveFormsModule, PlanungNav, KeinZugriff, Dialog, Bestaetigung, Feld],
  templateUrl: './planung-einstellungen.html',
  styleUrl: './planung-einstellungen.scss',
})
export class PlanungEinstellungen {
  private readonly svc = inject(PlanungStammdatenService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  // --- Rechte (nur UI-Sichtbarkeit; der Server setzt sie durch) -----------
  protected readonly darfAnlegen = computed(() => this.auth.darf('workflow', 'ANLEGEN'));
  protected readonly darfAendern = computed(() => this.auth.darf('workflow', 'AENDERN'));

  protected readonly verboten = signal<VerbotenState | null>(null);
  protected readonly meldung = signal<Meldung | null>(null);

  protected readonly farbOptionen: FeldOption[] = CATEGORY_COLORS.map((c) => ({
    wert: c.token,
    label: c.label,
  }));
  protected readonly typOptionen: FeldOption[] = RESOURCE_TYPES.map((t) => ({
    wert: t.wert,
    label: t.label,
  }));

  // ===================== Terminkategorien ================================
  protected readonly kategorien = signal<AppointmentCategory[]>([]);
  protected readonly katArchivierte = signal(false);
  protected readonly katDialogOffen = signal(false);
  protected readonly katLaedt = signal(false);
  protected readonly katEditId = signal<string | null>(null);
  protected readonly katFormularMeldung = signal<string | null>(null);
  protected readonly katArchivieren = signal<AppointmentCategory | null>(null);
  protected readonly katArchivLaedt = signal(false);

  protected readonly katForm = this.fb.group({
    name: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(80)],
    }),
    color_token: this.fb.control<CategoryColorToken>('NAVY', { nonNullable: true }),
    description: this.fb.control('', { nonNullable: true }),
    sort_order: this.fb.control(0, { nonNullable: true }),
    // Übliche Dauer in Minuten. Leer = keine — dann schlägt der Termin-Dialog
    // kein Ende vor (er erfindet keins).
    default_duration_minutes: this.fb.control('', { nonNullable: true }),
  });

  // ===================== Ressourcen ======================================
  protected readonly ressourcen = signal<Resource[]>([]);
  protected readonly resInaktive = signal(false);
  protected readonly resDialogOffen = signal(false);
  protected readonly resLaedt = signal(false);
  protected readonly resEditId = signal<string | null>(null);
  protected readonly resFormularMeldung = signal<string | null>(null);
  protected readonly resArchivieren = signal<Resource | null>(null);
  protected readonly resArchivLaedt = signal(false);
  protected readonly aktionBusyId = signal<string | null>(null);

  protected readonly resForm = this.fb.group({
    name: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(120)],
    }),
    resource_type: this.fb.control<ResourceType>('FAHRZEUG', { nonNullable: true }),
    notes: this.fb.control('', { nonNullable: true }),
  });

  constructor() {
    this.ladeKategorien();
    this.ladeRessourcen();
  }

  // ---- Laden --------------------------------------------------------------
  private ladeKategorien(): void {
    this.svc.listKategorien(this.katArchivierte()).subscribe({
      next: (k) => this.kategorien.set(k),
      error: (err) => this.ladeFehler(err),
    });
  }

  private ladeRessourcen(): void {
    this.svc.listRessourcen({ includeInactive: this.resInaktive() }).subscribe({
      next: (r) => this.ressourcen.set(r),
      error: (err) => this.ladeFehler(err),
    });
  }

  private ladeFehler(err: unknown): void {
    const s = fehlerState(err);
    if (s.kind === 'forbidden') this.verboten.set(s);
  }

  katArchivierteUmschalten(wert: boolean): void {
    this.katArchivierte.set(wert);
    this.ladeKategorien();
  }
  resInaktiveUmschalten(wert: boolean): void {
    this.resInaktive.set(wert);
    this.ladeRessourcen();
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  // ===================== Kategorie: Anlegen/Bearbeiten ====================
  katNeu(): void {
    this.katEditId.set(null);
    this.katForm.reset({
      name: '', color_token: 'NAVY', description: '', sort_order: 0,
      default_duration_minutes: '',
    });
    this.katFormularMeldung.set(null);
    this.katDialogOffen.set(true);
  }

  katBearbeiten(k: AppointmentCategory): void {
    this.katEditId.set(k.id);
    this.katForm.reset({
      name: k.name,
      color_token: k.color_token,
      description: k.description ?? '',
      sort_order: k.sort_order,
      default_duration_minutes:
        k.default_duration_minutes != null ? String(k.default_duration_minutes) : '',
    });
    this.katFormularMeldung.set(null);
    this.katDialogOffen.set(true);
  }

  katDialogSchliessen(): void {
    if (!this.katLaedt()) this.katDialogOffen.set(false);
  }

  katAbsenden(): void {
    if (this.katLaedt()) return;
    serverFehlerZuruecksetzen(this.katForm);
    this.katFormularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.katForm);
    if (this.katForm.invalid) return;

    const v = this.katForm.getRawValue();
    const editId = this.katEditId();
    const dauerRoh = v.default_duration_minutes.trim();
    // Eine unlesbare Eingabe darf die Dauer nicht STILL löschen: `Number('x')` ist
    // NaN, und NaN serialisiert als `null` — der Wert wäre weg, ohne dass es
    // jemand merkt. Lieber ein klarer Formularfehler.
    const dauer = dauerRoh ? Number(dauerRoh.replace(',', '.')) : null;
    if (dauer !== null && !Number.isFinite(dauer)) {
      this.katFormularMeldung.set('Die übliche Dauer muss eine Zahl in Minuten sein.');
      return;
    }
    const payload = {
      name: v.name.trim(),
      color_token: v.color_token,
      description: v.description.trim() || null,
      sort_order: Number(v.sort_order) || 0,
      // Leeres Feld = ausdrücklich „keine übliche Dauer" (null), nicht 0 Minuten.
      default_duration_minutes: dauer,
    };
    this.katLaedt.set(true);
    const obs = editId
      ? this.svc.updateKategorie(editId, payload)
      : this.svc.createKategorie(payload);
    obs.subscribe({
      next: () => {
        this.katLaedt.set(false);
        this.katDialogOffen.set(false);
        this.meldung.set({
          art: 'erfolg',
          text: editId
            ? `Kategorie „${payload.name}“ aktualisiert.`
            : `Kategorie „${payload.name}“ angelegt.`,
        });
        this.ladeKategorien();
      },
      error: (err) => {
        this.katLaedt.set(false);
        this.katFormularMeldung.set(apiFehlerZuweisen(err, this.katForm).formular);
      },
    });
  }

  katArchivierenFragen(k: AppointmentCategory): void {
    this.katArchivieren.set(k);
  }
  katArchivierenAbbrechen(): void {
    if (!this.katArchivLaedt()) this.katArchivieren.set(null);
  }
  katArchivierenBestaetigen(): void {
    const k = this.katArchivieren();
    if (!k) return;
    this.katArchivLaedt.set(true);
    this.svc.archiveKategorie(k.id).subscribe({
      next: () => {
        this.katArchivLaedt.set(false);
        this.katArchivieren.set(null);
        this.meldung.set({ art: 'erfolg', text: `Kategorie „${k.name}“ archiviert.` });
        this.ladeKategorien();
      },
      error: (err) => {
        this.katArchivLaedt.set(false);
        this.katArchivieren.set(null);
        this.aktionsFehler(err);
      },
    });
  }

  // ===================== Ressource: Anlegen/Bearbeiten ===================
  resNeu(): void {
    this.resEditId.set(null);
    this.resForm.reset({ name: '', resource_type: 'FAHRZEUG', notes: '' });
    this.resFormularMeldung.set(null);
    this.resDialogOffen.set(true);
  }

  resBearbeiten(r: Resource): void {
    this.resEditId.set(r.id);
    this.resForm.reset({
      name: r.name,
      resource_type: r.resource_type,
      notes: r.notes ?? '',
    });
    this.resFormularMeldung.set(null);
    this.resDialogOffen.set(true);
  }

  resDialogSchliessen(): void {
    if (!this.resLaedt()) this.resDialogOffen.set(false);
  }

  resAbsenden(): void {
    if (this.resLaedt()) return;
    serverFehlerZuruecksetzen(this.resForm);
    this.resFormularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.resForm);
    if (this.resForm.invalid) return;

    const v = this.resForm.getRawValue();
    const editId = this.resEditId();
    const payload = {
      name: v.name.trim(),
      resource_type: v.resource_type,
      notes: v.notes.trim() || null,
    };
    this.resLaedt.set(true);
    const obs = editId
      ? this.svc.updateRessource(editId, payload)
      : this.svc.createRessource(payload);
    obs.subscribe({
      next: () => {
        this.resLaedt.set(false);
        this.resDialogOffen.set(false);
        this.meldung.set({
          art: 'erfolg',
          text: editId
            ? `Ressource „${payload.name}“ aktualisiert.`
            : `Ressource „${payload.name}“ angelegt.`,
        });
        this.ladeRessourcen();
      },
      error: (err) => {
        this.resLaedt.set(false);
        this.resFormularMeldung.set(apiFehlerZuweisen(err, this.resForm).formular);
      },
    });
  }

  resStatus(r: Resource, toStatus: string, erfolg: string): void {
    if (this.aktionBusyId()) return;
    this.aktionBusyId.set(r.id);
    this.meldung.set(null);
    this.svc.setRessourceStatus(r.id, toStatus).subscribe({
      next: () => {
        this.aktionBusyId.set(null);
        this.meldung.set({ art: 'erfolg', text: erfolg });
        this.ladeRessourcen();
      },
      error: (err) => {
        this.aktionBusyId.set(null);
        this.aktionsFehler(err);
      },
    });
  }

  resArchivierenFragen(r: Resource): void {
    this.resArchivieren.set(r);
  }
  resArchivierenAbbrechen(): void {
    if (!this.resArchivLaedt()) this.resArchivieren.set(null);
  }
  resArchivierenBestaetigen(): void {
    const r = this.resArchivieren();
    if (!r) return;
    this.resArchivLaedt.set(true);
    this.svc.setRessourceStatus(r.id, 'ARCHIVIERT').subscribe({
      next: () => {
        this.resArchivLaedt.set(false);
        this.resArchivieren.set(null);
        this.meldung.set({ art: 'erfolg', text: `Ressource „${r.name}“ archiviert.` });
        this.ladeRessourcen();
      },
      error: (err) => {
        this.resArchivLaedt.set(false);
        this.resArchivieren.set(null);
        this.aktionsFehler(err);
      },
    });
  }

  private aktionsFehler(err: unknown): void {
    const text = istVerboten(err)
      ? (fehlerDetail(err) ?? 'Keine Berechtigung für diese Aktion.')
      : (fehlerDetail(err) ?? 'Die Aktion ist fehlgeschlagen. Bitte erneut versuchen.');
    this.meldung.set({ art: 'fehler', text });
  }

  // ---- Darstellungshelfer -------------------------------------------------
  katColorClass(token: CategoryColorToken): string {
    return categoryColorClass(token);
  }

  /** Minuten menschenlesbar: 90 → „1 h 30 min", 45 → „45 min", 120 → „2 h". */
  dauerText(minuten: number): string {
    const h = Math.floor(minuten / 60);
    const m = minuten % 60;
    if (h === 0) return `${m} min`;
    return m === 0 ? `${h} h` : `${h} h ${m} min`;
  }
  typLabel(t: ResourceType): string {
    return resourceTypeLabel(t);
  }
  statusLabel(s: Resource['status']): string {
    return resourceStatusLabel(s);
  }
}
