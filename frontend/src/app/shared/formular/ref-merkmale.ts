import { Party } from '../../core/party.model';
import { Property } from '../../core/property.model';
import { RefMerkmal, RefOption } from './referenz-wahl';

/**
 * Domänenobjekte → Auswahloptionen mit Entscheidungsmerkmalen.
 *
 * Zentral, weil dieselbe Abbildung an mehreren Stellen gebraucht wird: in der
 * Picker-Suche der Schnellerfassung UND beim „Übernehmen" aus der
 * Dublettenwarnung (dort entsteht die Auswahl ohne Suchlauf). Zwei Kopien
 * liefen unweigerlich auseinander — der Chip zeigte dann andere Merkmale als
 * die Trefferzeile, aus der er entstand.
 *
 * Regel überall: LEERE Werte fallen raus. Ein „—" ist keine Information, es
 * kostet nur Zeile und Aufmerksamkeit.
 */

function merkmal(label: string, wert: string | null | undefined): RefMerkmal | null {
  const w = wert?.trim();
  return w ? { label, wert: w } : null;
}

function gefiltert(liste: (RefMerkmal | null)[]): RefMerkmal[] {
  return liste.filter((m): m is RefMerkmal => m !== null);
}

/** Telefonnummer samt Herkunft — „030 12 (Verwaltung Stegos GmbH)". Die Quelle
 *  entscheidet mit: Eine Verwaltungsnummer ruft man anders an als den Mieter. */
export function telefonMitQuelle(p: Property): string | null {
  const t = p.telefon?.trim();
  if (!t) return null;
  const q = p.telefon_quelle?.trim();
  return q ? `${t} (${q})` : t;
}

/** Einzeilige Objektadresse, sonst der Ort als Rückfall. */
export function propertyAdresse(p: Property): string {
  return p.address_line?.trim() || p.city;
}

export function propertyMerkmale(p: Property): RefMerkmal[] {
  const einheiten = p.einheiten_anzahl ?? 0;
  return gefiltert([
    merkmal('Eigentümer', (p.eigentuemer ?? []).join(', ')),
    merkmal('Verwaltung', p.verwaltung),
    merkmal('Telefon', telefonMitQuelle(p)),
    einheiten > 0
      ? { label: 'Einheiten', wert: `${einheiten}` }
      : null,
    merkmal('Gebäude', (p.gebaeude_adressen ?? []).join(' · ')),
  ]);
}

export function propertyRefOption(p: Property): RefOption {
  return {
    id: p.id,
    label: p.name,
    sub: `${p.property_number} · ${propertyAdresse(p)}`,
    merkmale: propertyMerkmale(p),
  };
}

export function partyMerkmale(x: Party): RefMerkmal[] {
  return gefiltert([
    merkmal('Telefon', x.telefon),
    merkmal('E-Mail', x.email),
    merkmal('Adresse', x.address_line),
    merkmal('Objekte', (x.objekte ?? []).join(' · ')),
  ]);
}

export function partyRefOption(x: Party): RefOption {
  return {
    id: x.id,
    label: x.display_name,
    merkmale: partyMerkmale(x),
  };
}
