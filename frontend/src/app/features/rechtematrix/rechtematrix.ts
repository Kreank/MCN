import { Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { forkJoin } from 'rxjs';
import { AuthService } from '../../core/auth.service';
import { RechtematrixService } from '../../core/rechtematrix.service';
import {
  AppUserRow,
  PermissionCell,
  Role,
  UserRole,
  aktionLabel,
  modulLabel,
} from '../../core/rechtematrix.model';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { Dialog } from '../../shared/dialog/dialog';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { EinstellungenNav } from '../einstellungen-nav/einstellungen-nav';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready' }
  | VerbotenState
  | { kind: 'error' };

/** Eine Zuordnung mit der ID, die zum Beenden nötig ist. */
interface Zuordnung {
  id: string;
  role_code: string;
  role_label: string;
}

/** Benutzer + seine aktiven Rollenzuordnungen (Abschnitt 2). */
interface BenutzerZeile {
  id: string;
  display_name: string;
  status: string;
  /** Anmeldeadresse; null bei fachlicher Identität ohne Login (Altbestand). */
  email: string | null;
  kann_anmelden: boolean;
  zuordnungen: Zuordnung[];
}

/** Ziel eines Beenden-Vorgangs (für den Bestätigungsdialog). */
interface BeendenZiel {
  benutzer: BenutzerZeile;
  zuordnung: Zuordnung;
}

/** Trennzeichen für den Zellen-Schlüssel. Rollen-, Modul- und Aktionscodes sind
 *  Bezeichner ohne Doppelpunkt — der Schlüssel bleibt eindeutig. KEIN NUL-Byte:
 *  das machte die Datei für git binär und damit nicht diffbar (nicht reviewbar). */
const SEP = '::';

/**
 * Rechtematrix-Pflege (Einstellungen · Rechte). Zwei Abschnitte:
 *
 * 1. **Rechtematrix** — Rolle × Modul × Aktion. Layout-Entscheidung: Die volle
 *    Matrix hätte drei Dimensionen (14 Module × 8 Aktionen × N Rollen); als eine
 *    Tabelle wäre sie unbedienbar. Deshalb **eine Rolle zur Zeit** (Rollen-
 *    Umschalter) und darunter ein zweidimensionales Raster Modul (Zeilen) ×
 *    Aktion (Spalten). Jede Zelle ist ein echtes `<input type=checkbox>` mit
 *    zugänglichem Namen; ist die Aktion erlaubt, erscheint ein Select für den
 *    row_scope (ALLE/EIGENE). Änderungen laufen optimistisch mit Rollback.
 *
 * 2. **Rollenzuordnungen** — Benutzer mit ihren aktiven Rollen; Zuweisen über
 *    Dialog, Beenden über Bestätigungsdialog (unumkehrbar).
 *
 * Ohne `security/AENDERN` ist alles schreibgeschützt (Schalter deaktiviert, kein
 * Zuweisen/Beenden). Der Server setzt zusätzlich drei Härtungen durch, deren
 * 422-Klartext hier wörtlich angezeigt wird: keine Selbst-Erweiterung der
 * eigenen Rolle, keine Selbstzuweisung, keine Aufhebung der letzten aktiven
 * ADMINISTRATION.
 */
@Component({
  selector: 'app-rechtematrix',
  imports: [ReactiveFormsModule, Feld, Dialog, Bestaetigung, EinstellungenNav, KeinZugriff],
  templateUrl: './rechtematrix.html',
  styleUrl: './rechtematrix.scss',
})
export class Rechtematrix {
  private readonly svc = inject(RechtematrixService);
  private readonly fb = inject(FormBuilder);
  private readonly auth = inject(AuthService);

  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly verbotenDetail = signal<string | null>(null);
  protected readonly darfAendern = computed(() => this.auth.darf('security', 'AENDERN'));

  /** Aktiver Abschnitt (Umschalter oben). */
  protected readonly abschnitt = signal<'matrix' | 'zuordnungen'>('matrix');

  // --- Matrix-Daten --------------------------------------------------------
  protected readonly modules = signal<string[]>([]);
  protected readonly actions = signal<string[]>([]);
  protected readonly roles = signal<Role[]>([]);
  /** Schlüssel `role SEP module SEP action` -> Zelle. Fehlt = nicht erlaubt. */
  private readonly cellMap = signal<Map<string, PermissionCell>>(new Map());
  protected readonly aktiveRolle = signal<string>('');
  protected readonly matrixMeldung = signal<string | null>(null);

  // --- Zuordnungs-Daten ----------------------------------------------------
  protected readonly benutzer = signal<BenutzerZeile[]>([]);
  protected readonly zuordnungMeldung = signal<string | null>(null);
  protected readonly zuordnungErfolg = signal<string | null>(null);

  // --- Rolle zuweisen (Dialog) ---------------------------------------------
  protected readonly zuweisenFuer = signal<BenutzerZeile | null>(null);
  protected readonly zuweisenLaedt = signal(false);
  protected readonly zuweisenMeldung = signal<string | null>(null);
  protected readonly zuweisenForm = this.fb.group({
    role_code: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    valid_from: this.fb.control('', { nonNullable: true }),
  });

  // --- Zuordnung beenden (Bestätigung) -------------------------------------
  protected readonly beendenZiel = signal<BeendenZiel | null>(null);
  protected readonly beendenLaedt = signal(false);

  // --- Benutzer anlegen (Dialog) -------------------------------------------
  // Eigenes Recht: security/ANLEGEN. Ein Konto anzulegen ist die Vergabe eines
  // Systemzugangs und damit eine andere Entscheidung als eine Rolle zu ändern.
  protected readonly darfAnlegen = computed(() => this.auth.darf('security', 'ANLEGEN'));
  protected readonly anlegenOffen = signal(false);
  protected readonly anlegenLaedt = signal(false);
  protected readonly anlegenMeldung = signal<string | null>(null);
  protected readonly anlegenForm = this.fb.group({
    display_name: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    email: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.email],
    }),
    // Mindestlänge nur als Sofort-Hinweis; die verbindliche Prüfung macht
    // Djangos validate_password auf dem Server (422 im Klartext).
    password: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.minLength(8)],
    }),
  });

  // --- Benutzer sperren/freigeben (Bestätigung) ----------------------------
  protected readonly sperrenZiel = signal<BenutzerZeile | null>(null);
  protected readonly sperrenLaedt = signal(false);

  // Label-Helfer für das Template.
  protected readonly modulLabel = modulLabel;
  protected readonly aktionLabel = aktionLabel;

  protected readonly aktiveRolleLabel = computed(() => {
    const code = this.aktiveRolle();
    return this.roles().find((r) => r.code === code)?.label ?? code;
  });

  protected readonly rollenOptionen = computed<FeldOption[]>(() =>
    this.roles().map((r) => ({ wert: r.code, label: `${r.label} (${r.code})` })),
  );

  protected readonly scopeOptionen: FeldOption[] = [
    { wert: 'ALLE', label: 'Alle' },
    { wert: 'EIGENE', label: 'Eigene' },
  ];

  constructor() {
    this.laden();
  }

  private laden(): void {
    this.state.set({ kind: 'loading' });
    forkJoin({
      matrix: this.svc.getPermissions(),
      users: this.svc.listUsers(),
      userRoles: this.svc.listUserRoles(true),
    }).subscribe({
      next: ({ matrix, users, userRoles }) => {
        this.modules.set(matrix.modules);
        this.actions.set(matrix.actions);
        this.roles.set(matrix.roles);
        this.cellMap.set(this.baueCellMap(matrix.cells));
        if (!this.aktiveRolle() && matrix.roles.length) {
          this.aktiveRolle.set(matrix.roles[0].code);
        }
        this.benutzer.set(this.baueBenutzer(users, userRoles));
        this.state.set({ kind: 'ready' });
      },
      error: (err: unknown) => {
        const s = fehlerState(err);
        if (s.kind === 'forbidden') this.verbotenDetail.set(s.detail);
        this.state.set(s);
      },
    });
  }

  private baueCellMap(cells: PermissionCell[]): Map<string, PermissionCell> {
    const m = new Map<string, PermissionCell>();
    for (const c of cells) m.set(this.key(c.role_code, c.module, c.action), c);
    return m;
  }

  private baueBenutzer(users: AppUserRow[], userRoles: UserRole[]): BenutzerZeile[] {
    const proUser = new Map<string, Zuordnung[]>();
    for (const ur of userRoles) {
      if (!ur.is_active) continue;
      const liste = proUser.get(ur.user_id) ?? [];
      liste.push({ id: ur.id, role_code: ur.role_code, role_label: ur.role_label });
      proUser.set(ur.user_id, liste);
    }
    return users.map((u) => ({
      id: u.id,
      display_name: u.display_name,
      status: u.status,
      email: u.email,
      kann_anmelden: u.kann_anmelden,
      zuordnungen: (proUser.get(u.id) ?? []).sort((a, b) =>
        a.role_label.localeCompare(b.role_label, 'de'),
      ),
    }));
  }

  private key(role: string, module: string, action: string): string {
    return `${role}${SEP}${module}${SEP}${action}`;
  }

  // --- Abschnitt 1: Matrix -------------------------------------------------

  waehleRolle(code: string): void {
    this.aktiveRolle.set(code);
    this.matrixMeldung.set(null);
  }

  /** Zustand einer Zelle für die aktive Rolle (Default: nicht erlaubt, ALLE). */
  zelle(module: string, action: string): { allowed: boolean; row_scope: string } {
    const c = this.cellMap().get(this.key(this.aktiveRolle(), module, action));
    return c ? { allowed: c.allowed, row_scope: c.row_scope } : { allowed: false, row_scope: 'ALLE' };
  }

  /** Die Rolle wird IMMER explizit übergeben: ein Rollback darf nicht in die
   * Rolle schreiben, die gerade aktiv ist, sondern in die, für die die Anfrage
   * lief — der Nutzer kann während des Requests umgeschaltet haben. */
  private setzeLokal(
    role: string, module: string, action: string, allowed: boolean, row_scope: string,
  ): void {
    const naechste = new Map(this.cellMap());
    naechste.set(this.key(role, module, action), { role_code: role, module, action, allowed, row_scope });
    this.cellMap.set(naechste);
  }

  /** Laufende Schreibversion je Zelle: die Antwort einer überholten Anfrage darf
   * eine neuere nicht überschreiben (out-of-order responses). */
  private readonly zellenVersion = new Map<string, number>();

  private naechsteVersion(k: string): number {
    const v = (this.zellenVersion.get(k) ?? 0) + 1;
    this.zellenVersion.set(k, v);
    return v;
  }

  private istAktuell(k: string, v: number): boolean {
    return this.zellenVersion.get(k) === v;
  }

  private setzeVonServer(cell: PermissionCell): void {
    const naechste = new Map(this.cellMap());
    naechste.set(this.key(cell.role_code, cell.module, cell.action), cell);
    this.cellMap.set(naechste);
  }

  umschalten(module: string, action: string, allowed: boolean): void {
    if (!this.darfAendern()) return;
    const role = this.aktiveRolle();
    const vorher = this.zelle(module, action);
    const scope = vorher.row_scope || 'ALLE';
    const k = this.key(role, module, action);
    const v = this.naechsteVersion(k);
    this.matrixMeldung.set(null);
    // Optimistisch setzen, bei Fehler zurückrollen.
    this.setzeLokal(role, module, action, allowed, scope);
    this.svc
      .setPermission({ role_code: role, module, action, allowed, row_scope: scope })
      .subscribe({
        next: (cell) => {
          if (this.istAktuell(k, v)) this.setzeVonServer(cell);
        },
        error: (err: unknown) => {
          if (!this.istAktuell(k, v)) return;
          this.setzeLokal(role, module, action, vorher.allowed, vorher.row_scope);
          this.matrixMeldung.set(
            fehlerDetail(err) ?? 'Die Berechtigung konnte nicht geändert werden.',
          );
        },
      });
  }

  scopeAendern(module: string, action: string, row_scope: string): void {
    if (!this.darfAendern()) return;
    const role = this.aktiveRolle();
    const vorher = this.zelle(module, action);
    const k = this.key(role, module, action);
    const v = this.naechsteVersion(k);
    this.matrixMeldung.set(null);
    this.setzeLokal(role, module, action, true, row_scope);
    this.svc
      .setPermission({ role_code: role, module, action, allowed: true, row_scope })
      .subscribe({
        next: (cell) => {
          if (this.istAktuell(k, v)) this.setzeVonServer(cell);
        },
        error: (err: unknown) => {
          if (!this.istAktuell(k, v)) return;
          this.setzeLokal(role, module, action, vorher.allowed, vorher.row_scope);
          this.matrixMeldung.set(
            fehlerDetail(err) ?? 'Der Sichtbarkeitsbereich konnte nicht geändert werden.',
          );
        },
      });
  }

  // --- Abschnitt 2: Rollenzuordnungen --------------------------------------

  private ladeZuordnungen(): void {
    forkJoin({
      users: this.svc.listUsers(),
      userRoles: this.svc.listUserRoles(true),
    }).subscribe({
      next: ({ users, userRoles }) => this.benutzer.set(this.baueBenutzer(users, userRoles)),
      error: (err: unknown) =>
        this.zuordnungMeldung.set(
          fehlerDetail(err) ?? 'Die Rollenzuordnungen konnten nicht neu geladen werden.',
        ),
    });
  }

  // Rolle zuweisen -----------------------------------------------------------

  starteZuweisen(b: BenutzerZeile): void {
    if (!this.darfAendern()) return;
    this.zuordnungErfolg.set(null);
    this.zuweisenMeldung.set(null);
    this.zuweisenForm.reset({ role_code: '', valid_from: '' });
    this.zuweisenFuer.set(b);
  }

  zuweisenSchliessen(): void {
    if (this.zuweisenLaedt()) return;
    this.zuweisenFuer.set(null);
    this.zuweisenMeldung.set(null);
  }

  zuweisenAbsenden(): void {
    const ziel = this.zuweisenFuer();
    if (!ziel || this.zuweisenLaedt()) return;
    this.zuweisenMeldung.set(null);
    this.zuweisenForm.markAllAsTouched();
    if (this.zuweisenForm.invalid) return;
    const v = this.zuweisenForm.getRawValue();
    this.zuweisenLaedt.set(true);
    this.svc
      .assignRole({
        user_id: ziel.id,
        role_code: v.role_code,
        valid_from: v.valid_from.trim() || undefined,
      })
      .subscribe({
        next: (ur) => {
          this.zuweisenLaedt.set(false);
          this.zuweisenFuer.set(null);
          this.zuordnungErfolg.set(
            `Rolle „${ur.role_label}“ wurde ${ziel.display_name} zugewiesen.`,
          );
          this.ladeZuordnungen();
        },
        error: (err: unknown) => {
          this.zuweisenLaedt.set(false);
          this.zuweisenMeldung.set(
            fehlerDetail(err) ?? 'Die Rolle konnte nicht zugewiesen werden.',
          );
        },
      });
  }

  // Zuordnung beenden --------------------------------------------------------

  starteBeenden(benutzer: BenutzerZeile, zuordnung: Zuordnung): void {
    if (!this.darfAendern()) return;
    this.zuordnungErfolg.set(null);
    this.zuordnungMeldung.set(null);
    this.beendenZiel.set({ benutzer, zuordnung });
  }

  beendenAbbrechen(): void {
    if (this.beendenLaedt()) return;
    this.beendenZiel.set(null);
  }

  beendenBestaetigen(): void {
    const ziel = this.beendenZiel();
    if (!ziel || this.beendenLaedt()) return;
    this.beendenLaedt.set(true);
    this.svc.endUserRole(ziel.zuordnung.id).subscribe({
      next: () => {
        this.beendenLaedt.set(false);
        this.beendenZiel.set(null);
        this.zuordnungErfolg.set(
          `Rolle „${ziel.zuordnung.role_label}“ von ${ziel.benutzer.display_name} wurde beendet.`,
        );
        this.ladeZuordnungen();
      },
      error: (err: unknown) => {
        this.beendenLaedt.set(false);
        this.beendenZiel.set(null);
        // Der Server erklärt die Härtung im Klartext (z. B. letzte aktive
        // ADMINISTRATION) — wörtlich anzeigen.
        this.zuordnungMeldung.set(
          fehlerDetail(err) ?? 'Die Rollenzuordnung konnte nicht beendet werden.',
        );
      },
    });
  }

  protected beendenText(): string {
    const ziel = this.beendenZiel();
    if (!ziel) return '';
    return (
      `Die Rolle „${ziel.zuordnung.role_label}“ von ${ziel.benutzer.display_name} wird ` +
      `mit sofortiger Wirkung beendet. Die Zuordnung wird nicht gelöscht, sondern ` +
      `historisch abgeschlossen — rückgängig machen lässt sich das nicht.`
    );
  }

  // Benutzer anlegen ---------------------------------------------------------

  starteAnlegen(): void {
    if (!this.darfAnlegen()) return;
    this.zuordnungErfolg.set(null);
    this.zuordnungMeldung.set(null);
    this.anlegenMeldung.set(null);
    this.anlegenForm.reset({ display_name: '', email: '', password: '' });
    this.anlegenOffen.set(true);
  }

  anlegenSchliessen(): void {
    if (this.anlegenLaedt()) return;
    this.anlegenOffen.set(false);
    this.anlegenMeldung.set(null);
  }

  anlegenAbsenden(): void {
    if (this.anlegenLaedt()) return;
    this.anlegenMeldung.set(null);
    this.anlegenForm.markAllAsTouched();
    if (this.anlegenForm.invalid) return;
    const v = this.anlegenForm.getRawValue();
    this.anlegenLaedt.set(true);
    this.svc
      .createUser({
        display_name: v.display_name.trim(),
        email: v.email.trim(),
        password: v.password,
      })
      .subscribe({
        next: (u) => {
          this.anlegenLaedt.set(false);
          this.anlegenOffen.set(false);
          this.zuordnungErfolg.set(
            `Benutzer „${u.display_name}“ wurde angelegt. Er hat noch keine Rolle — ` +
              `weisen Sie ihm jetzt eine zu.`,
          );
          this.ladeZuordnungen();
        },
        error: (err: unknown) => {
          this.anlegenLaedt.set(false);
          // Der Server erklärt im Klartext, was fehlt (Adresse doppelt, Passwort
          // zu schwach) — wörtlich anzeigen.
          this.anlegenMeldung.set(
            fehlerDetail(err) ?? 'Der Benutzer konnte nicht angelegt werden.',
          );
        },
      });
  }

  // Benutzer sperren/freigeben -----------------------------------------------

  starteSperren(b: BenutzerZeile): void {
    if (!this.darfAendern()) return;
    this.zuordnungErfolg.set(null);
    this.zuordnungMeldung.set(null);
    this.sperrenZiel.set(b);
  }

  sperrenAbbrechen(): void {
    if (this.sperrenLaedt()) return;
    this.sperrenZiel.set(null);
  }

  sperrenBestaetigen(): void {
    const ziel = this.sperrenZiel();
    if (!ziel || this.sperrenLaedt()) return;
    const neu = ziel.status === 'ACTIVE' ? 'DISABLED' : 'ACTIVE';
    this.sperrenLaedt.set(true);
    this.svc.setUserStatus(ziel.id, neu).subscribe({
      next: (u) => {
        this.sperrenLaedt.set(false);
        this.sperrenZiel.set(null);
        this.zuordnungErfolg.set(
          neu === 'DISABLED'
            ? `${u.display_name} kann sich nicht mehr anmelden.`
            : `${u.display_name} kann sich wieder anmelden.`,
        );
        this.ladeZuordnungen();
      },
      error: (err: unknown) => {
        this.sperrenLaedt.set(false);
        this.sperrenZiel.set(null);
        this.zuordnungMeldung.set(
          fehlerDetail(err) ?? 'Der Status konnte nicht geändert werden.',
        );
      },
    });
  }

  protected sperrenText(): string {
    const ziel = this.sperrenZiel();
    if (!ziel) return '';
    if (ziel.status === 'ACTIVE') {
      return (
        `${ziel.display_name} kann sich danach nicht mehr anmelden. Das Konto wird ` +
        `NICHT gelöscht — es bleibt als Urheber vergangener Vorgänge sichtbar und ` +
        `lässt sich jederzeit wieder freigeben.`
      );
    }
    return `${ziel.display_name} kann sich danach wieder anmelden.`;
  }
}
