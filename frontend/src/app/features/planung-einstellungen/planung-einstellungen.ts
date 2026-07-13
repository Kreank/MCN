import { Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { AuthService } from '../../core/auth.service';
import { PlanungStammdatenService } from '../../core/planung-stammdaten.service';
import {
  AppointmentCategory,
  CATEGORY_COLORS,
  CategoryColorToken,
  Qualifikation,
  Resource,
  RESOURCE_TYPES,
  ResourceType,
  Zuweisungsvorlage,
  categoryColorClass,
  resourceStatusLabel,
  resourceTypeLabel,
} from '../../core/einsatz.model';
import { AufgabeService } from '../../core/aufgabe.service';
import { AssignableUser } from '../../core/aufgabe.model';
import { forkJoin } from 'rxjs';
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
  private readonly aufgabeSvc = inject(AufgabeService);
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

  // ===================== Qualifikationen (Migration 0078) =================
  // Der Katalog ist FREI: `kind` ist ein Textfeld, kein Select. Der Betrieb legt
  // GEWERK, ZERTIFIKAT, HERSTELLERSCHULUNG oder was immer er braucht selbst an —
  // eine neue Art kostet keinen Deploy (User-Entscheidung: „dynamisch halten").
  protected readonly qualifikationen = signal<Qualifikation[]>([]);
  protected readonly qualInaktive = signal(false);
  protected readonly qualDialogOffen = signal(false);
  protected readonly qualLaedt = signal(false);
  protected readonly qualEditId = signal<string | null>(null);
  protected readonly qualFormularMeldung = signal<string | null>(null);

  protected readonly qualForm = this.fb.group({
    code: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(40)],
    }),
    label: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(120)],
    }),
    kind: this.fb.control('', { nonNullable: true }),
    description: this.fb.control('', { nonNullable: true }),
    expires: this.fb.control(false, { nonNullable: true }),
    sort_order: this.fb.control(0, { nonNullable: true }),
  });

  /** Bereits vergebene Arten — als Vorschlagsliste (datalist), nicht als Zwang. */
  protected readonly bekannteArten = computed(() =>
    [...new Set(this.qualifikationen().map((q) => q.kind).filter((k): k is string => !!k))].sort(),
  );

  // ===================== Zuweisungs-Vorlagen (lose Gruppen) ===============
  protected readonly vorlagen = signal<Zuweisungsvorlage[]>([]);
  protected readonly personen = signal<AssignableUser[]>([]);
  protected readonly vorlDialogOffen = signal(false);
  protected readonly vorlLaedt = signal(false);
  protected readonly vorlEditId = signal<string | null>(null);
  protected readonly vorlFormularMeldung = signal<string | null>(null);
  protected readonly vorlMitglieder = signal<{ app_user_id: string; role: string }[]>([]);

  protected readonly vorlForm = this.fb.group({
    name: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(80)],
    }),
    description: this.fb.control('', { nonNullable: true }),
    sort_order: this.fb.control(0, { nonNullable: true }),
  });

  constructor() {
    this.ladeKategorien();
    this.ladeRessourcen();
    this.ladeQualifikationen();
    this.ladeVorlagen();
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

  private ladeQualifikationen(): void {
    this.svc.listQualifikationen(this.qualInaktive()).subscribe({
      next: (q) => this.qualifikationen.set(q),
      error: (err) => this.ladeFehler(err),
    });
  }

  private ladeVorlagen(): void {
    forkJoin({
      vorlagen: this.svc.listVorlagen(),
      personen: this.aufgabeSvc.listAssignableUsers(),
    }).subscribe({
      next: (r) => {
        this.vorlagen.set(r.vorlagen);
        this.personen.set(r.personen);
      },
      error: (err) => this.ladeFehler(err),
    });
  }

  katArchivierteUmschalten(wert: boolean): void {
    this.katArchivierte.set(wert);
    this.ladeKategorien();
  }
  resInaktiveUmschalten(wert: boolean): void {
    this.resInaktive.set(wert);
    this.ladeRessourcen();
  }
  qualInaktiveUmschalten(wert: boolean): void {
    this.qualInaktive.set(wert);
    this.ladeQualifikationen();
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
    // Den Bedarf MIT zurücksetzen. Sonst erbt eine neue Kategorie stillschweigend
    // die Häkchen der zuletzt bearbeiteten — eine „Fensterreinigung", die den
    // Gasschein verlangt, und ab da falsche Warnungen auf jeder Kachel.
    // (Review-Fund.)
    this.katBedarf.set([]);
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
    // Den Bedarf dieses Termintyps nachladen (was er IMMER verlangt).
    this.katBedarf.set([]);
    this.svc.kategorieBedarf(k.id).subscribe({
      next: (qs) => this.katBedarf.set(qs.map((q) => q.id)),
      error: () => this.katBedarf.set([]),
    });
    this.katDialogOffen.set(true);
  }

  /**
   * Der Qualifikationsbedarf der Kategorie — was dieser Termintyp IMMER verlangt.
   * Am einzelnen Termin lässt sich zusätzlicher Bedarf ergänzen; wirksam ist die
   * VEREINIGUNG beider.
   */
  protected readonly katBedarf = signal<string[]>([]);

  katBedarfUmschalten(qualId: string): void {
    const liste = this.katBedarf();
    this.katBedarf.set(
      liste.includes(qualId)
        ? liste.filter((x) => x !== qualId)
        : [...liste, qualId],
    );
  }

  katBedarfGesetzt(qualId: string): boolean {
    return this.katBedarf().includes(qualId);
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
      next: (kat) => {
        // Der Kopf ist gespeichert. Ab hier ist der Dialog im BEARBEITEN-Modus —
        // sonst legte ein zweiter Anlauf nach einem Fehler im nächsten Schritt
        // eine ZWEITE Kategorie an (bzw. liefe in „existiert bereits"), und der
        // Bediener käme nicht mehr weiter. (Review-Fund.)
        this.katEditId.set(kat.id);
        // Den Bedarf im ANSCHLUSS setzen (eigener Endpunkt) — auch beim Anlegen,
        // damit ein neuer Termintyp seine Qualifikationen sofort mitbringt.
        this.svc.setKategorieBedarf(kat.id, this.katBedarf()).subscribe({
          next: () => this.katFertig(editId, payload.name),
          error: (err) => {
            // Kopf ist gespeichert, Bedarf nicht — das muss man sagen, nicht
            // hinter einer Erfolgsmeldung verstecken.
            this.katLaedt.set(false);
            this.katFormularMeldung.set(
              (fehlerDetail(err) ?? 'Der Qualifikationsbedarf ließ sich nicht ' +
                'speichern.') + ' Die übrigen Angaben sind gespeichert — ' +
                'ein erneutes Speichern versucht nur noch den Bedarf.',
            );
            this.ladeKategorien();
          },
        });
      },
      error: (err) => {
        this.katLaedt.set(false);
        this.katFormularMeldung.set(apiFehlerZuweisen(err, this.katForm).formular);
      },
    });
  }

  private katFertig(editId: string | null, name: string): void {
    this.katLaedt.set(false);
    this.katDialogOffen.set(false);
    this.meldung.set({
      art: 'erfolg',
      text: editId
        ? `Kategorie „${name}“ aktualisiert.`
        : `Kategorie „${name}“ angelegt.`,
    });
    this.ladeKategorien();
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

  // ===================== Qualifikation: Anlegen/Bearbeiten ================
  qualNeu(): void {
    this.qualEditId.set(null);
    this.qualForm.reset({
      code: '', label: '', kind: '', description: '', expires: false, sort_order: 0,
    });
    this.qualForm.controls.code.enable();
    this.qualFormularMeldung.set(null);
    this.qualDialogOffen.set(true);
  }

  qualBearbeiten(q: Qualifikation): void {
    this.qualEditId.set(q.id);
    this.qualForm.reset({
      code: q.code,
      label: q.label,
      kind: q.kind ?? '',
      description: q.description ?? '',
      expires: q.expires,
      sort_order: q.sort_order,
    });
    // Der Code ist der fachliche Schlüssel, auf den Bedarf und Nachweise zeigen —
    // er bleibt unveränderlich.
    this.qualForm.controls.code.disable();
    this.qualFormularMeldung.set(null);
    this.qualDialogOffen.set(true);
  }

  qualDialogSchliessen(): void {
    if (!this.qualLaedt()) this.qualDialogOffen.set(false);
  }

  qualAbsenden(): void {
    if (this.qualLaedt()) return;
    serverFehlerZuruecksetzen(this.qualForm);
    this.qualFormularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.qualForm);
    if (this.qualForm.invalid) return;

    const v = this.qualForm.getRawValue();
    const editId = this.qualEditId();
    this.qualLaedt.set(true);
    const obs = editId
      ? this.svc.updateQualifikation(editId, {
          label: v.label.trim(),
          kind: v.kind.trim() || null,
          description: v.description.trim() || null,
          expires: v.expires,
          sort_order: Number(v.sort_order) || 0,
        })
      : this.svc.createQualifikation({
          code: v.code.trim(),
          label: v.label.trim(),
          kind: v.kind.trim() || null,
          description: v.description.trim() || null,
          expires: v.expires,
          sort_order: Number(v.sort_order) || 0,
        });
    obs.subscribe({
      next: () => {
        this.qualLaedt.set(false);
        this.qualDialogOffen.set(false);
        this.meldung.set({
          art: 'erfolg',
          text: editId
            ? `Qualifikation „${v.label}“ aktualisiert.`
            : `Qualifikation „${v.label}“ angelegt.`,
        });
        this.ladeQualifikationen();
      },
      error: (err) => {
        this.qualLaedt.set(false);
        this.qualFormularMeldung.set(apiFehlerZuweisen(err, this.qualForm).formular);
      },
    });
  }

  /** Stilllegen/reaktivieren (statt Löschen — der Bedarf zeigt darauf). */
  qualAktivSetzen(q: Qualifikation, active: boolean): void {
    if (this.aktionBusyId()) return;
    this.aktionBusyId.set(q.id);
    this.svc.updateQualifikation(q.id, { active }).subscribe({
      next: () => {
        this.aktionBusyId.set(null);
        this.meldung.set({
          art: 'erfolg',
          text: active
            ? `„${q.label}“ ist wieder in Gebrauch.`
            : `„${q.label}“ ist stillgelegt und wird nicht mehr gefordert.`,
        });
        this.ladeQualifikationen();
      },
      error: (err) => {
        this.aktionBusyId.set(null);
        this.meldung.set({ art: 'fehler', text: fehlerDetail(err) ?? 'Fehlgeschlagen.' });
      },
    });
  }

  // ===================== Vorlage: Anlegen/Bearbeiten ======================
  vorlNeu(): void {
    this.vorlEditId.set(null);
    this.vorlForm.reset({ name: '', description: '', sort_order: 0 });
    this.vorlMitglieder.set([]);
    this.vorlFormularMeldung.set(null);
    this.vorlDialogOffen.set(true);
  }

  vorlBearbeiten(v: Zuweisungsvorlage): void {
    this.vorlEditId.set(v.id);
    this.vorlForm.reset({
      name: v.name,
      description: v.description ?? '',
      sort_order: v.sort_order,
    });
    this.vorlMitglieder.set(
      v.members.map((m) => ({ app_user_id: m.app_user_id, role: m.role })),
    );
    this.vorlFormularMeldung.set(null);
    this.vorlDialogOffen.set(true);
  }

  vorlDialogSchliessen(): void {
    if (!this.vorlLaedt()) this.vorlDialogOffen.set(false);
  }

  vorlMitgliedUmschalten(userId: string): void {
    const liste = this.vorlMitglieder();
    this.vorlMitglieder.set(
      liste.some((m) => m.app_user_id === userId)
        ? liste.filter((m) => m.app_user_id !== userId)
        : [...liste, { app_user_id: userId, role: 'TECHNICIAN' }],
    );
  }

  vorlIstMitglied(userId: string): boolean {
    return this.vorlMitglieder().some((m) => m.app_user_id === userId);
  }

  vorlIstLead(userId: string): boolean {
    return this.vorlMitglieder().some(
      (m) => m.app_user_id === userId && m.role === 'LEAD',
    );
  }

  /** Rolle umschalten (Techniker ↔ Einsatzleitung). */
  vorlRolleUmschalten(userId: string): void {
    this.vorlMitglieder.set(
      this.vorlMitglieder().map((m) =>
        m.app_user_id === userId
          ? { ...m, role: m.role === 'LEAD' ? 'TECHNICIAN' : 'LEAD' }
          : m,
      ),
    );
  }

  vorlAbsenden(): void {
    if (this.vorlLaedt()) return;
    serverFehlerZuruecksetzen(this.vorlForm);
    this.vorlFormularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.vorlForm);
    if (this.vorlForm.invalid) return;

    const v = this.vorlForm.getRawValue();
    const editId = this.vorlEditId();
    const payload = {
      name: v.name.trim(),
      description: v.description.trim() || null,
      sort_order: Number(v.sort_order) || 0,
      members: this.vorlMitglieder(),
    };
    this.vorlLaedt.set(true);
    const obs = editId
      ? this.svc.updateVorlage(editId, payload)
      : this.svc.createVorlage(payload);
    obs.subscribe({
      next: () => {
        this.vorlLaedt.set(false);
        this.vorlDialogOffen.set(false);
        this.meldung.set({
          art: 'erfolg',
          text: editId
            ? `Vorlage „${payload.name}“ aktualisiert.`
            : `Vorlage „${payload.name}“ angelegt.`,
        });
        this.ladeVorlagen();
      },
      error: (err) => {
        this.vorlLaedt.set(false);
        this.vorlFormularMeldung.set(apiFehlerZuweisen(err, this.vorlForm).formular);
      },
    });
  }

  /** Name der Person (für die Mitgliederliste). */
  personName(id: string): string {
    return this.personen().find((p) => p.id === id)?.display_name ?? id;
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
