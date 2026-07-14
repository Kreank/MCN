import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { AuthService } from './auth.service';
import { Me } from './auth.model';
import { BEREICH_NUR_ALLE, nurAlleFuerPfad, rechtFuerPfad } from './bereiche';

/**
 * Die Rechtematrix des MONTEURs — genau so, wie `/api/auth/me` sie in der
 * Dev-Datenbank ausliefert (Migrationen 0068/0102 und die Objektsicht).
 *
 * **Der Punkt dieser Datei:** Der Server ist an den meisten Endpunkten
 * *fail-closed* (`permissions.require`): ein Konto mit row_scope `EIGENE` bekommt
 * dort 403, obwohl es das Recht trägt. `AuthService.darf` reicht deshalb NICHT,
 * um ein Bedienelement zu zeigen — es muss `darfAlle` sein, sobald der Endpunkt
 * dahinter den Scope nicht auswertet. Diese Tests halten den Unterschied fest,
 * damit niemand die beiden Funktionen wieder verwechselt.
 */
const MONTEUR: Me = {
  id: 3,
  email: 'timo.kalinski@mitra-sanitaer.de',
  display_name: 'Timo Kalinski',
  app_user_id: '00000000-0000-4000-8000-000000000104',
  is_staff: false,
  roles: ['MONTEUR'],
  permissions: [
    { module: 'company', action: 'LESEN', row_scope: 'ALLE' },
    { module: 'content', action: 'ANLEGEN', row_scope: 'EIGENE' },
    { module: 'content', action: 'LESEN', row_scope: 'EIGENE' },
    { module: 'hr', action: 'AENDERN', row_scope: 'EIGENE' },
    { module: 'hr', action: 'LESEN', row_scope: 'EIGENE' },
    { module: 'identity', action: 'LESEN', row_scope: 'EIGENE' },
    { module: 'invoicing', action: 'LESEN', row_scope: 'EIGENE' },
    { module: 'maintenance', action: 'LESEN', row_scope: 'EIGENE' },
    { module: 'management', action: 'LESEN', row_scope: 'EIGENE' },
    { module: 'property', action: 'AENDERN', row_scope: 'EIGENE' },
    { module: 'property', action: 'ANLEGEN', row_scope: 'EIGENE' },
    { module: 'property', action: 'LESEN', row_scope: 'EIGENE' },
    { module: 'tenure', action: 'LESEN', row_scope: 'EIGENE' },
    { module: 'workflow', action: 'AENDERN', row_scope: 'EIGENE' },
    { module: 'workflow', action: 'ANLEGEN', row_scope: 'EIGENE' },
    { module: 'workflow', action: 'LESEN', row_scope: 'EIGENE' },
  ],
} as Me;

describe('Rechte-Tore des MONTEURs', () => {
  let auth: AuthService;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient()] });
    auth = TestBed.inject(AuthService);
    auth.user.set(MONTEUR);
  });

  it('trägt die Rechte — aber nur mit row_scope EIGENE', () => {
    for (const [modul, aktion] of [
      ['workflow', 'ANLEGEN'],
      ['workflow', 'AENDERN'],
      ['property', 'ANLEGEN'],
      ['property', 'AENDERN'],
      ['invoicing', 'LESEN'],
    ] as const) {
      expect(auth.darf(modul, aktion)).toBe(true);
      expect(auth.darfAlle(modul, aktion)).toBe(false);
    }
  });

  it('hat pricing, accounting und security überhaupt nicht', () => {
    expect(auth.darf('pricing', 'LESEN')).toBe(false);
    expect(auth.darf('accounting', 'LESEN')).toBe(false);
    expect(auth.darf('security', 'LESEN')).toBe(false);
    expect(auth.darf('invoicing', 'ANLEGEN')).toBe(false);
    expect(auth.darf('workflow', 'FREIGEBEN')).toBe(false);
  });

  it('bekommt die Monteurs-Startseite (workflow-Scope EIGENE)', () => {
    const monteurSicht = auth.darf('workflow', 'LESEN') && !auth.darfAlle('workflow', 'LESEN');
    expect(monteurSicht).toBe(true);
  });

  it('sieht Buchhaltung, Auswertungen, Rechnungen, Mitarbeiter und Zeiterfassung nicht', () => {
    for (const bereich of ['buchhaltung', 'auswertungen', 'rechnungen', 'mitarbeiter', 'zeiterfassung']) {
      expect(BEREICH_NUR_ALLE.has(bereich)).toBe(true);
      const recht = rechtFuerPfad(`/${bereich}`)!;
      expect(nurAlleFuerPfad(`/${bereich}`)).toBe(true);
      // Genau das Tor, an dem der Server ihn abweist.
      expect(auth.darfAlle(recht[0], recht[1])).toBe(false);
    }
  });

  it('sieht die Bereiche, die den Scope auswerten (require_scoped)', () => {
    for (const bereich of ['liegenschaften', 'projekte', 'planung', 'aufgaben', 'wartung', 'kontakte', 'dokumente']) {
      const recht = rechtFuerPfad(`/${bereich}`)!;
      expect(nurAlleFuerPfad(`/${bereich}`)).toBe(false);
      expect(auth.darf(recht[0], recht[1])).toBe(true);
    }
  });
});
