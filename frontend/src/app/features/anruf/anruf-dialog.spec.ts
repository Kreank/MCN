import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { AuthService } from '../../core/auth.service';
import { AnrufDialog } from './anruf-dialog';

/**
 * „Anruf annehmen" — die Maske, die der Disponent am Telefon bedient.
 *
 * Sascha beim Praxistest (2026-08-03): *„Kunde kann ich suchen und auswählen.
 * Objekt nicht. Die Suche erscheint, aber wenn ich das Objekt anklicke,
 * passiert nichts und mein Kontakt verschwindet. Wenn ich Objektart ändere,
 * dann verschwinden alle Angaben! Absolutes No-Go!"*
 *
 * **Eine Ursache, zwei Symptome.** Der Effekt, der das Formular beim ÖFFNEN
 * zurücksetzt, rief `pflichtfelderSynchronisieren()`. Das liest `bereichNoetig()`
 * — ein `computed` über Objektwahl, Objektart, Notfall und Vorlege-Weg. Damit
 * wurde dieses computed zur **Abhängigkeit des Effekts**: Sobald die Frage
 * „Verantwortungsbereich nötig?" ihre Antwort wechselte, lief der Effekt erneut
 * und `form.reset()` löschte alles Eingetippte.
 *
 * Beide gemeldeten Wege kippen genau diese Antwort von `false` auf `true`:
 * ein bestehendes Objekt wählen, und die Objektart von „Einfamilienhaus"
 * wegstellen. Deshalb prüft dieses Modul beide — und zusätzlich, dass der
 * Effekt seine eigentliche Aufgabe (Zurücksetzen beim Öffnen) behalten hat.
 */
describe('Anruf annehmen — Eingaben überleben die Bedienung', () => {
  let fixture: ComponentFixture<AnrufDialog>;
  let http: HttpTestingController;

  const form = () => (fixture.componentInstance as unknown as {
    form: {
      controls: Record<string, { value: unknown; setValue: (v: unknown) => void }>;
      getRawValue: () => Record<string, unknown>;
    };
  }).form;

  /**
   * Stammdaten-Abrufe abräumen. Sie sind Beiwerk (Gewerke, Mitarbeiter) — ohne
   * Antwort bliebe der Test an ihnen hängen, obwohl er nichts über sie aussagt.
   */
  const stammdatenAbraeumen = () => {
    http.match(() => true).forEach((r) => {
      if (r.cancelled) return;
      r.flush([]);
    });
    fixture.detectChanges();
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AnrufDialog],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: AuthService, useValue: { darf: () => true, darfAlle: () => true } },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(AnrufDialog);
    http = TestBed.inject(HttpTestingController);
    fixture.componentRef.setInput('offen', true);
    fixture.componentRef.setInput('startDatum', '2026-08-03');
    fixture.componentRef.setInput('startZeit', '14:00');
    fixture.detectChanges();
    stammdatenAbraeumen();
  });

  it('behält den gewählten Kunden, wenn danach ein Objekt gewählt wird', () => {
    form().controls['existing_party_id'].setValue('party-1');
    fixture.detectChanges();
    expect(form().controls['existing_party_id'].value).toBe('party-1');

    // Der gemeldete Fehler: Diese Zeile räumte den Kunden gleich mit ab, weil
    // `bereichNoetig` dabei von false auf true kippt.
    form().controls['existing_property_id'].setValue('prop-1');
    fixture.detectChanges();

    expect(form().controls['existing_party_id'].value).toBe('party-1');
    expect(form().controls['existing_property_id'].value).toBe('prop-1');
  });

  it('behält alle Angaben, wenn die Objektart gewechselt wird', () => {
    form().controls['existing_party_id'].setValue('party-1');
    form().controls['title'].setValue('Therme heizt nicht');
    form().controls['street'].setValue('Musterweg');
    form().controls['postal_code'].setValue('12345');
    form().controls['city'].setValue('Musterstadt');
    fixture.detectChanges();

    // Weg vom Einfamilienhaus — auch das kippt `bereichNoetig` auf true.
    form().controls['property_type'].setValue('WEG');
    fixture.detectChanges();

    expect(form().controls['property_type'].value).toBe('WEG');
    expect(form().controls['existing_party_id'].value).toBe('party-1');
    expect(form().controls['title'].value).toBe('Therme heizt nicht');
    expect(form().controls['street'].value).toBe('Musterweg');
    expect(form().controls['postal_code'].value).toBe('12345');
    expect(form().controls['city'].value).toBe('Musterstadt');
  });

  it('behält die Angaben auch beim Notfall-Schalter', () => {
    // Dritter Weg in dasselbe computed — und er zählt nur, wenn die Frage
    // vorher WIRKLICH offen war: Beim Einfamilienhaus ist sie es nie, ein
    // Notfall-Haken änderte dort gar nichts. Also erst auf WEG stellen
    // (Bereichsfrage an), dann den Notfall setzen (Bereichsfrage aus) — das
    // ist die Umschaltung, die den Effekt früher erneut laufen ließ.
    form().controls['property_type'].setValue('WEG');
    form().controls['title'].setValue('Wasserrohrbruch');
    fixture.detectChanges();

    form().controls['is_emergency'].setValue(true);
    fixture.detectChanges();

    expect(form().controls['is_emergency'].value).toBe(true);
    expect(form().controls['title'].value).toBe('Wasserrohrbruch');
    expect(form().controls['property_type'].value).toBe('WEG');
  });

  it('setzt beim ERNEUTEN Öffnen zurück — die eigentliche Aufgabe des Effekts', () => {
    form().controls['title'].setValue('Alter Anruf');
    form().controls['existing_party_id'].setValue('party-1');
    fixture.detectChanges();

    // Zu und wieder auf: Der nächste Anrufer fängt bei null an. Ohne diesen
    // Nachweis hätte `untracked` den Effekt auch stillegen können.
    fixture.componentRef.setInput('offen', false);
    fixture.detectChanges();
    fixture.componentRef.setInput('offen', true);
    fixture.detectChanges();
    stammdatenAbraeumen();

    expect(form().controls['title'].value).toBe('');
    expect(form().controls['existing_party_id'].value).toBe('');
    // Die Vorbelegung aus dem angeklickten Slot steht wieder.
    expect(form().controls['start_datum'].value).toBe('2026-08-03');
    expect(form().controls['start_zeit'].value).toBe('14:00');
  });
});
