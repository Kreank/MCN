import { HttpErrorResponse } from '@angular/common/http';
import { FormControl, FormGroup } from '@angular/forms';
import { apiFehlerZuweisen } from './api-fehler';

function form() {
  return new FormGroup({
    title: new FormControl(''),
    description: new FormControl(''),
  });
}

describe('apiFehlerZuweisen', () => {
  it('verteilt Pydantic-422 auf die passenden Felder', () => {
    const f = form();
    const err = new HttpErrorResponse({
      status: 422,
      error: {
        detail: [{ loc: ['body', 'payload', 'title'], msg: 'Titel ist erforderlich' }],
      },
    });

    const res = apiFehlerZuweisen(err, f);

    expect(res.formular).toBeNull();
    expect(f.controls.title.errors).toEqual({ server: 'Titel ist erforderlich' });
    expect(f.controls.title.touched).toBe(true);
  });

  it('sammelt nicht zuordenbare Pydantic-Meldungen in die Formularmeldung', () => {
    const f = form();
    const err = new HttpErrorResponse({
      status: 422,
      error: { detail: [{ loc: ['body', 'payload', 'unbekannt'], msg: 'Feld fehlt' }] },
    });

    const res = apiFehlerZuweisen(err, f);

    expect(res.formular).toBe('Feld fehlt');
    expect(f.controls.title.errors).toBeNull();
  });

  it('nimmt Freitext-422 als Formularmeldung', () => {
    const f = form();
    const err = new HttpErrorResponse({
      status: 422,
      error: { detail: 'Statuswechsel nicht erlaubt.' },
    });

    expect(apiFehlerZuweisen(err, f).formular).toBe('Statuswechsel nicht erlaubt.');
  });

  it('meldet 403 als Berechtigungsfehler (Servermeldung bevorzugt)', () => {
    const f = form();
    const err = new HttpErrorResponse({
      status: 403,
      error: { detail: 'Keine Berechtigung: ANLEGEN im Modul workflow.' },
    });

    expect(apiFehlerZuweisen(err, f).formular).toBe(
      'Keine Berechtigung: ANLEGEN im Modul workflow.',
    );
  });

  it('gibt bei Netzwerkfehler (Status 0) eine generische Meldung', () => {
    const f = form();
    const err = new HttpErrorResponse({ status: 0, error: null });
    expect(apiFehlerZuweisen(err, f).formular).toContain('Keine Verbindung');
  });
});
