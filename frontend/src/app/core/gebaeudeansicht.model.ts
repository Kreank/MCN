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
  readonly bewohner: readonly HausBewohner[];
  readonly anlagen: readonly HausAnlage[];
}

export interface HausEtage {
  /** Wortwörtlich der erfasste Text („2. OG") — nichts Vereinheitlichtes. */
  readonly label: string;
  /** Abgeleitete Höhe; `null` = nicht deutbar, eigenes Band ganz unten. */
  readonly ordnung: number | null;
  readonly gedeutet: boolean;
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
