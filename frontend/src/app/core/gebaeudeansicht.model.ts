/**
 * Die Liegenschaft als **Haus**: Gebäude → Etagen → Einheiten, mit Bewohnern
 * und Technik (`GET /api/property/properties/{id}/gebaeudeansicht`).
 *
 * Ein Aufruf für ein Bild, das sonst über die Reiter Struktur, Belegung und
 * Anlagen verteilt ist. Die Daten kommen aus genau denselben Tabellen wie dort
 * — hier entsteht keine zweite Wahrheit, nur eine andere Darstellung.
 */
import { AssetType, EnergySource, SupplyType } from './anlage.model';

export interface HausAnlage {
  readonly id: string;
  readonly name: string;
  readonly asset_type: AssetType;
  /** Die Angabe, die den Einsatz verändert: zentral oder Etagengerät. */
  readonly supply_type: SupplyType;
  readonly energy_source: EnergySource | null;
  /** `null` = unbekannt, NIE 0 kW. */
  readonly power_kw: string | number | null;
  readonly location_note: string | null;
}

export interface HausBewohner {
  readonly party_id: string;
  readonly display_name: string;
  readonly rolle: string;
  readonly telefon: string | null;
  readonly email: string | null;
}

export interface HausEinheit {
  readonly id: string;
  readonly unit_number: string;
  readonly unit_type: string;
  /**
   * Gemeinschaftsflächen und Technikräume tragen keine Belegung. Ohne diese
   * Angabe sähe der Technikraum aus wie eine leerstehende Wohnung.
   */
  readonly belegbar: boolean;
  readonly belegt: boolean;
  /**
   * Der Etagentext, wie er an der Einheit steht („EG links"). Das Band trägt
   * nur die Etage — hier steht der vollständige erfasste Eintrag.
   */
  readonly etage_text: string | null;
  /** Abgeleitete Lage in der Etage: „links" | „Mitte" | „rechts". */
  readonly lage: string | null;
  readonly bewohner: readonly HausBewohner[];
  readonly anlagen: readonly HausAnlage[];
}

export interface HausEtage {
  /** Die Etage wie erfasst („2. OG") — ohne den abgespaltenen Lagezusatz. */
  readonly label: string;
  /** Abgeleitete Höhe; `null` = nicht deutbar, eigenes Band ganz unten. */
  readonly ordnung: number | null;
  readonly gedeutet: boolean;
  /**
   * Mindestens eine Einheit verdankt ihre Etage der **Nummer**, nicht dem Feld
   * „Etage". Die Ansicht sagt das, statt es als erfasst auszugeben.
   */
  readonly abgeleitet: boolean;
  /** Alle Schreibweisen im Band. Mehr als eine heißt: uneinheitlich erfasst. */
  readonly schreibweisen: readonly string[];
  readonly einheiten: readonly HausEinheit[];
}

export interface Haus {
  readonly id: string;
  readonly building_number: string;
  readonly name: string | null;
  readonly etagen: readonly HausEtage[];
  /** Technik am Gebäude ohne Einheit — die Zentralanlage im Keller. */
  readonly technik: readonly HausAnlage[];
  readonly einheiten_gesamt: number;
  readonly einheiten_belegt: number;
}

export interface Gebaeudeansicht {
  readonly haeuser: readonly Haus[];
  readonly anlagen_ohne_gebaeude: readonly HausAnlage[];
  /**
   * `false` heißt „darf die Belegung nicht sehen", **nicht** „alles leer".
   * Ohne dieses Flag wäre ein Haus voller „frei"-Kacheln eine Lüge.
   */
  readonly belegung_sichtbar: boolean;
}

/** Anzeigename eines Hauses — „Gebäude 2", wenn niemand einen Namen vergab. */
export function hausName(h: Haus): string {
  return h.name?.trim() || `Gebäude ${h.building_number}`;
}
