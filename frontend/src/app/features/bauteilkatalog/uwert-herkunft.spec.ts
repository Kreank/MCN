import { Bauteil } from '../../core/bauteilkatalog.model';
import { uHerkunft, vorlagenWert } from './uwert-herkunft';

/**
 * Der Katalogwert wird KOPIERT, nicht verlinkt — und er ist überschreibbar. Der
 * Bediener muss deshalb jederzeit sehen, ob der U-Wert im Feld *aus der Vorlage*
 * stammt oder *abweichend eingegeben* wurde. Und eine Vorlage ohne U-Wert ist
 * kein Fehler, sondern der Auslieferungszustand des Katalogs — sie muss aber als
 * solche erkannt werden, sonst bleibt die Heizlast unbemerkt unbekannt.
 */
const vorlage = (u: string | null): Bauteil => ({
  id: 't-1',
  kind: 'FLAECHE',
  name: 'Außenwand, Ziegel ungedämmt',
  default_surface_type: 'AUSSENWAND',
  default_opening_type: null,
  u_value: u,
  note: null,
  status: 'AKTIV',
  sort_index: 1,
});

describe('uHerkunft', () => {
  it('ohne Vorlage: Handeingabe, keine Herkunft', () => {
    expect(uHerkunft(null, '0,24').art).toBe('ohne-vorlage');
    expect(uHerkunft(null, '').art).toBe('ohne-vorlage');
  });

  it('Wert = Katalogwert → „aus Vorlage" (auch bei anderer Schreibweise)', () => {
    expect(uHerkunft(vorlage('0.240'), '0,24')).toEqual({ art: 'aus-vorlage', vorlage: '0,24' });
    expect(uHerkunft(vorlage('0.240'), '0,240')).toEqual({ art: 'aus-vorlage', vorlage: '0,24' });
  });

  it('überschriebener Wert → „abweichend", mit dem Katalogwert daneben', () => {
    expect(uHerkunft(vorlage('0.240'), '0,9')).toEqual({ art: 'abweichend', vorlage: '0,24' });
  });

  it('unlesbare/mehrdeutige Eingabe ist nie „aus Vorlage"', () => {
    expect(uHerkunft(vorlage('0.240'), '1.500').art).toBe('abweichend');
  });

  it('Vorlage mit Wert, Feld leer → „fehlt" (Heizlast bleibt unbekannt)', () => {
    expect(uHerkunft(vorlage('0.240'), '')).toEqual({ art: 'fehlt', vorlage: '0,24' });
  });

  it('Vorlage OHNE U-Wert, Feld leer → Auslieferungszustand, kein Fehler', () => {
    expect(uHerkunft(vorlage(null), '').art).toBe('vorlage-ohne-wert');
  });

  it('Vorlage OHNE U-Wert, Wert von Hand → „eigener Wert"', () => {
    expect(uHerkunft(vorlage(null), '2,7').art).toBe('eigener-wert');
  });
});

describe('vorlagenWert', () => {
  it('zeigt den Katalogwert deutsch und ohne Nachkomma-Nullen', () => {
    expect(vorlagenWert(vorlage('2.700'))).toBe('2,7');
    expect(vorlagenWert(vorlage('0.240'))).toBe('0,24');
  });

  it('ohne Wert bleibt null — nie 0', () => {
    expect(vorlagenWert(vorlage(null))).toBeNull();
    expect(vorlagenWert(vorlage(''))).toBeNull();
  });
});
