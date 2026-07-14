// Vertrag zu /api/property/component-templates (property.component_template,
// Migration 0090) — der **Bauteilkatalog**.
//
// Statt an jeder Wand einen U-Wert zu tippen, wird ein Bauteil gewählt
// („Fenster, Doppelkastenfenster", „Außenwand, Ziegel ungedämmt"); der U-Wert
// kommt mit.
//
// ZWEI DINGE, die dieses Modell trägt und die das UI aussprechen MUSS:
//
// 1. **Der Katalog kommt OHNE U-Werte.** Es gibt bewusst keine mitgelieferten
//    Normtabellen (dieselbe Haltung wie bei der Auslegungstemperatur): der
//    Betrieb trägt den Wert einmal ein — Herstellerangabe oder Erfahrungswert.
//    Eine Vorlage ohne Wert ist deshalb KEIN Fehler, sondern der
//    Auslieferungszustand. Sie muss aber sichtbar als „U-Wert noch nicht
//    hinterlegt" markiert sein, sonst wundert sich der Nutzer, warum die
//    Heizlast unbekannt bleibt.
//
// 2. **Die Vorlage ist eine KOPIERQUELLE, kein Verweis.** Der U-Wert wird beim
//    Erfassen in das Aufmaß kopiert; der Heizlast-Rechner liest nie den Katalog.
//    Eine spätere Katalogkorrektur ändert damit **kein bestehendes Aufmaß**.
//    (Dieselbe Regel wie beim Artikel in der Belegposition.)
//
// Gelöscht wird nie (die Datenbank verbietet es) — stillgelegt wird über
// `status = 'INAKTIV'`.

import {
  Dezimal,
  OpeningType,
  SurfaceType,
  openingTypeLabel,
  surfaceTypeLabel,
} from './raum.model';

/** Gattung einer Vorlage: Hüllfläche (Wand/Decke/Boden) oder Öffnung (Fenster/Tür). */
export type BauteilGattung = 'FLAECHE' | 'OEFFNUNG';

export type BauteilStatus = 'AKTIV' | 'INAKTIV';

export interface Bauteil {
  id: string;
  kind: BauteilGattung;
  name: string;
  /** Vorbelegung der Bauteilart — nur bei `kind = 'FLAECHE'` gesetzt. */
  default_surface_type: SurfaceType | null;
  /** Vorbelegung der Öffnungsart — nur bei `kind = 'OEFFNUNG'` gesetzt. */
  default_opening_type: OpeningType | null;
  /** `null` = im Katalog noch nicht hinterlegt. Kein Fehler, aber sichtbar zu machen. */
  u_value: Dezimal | null;
  note: string | null;
  status: BauteilStatus;
  sort_index: number;
}

/** POST /component-templates. */
export interface BauteilIn {
  kind: BauteilGattung;
  name: string;
  default_surface_type?: SurfaceType | null;
  default_opening_type?: OpeningType | null;
  u_value?: Dezimal | null;
  note?: string | null;
  sort_index?: number;
}

/** PATCH /component-templates/{id} — nur gesetzte Felder; `status` legt still/reaktiviert. */
export type BauteilPatch = Partial<BauteilIn> & { status?: BauteilStatus };

// --- Anzeige ---------------------------------------------------------------

const GATTUNG_LABELS: Record<BauteilGattung, string> = {
  FLAECHE: 'Hüllfläche',
  OEFFNUNG: 'Öffnung',
};

export const BAUTEIL_GATTUNGEN = Object.keys(GATTUNG_LABELS) as BauteilGattung[];

export function gattungLabel(k: BauteilGattung): string {
  return GATTUNG_LABELS[k] ?? k;
}

/** Hat die Vorlage (noch) keinen U-Wert? Auslieferungszustand — kein Fehler. */
export function ohneUWert(b: Bauteil): boolean {
  return b.u_value == null || b.u_value === '';
}

/** Die Art, die die Vorlage vorbelegt („Außenwand", „Fenster") — oder „—". */
export function bauteilArtLabel(b: Bauteil): string {
  if (b.default_surface_type) return surfaceTypeLabel(b.default_surface_type);
  if (b.default_opening_type) return openingTypeLabel(b.default_opening_type);
  return '—';
}

/**
 * Der eine Satz, der im Katalog-UI stehen MUSS — sonst hält jemand das
 * Nicht-Nachziehen alter Aufmaße für einen Bug.
 */
export const BAUTEIL_KOPIE_HINWEIS =
  'Der U-Wert wird beim Erfassen in das Aufmaß kopiert, nicht verlinkt: eine spätere ' +
  'Änderung hier ändert bestehende Aufmaße NICHT (sie bleiben der Nachweis über den ' +
  'Bestand zum Zeitpunkt der Aufnahme).';

/** Warum der Katalog leer an U-Werten ausgeliefert wird. */
export const BAUTEIL_OHNE_UWERT_HINWEIS =
  'Der Katalog wird ohne U-Werte ausgeliefert — es gibt bewusst keine mitgelieferten ' +
  'Normtabellen. Der Betrieb trägt den Wert einmal ein (Herstellerangabe oder ' +
  'Erfahrungswert). Bis dahin bleibt die Heizlast jeder Fläche mit dieser Vorlage unbekannt.';
