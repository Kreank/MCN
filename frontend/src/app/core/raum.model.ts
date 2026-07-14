// Vertrag zu /api/property (property.room / room_surface / room_opening,
// Migration 0086). Das Raumaufmaß ist die Grundlage für Heizlast, Leitungslängen
// und später die 3D-Planung.
//
// ZWEI INVARIANTEN, die dieses Modell trägt:
//
// 1. **Dezimalzahlen sind Strings** (API-Punkt-Form, z. B. "12.500"). Niemals
//    `number` — die deutsche Eingabe wird über `shared/formular/dezimal.ts`
//    gewandelt, und eine mehrdeutige Eingabe („1.500") wird ABGELEHNT, nicht
//    geraten. Ganzzahlen (Stückzahlen, Zähler) sind `number`.
//
// 2. **Die Heizlast rechnet der Server.** Ist sie `null`, ist sie UNBEKANNT —
//    dann steht `unbekannt_grund` daneben (z. B. „U-Wert fehlt an: Außenwand
//    Nord"). Das UI zeigt „unbekannt" mit Grund, **niemals 0** und niemals eine
//    geratene Zahl. Der Client rechnet ausschließlich die triviale Geometrie
//    (Fläche = L × B, Nettofläche = brutto − Öffnungen) für Sofort-Feedback.

/** Dezimalwert in API-Form (Punkt als Trenner). Nie `number`. */
export type Dezimal = string;

export type RoomType =
  | 'WOHNEN'
  | 'SCHLAFEN'
  | 'KUECHE'
  | 'BAD'
  | 'WC'
  | 'FLUR'
  | 'TREPPENHAUS'
  | 'KELLER'
  | 'DACHBODEN'
  | 'TECHNIK'
  | 'BUERO'
  | 'LAGER'
  | 'GEWERBE'
  | 'SONSTIGES';

export type SurfaceType = 'AUSSENWAND' | 'INNENWAND' | 'DACHSCHRAEGE' | 'DECKE' | 'BODEN';

/** Nachbarbereich — er entscheidet über den Wärmeverlust, nicht die Bauteilart. */
export type Adjacent = 'AUSSENLUFT' | 'ERDREICH' | 'UNBEHEIZT' | 'BEHEIZT';

export type Orientation = 'N' | 'NO' | 'O' | 'SO' | 'S' | 'SW' | 'W' | 'NW';

export type OpeningType = 'FENSTER' | 'DACHFENSTER' | 'TUER_AUSSEN' | 'TUER_INNEN' | 'SONSTIGES';

/**
 * Ein Raum wird **nie gelöscht** — ein Aufmaß ist ein Nachweis über den Bestand.
 * Ein umgebauter oder weggefallener Raum (zwei Zimmer zusammengelegt) wird
 * stillgelegt: `INAKTIV`. Der Server nimmt ihn dann aus den Summen und liefert
 * ihn nur noch auf ausdrückliche Nachfrage (`?mit_inaktiven=true`).
 */
export type RoomStatus = 'AKTIV' | 'INAKTIV';

// --- Lesen -----------------------------------------------------------------

/**
 * Ein Punkt des Raumumrisses (Migration 0091). **Ganzzahlige Millimeter**, im
 * Koordinatensystem des GESCHOSSES — nicht des Raumes. Damit liegen alle Räume
 * einer Etage im selben Raster, und die Etagenübersicht ergibt sich von selbst.
 *
 * Kein Gleitkomma: sonst lägen die Kanten zweier Nachbarräume „fast"
 * aufeinander. `idx` ist die Reihenfolge des Umlaufs; **Kante `i` = Strecke von
 * Punkt `i` nach Punkt `i+1`**, zyklisch (der letzte schließt auf den ersten).
 */
export interface Vertex {
  idx: number;
  x_mm: number;
  y_mm: number;
}

export interface Surface {
  id: string;
  surface_type: SurfaceType;
  adjacent: Adjacent;
  orientation: Orientation | null;
  label: string | null;
  gross_area_m2: Dezimal;
  u_value: Dezimal | null;
  temp_factor: Dezimal | null;
  /**
   * Ist `gross_area_m2` **abgeleitet** (Kantenlänge × Raumhöhe) oder Handeingabe?
   * (Migration 0093.)
   *
   * `true`: Der Server hat sie aus der Kante gerechnet und rechnet sie bei **jeder**
   * Änderung von Umriss oder Raumhöhe **neu**. Korrigiert jemand die Raumhöhe von
   * 2,50 auf 2,80 m, wandert die Wandfläche mit.
   *
   * `false`: Jemand hat sie ausdrücklich eingetragen (Giebel, Erker, Dachschräge).
   * Sie wird dann **nie** überschrieben — auch nicht, wenn die Raumhöhe sich ändert.
   *
   * Der Unterschied entsteht allein dadurch, **ob der Client `gross_area_m2`
   * mitschickt**: weglassen = ableiten lassen, senden = Handeingabe.
   */
  area_is_derived: boolean;
  /** Bruttofläche minus der Öffnungen darin — vom Server gerechnet. */
  net_area_m2: Dezimal | null;
  /**
   * Die Katalog-Vorlage, aus der erfasst wurde — **Herkunftsvermerk, kein
   * Verweis**: `u_value` oben ist eine KOPIE. Ändert jemand später den
   * Katalogwert, ändert sich diese Fläche nicht.
   */
  template_id?: string | null;
  /**
   * Die Polygonkante, auf der diese Wand steht (`vertex i → i+1`).
   * **null = keine Kante**: Decke, Boden und Dachschräge liegen über bzw. unter
   * dem Polygon — und eine von Hand angelegte Wand ohne Zeichnung hat auch keine.
   */
  edge_index: number | null;
  /** Länge dieser Kante (vom Server aus dem Umriss). null = keine Kante. */
  edge_length_m: Dezimal | null;
}

export interface Opening {
  id: string;
  /** Die Hüllfläche, in der die Öffnung sitzt. null = keiner Wand zugeordnet. */
  surface_id: string | null;
  opening_type: OpeningType;
  label: string | null;
  quantity: number;
  width_m: Dezimal;
  height_m: Dezimal;
  u_value: Dezimal | null;
  /** Anzahl × Breite × Höhe — GENERATED-Spalte der Datenbank. */
  area_m2: Dezimal | null;
  /** Herkunftsvermerk der Katalog-Vorlage (der U-Wert bleibt eine Kopie). */
  template_id?: string | null;
  /**
   * Abstand der linken Öffnungskante vom **Anfangspunkt** ihrer Kante.
   *
   * **null heißt UNBEKANNT, nicht „bei 0 m".** Ein Fenster darf erfasst sein,
   * ohne dass jemand seine Lage in der Wand ausgemessen hat: Es zählt ganz normal
   * in Fläche und Heizlast — es wird nur **nicht gezeichnet**. Es auf 0 zu setzen
   * wäre eine erfundene Angabe.
   */
  position_m: Dezimal | null;
}

/**
 * Kennzahlen eines Raumes — **alles vom Server gerechnet**.
 *
 * `heizlast_kennwert_w` (Fläche × Kennwert) und `heizlast_huellflaeche_w`
 * (Transmission + Lüftung) sind zwei getrennte Verfahren. Ist eines `null`,
 * fehlt eine Angabe — der Grund steht in `unbekannt_grund`. Ein `null` wird
 * NIE als 0 dargestellt.
 */
export interface Kennzahlen {
  /**
   * Woher Fläche und Umfang stammen. `GEZEICHNET`: Der Raum hat einen Umriss —
   * dann rechnet der **Server** `floor_area_m2` und `perimeter_m` aus dem Polygon
   * (Trapezformel bzw. Summe der Kantenlängen), und die Felder sind im Editor
   * nicht mehr frei tippbar. `EINGEGEBEN`: Handeingabe wie bisher.
   *
   * **Wer zeichnet, misst nicht doppelt** — es gibt keinen zweiten Satz Zahlen,
   * der auseinanderlaufen kann.
   */
  geometrie_quelle: 'GEZEICHNET' | 'EINGEGEBEN';
  floor_area_m2: Dezimal | null;
  volume_m3: Dezimal | null;
  perimeter_m: Dezimal | null;
  wall_area_gross_m2: Dezimal | null;
  opening_area_m2: Dezimal | null;
  wall_area_net_m2: Dezimal | null;
  heizlast_kennwert_w: Dezimal | null;
  transmission_w: Dezimal | null;
  lueftung_w: Dezimal | null;
  heizlast_huellflaeche_w: Dezimal | null;
  /** Warum die Hüllflächen-Heizlast unbekannt ist. null = sie ist bekannt. */
  unbekannt_grund: string | null;
  hinweise: string[];
}

export interface Room {
  id: string;
  property_id?: string;
  building_id: string | null;
  unit_id: string | null;
  storey: string | null;
  name: string;
  room_type: RoomType | null;
  floor_area_m2: Dezimal;
  length_m: Dezimal | null;
  width_m: Dezimal | null;
  room_height_m: Dezimal;
  perimeter_m: Dezimal | null;
  /** GENERATED: Fläche × Höhe. Nicht schreibbar. */
  volume_m3: Dezimal | null;
  indoor_temp_c: Dezimal | null;
  air_change_rate: Dezimal | null;
  heat_load_w_per_m2: Dezimal | null;
  riser_distance_m: Dezimal | null;
  status: RoomStatus | null;
  note: string | null;
  surfaces: Surface[];
  openings: Opening[];
  /**
   * Der Umriss, in der Reihenfolge des Umlaufs. **Leer = nicht gezeichnet** (der
   * Raum bleibt gültig, Fläche und Umfang sind dann Handeingabe).
   */
  vertices: Vertex[];
  kennzahlen: Kennzahlen;
}

/** Hat der Raum einen Umriss? Dann rechnet der Server Fläche und Umfang. */
export function istGezeichnet(r: { vertices?: Vertex[] | null }): boolean {
  return (r.vertices?.length ?? 0) >= 3;
}

/** Stillgelegt? Ein Raum ohne Status gilt als aktiv (der Server setzt AKTIV). */
export function istStillgelegt(r: { status: RoomStatus | null }): boolean {
  return r.status === 'INAKTIV';
}

// --- Schreiben --------------------------------------------------------------

/** POST /properties/{id}/rooms bzw. PATCH /rooms/{id} (dort sind alle Felder optional). */
export interface RoomIn {
  name: string;
  floor_area_m2: Dezimal;
  room_height_m: Dezimal;
  storey?: string | null;
  room_type?: RoomType | null;
  building_id?: string | null;
  unit_id?: string | null;
  length_m?: Dezimal | null;
  width_m?: Dezimal | null;
  perimeter_m?: Dezimal | null;
  indoor_temp_c?: Dezimal | null;
  air_change_rate?: Dezimal | null;
  heat_load_w_per_m2?: Dezimal | null;
  riser_distance_m?: Dezimal | null;
  /** `INAKTIV` legt den Raum still. Gelöscht wird nie. */
  status?: RoomStatus | null;
  note?: string | null;
}

export type RoomPatch = Partial<RoomIn>;

/**
 * Hüllfläche im `PUT /rooms/{id}/aufbau`. `ref` vergibt der **Client** — die
 * Öffnungen zeigen über `surface_ref` darauf. Der Aufbau wird immer als GANZES
 * geschickt (Flächen + Öffnungen in EINER Anfrage), sonst könnte eine Öffnung
 * kurzzeitig auf eine nicht existierende Wand zeigen.
 */
export interface SurfaceIn {
  ref: string;
  surface_type: SurfaceType;
  adjacent: Adjacent;
  orientation?: Orientation | null;
  label?: string | null;
  /**
   * **Weglassen heißt: der Server rechnet sie** (Kantenlänge × Raumhöhe) und hält
   * sie fortan aktuell — `area_is_derived = true`. Das geht nur mit `edge_index`.
   *
   * **Senden heißt: Handeingabe** — der Server rührt sie nie wieder an
   * (`area_is_derived = false`). Das ist für Giebel und Erker richtig und für eine
   * gewöhnliche Kantenwand **falsch**: Sie bliebe auf der alten Raumhöhe stehen,
   * während die Heizlast still falsch würde.
   *
   * Ohne `edge_index` (Decke, Boden, Dachschräge, Wand ohne Zeichnung) ist sie
   * **Pflicht** — es gibt keine Kante, aus der sich etwas ableiten ließe.
   */
  gross_area_m2?: Dezimal;
  u_value?: Dezimal | null;
  temp_factor?: Dezimal | null;
  /**
   * Die gewählte Katalog-Vorlage (`property.component_template`). Sie merkt nur
   * die **Herkunft**: der U-Wert oben ist eine Kopie und wird von einer späteren
   * Katalogänderung NICHT nachgezogen. Ein gemessener Wert schlägt die Vorlage.
   */
  template_id?: string | null;
  /**
   * Die Polygonkante, auf der diese Wand steht. Der Server prüft sie gegen die
   * Punktzahl des Umrisses und verhindert **zwei Wände auf derselben Kante**
   * (sonst zählte dieselbe Fläche doppelt in die Heizlast).
   *
   * Decke, Boden und Dachschräge bekommen **null** — sie liegen über bzw. unter
   * dem Polygon und haben keine Kante.
   */
  edge_index?: number | null;
}

export interface OpeningIn {
  /** Verweis auf `SurfaceIn.ref`. null = keiner Wand zugeordnet. */
  surface_ref?: string | null;
  opening_type: OpeningType;
  label?: string | null;
  quantity: number;
  width_m: Dezimal;
  height_m: Dezimal;
  u_value?: Dezimal | null;
  /** Katalog-Vorlage als Herkunftsvermerk — der U-Wert bleibt eine Kopie. */
  template_id?: string | null;
  /**
   * Abstand vom Anfangspunkt der Kante. **Weglassen bzw. `null` = die Lage ist
   * nicht ausgemessen** — die Öffnung zählt trotzdem in Fläche und Heizlast, sie
   * wird nur nicht gezeichnet. **Niemals 0 senden, wenn die Lage unbekannt ist.**
   */
  position_m?: Dezimal | null;
}

export interface AufbauIn {
  surfaces: SurfaceIn[];
  openings: OpeningIn[];
}

/**
 * `PUT /rooms/{id}/grundriss` — der Umriss als GANZES (die Reihenfolge ist der
 * Umlauf). **Leeres Array entfernt den Umriss**: Fläche und Umfang werden danach
 * wieder Handeingabe, und die `edge_index` der Wände fallen auf null.
 *
 * Der Server antwortet mit **422**, wenn der Umriss weniger als 3 Punkte hat,
 * ein Punkt doppelt vorkommt, das Polygon entartet ist (Fläche 0) oder es sich
 * selbst schneidet. Der Editor fängt all das vorher ab (`grundriss/geometrie.ts`).
 */
export interface GrundrissIn {
  vertices: { x_mm: number; y_mm: number }[];
}

// --- Auslegungsdaten des Objekts (Migration 0089) ---------------------------

/**
 * Die Rechenannahmen der Heizlast sind **Eigenschaften des Objekts**, keine
 * Parameter des Aufrufs: Auslegungs-Außentemperatur (Standort) und
 * Gebäudekennwert (W/m²). Sie stehen an `property` und werden über
 * `PATCH /properties/{id}/auslegung` gepflegt.
 *
 * **Ohne Außentemperatur gibt es keine raumweise Heizlast** — der Server meldet
 * sie dann als unbekannt. Es gibt bewusst KEINE mitgelieferten Normwerte
 * (keine DIN-Tabellen im Produkt); der Betrieb trägt sie ein.
 */
export interface Auslegung {
  design_outdoor_temp_c: Dezimal | null;
  heat_load_w_per_m2: Dezimal | null;
}

/** PATCH-Body: nicht gesendete Felder bleiben unverändert, `null` setzt zurück. */
export interface AuslegungIn {
  design_outdoor_temp_c?: Dezimal | null;
  heat_load_w_per_m2?: Dezimal | null;
}

/** GET /properties/{id}/aufmass — die Summe über alle Räume. */
export interface Aufmass extends Auslegung {
  raeume_anzahl: number;
  flaeche_m2: Dezimal | null;
  volumen_m3: Dezimal | null;
  umfang_m: Dezimal | null;
  heizlast_kennwert_w: Dezimal | null;
  heizlast_huellflaeche_w: Dezimal | null;
  /** Räume, deren Heizlast unbekannt ist — namentlich, nicht als Zahl 0. */
  unbekannt_raeume: string[];
  leitungslaenge_schaetzung_m: Dezimal | null;
  raeume_ohne_steigleitung: number;
  hinweise: string[];
}

// --- Deutsche Labels (Muster: site-report.model.ts) --------------------------

const ROOM_TYPE_LABELS: Record<RoomType, string> = {
  WOHNEN: 'Wohnen',
  SCHLAFEN: 'Schlafen',
  KUECHE: 'Küche',
  BAD: 'Bad',
  WC: 'WC',
  FLUR: 'Flur',
  TREPPENHAUS: 'Treppenhaus',
  KELLER: 'Keller',
  DACHBODEN: 'Dachboden',
  TECHNIK: 'Technik',
  BUERO: 'Büro',
  LAGER: 'Lager',
  GEWERBE: 'Gewerbe',
  SONSTIGES: 'Sonstiges',
};

const SURFACE_TYPE_LABELS: Record<SurfaceType, string> = {
  AUSSENWAND: 'Außenwand',
  INNENWAND: 'Innenwand',
  DACHSCHRAEGE: 'Dachschräge',
  DECKE: 'Decke',
  BODEN: 'Boden',
};

const ADJACENT_LABELS: Record<Adjacent, string> = {
  AUSSENLUFT: 'Außenluft',
  ERDREICH: 'Erdreich',
  UNBEHEIZT: 'unbeheizt',
  BEHEIZT: 'beheizt',
};

const ORIENTATION_LABELS: Record<Orientation, string> = {
  N: 'Nord',
  NO: 'Nordost',
  O: 'Ost',
  SO: 'Südost',
  S: 'Süd',
  SW: 'Südwest',
  W: 'West',
  NW: 'Nordwest',
};

const OPENING_TYPE_LABELS: Record<OpeningType, string> = {
  FENSTER: 'Fenster',
  DACHFENSTER: 'Dachfenster',
  TUER_AUSSEN: 'Außentür',
  TUER_INNEN: 'Innentür',
  SONSTIGES: 'Sonstiges',
};

export const ROOM_TYPES = Object.keys(ROOM_TYPE_LABELS) as RoomType[];
export const SURFACE_TYPES = Object.keys(SURFACE_TYPE_LABELS) as SurfaceType[];
export const ADJACENTS = Object.keys(ADJACENT_LABELS) as Adjacent[];
export const ORIENTATIONS = Object.keys(ORIENTATION_LABELS) as Orientation[];
export const OPENING_TYPES = Object.keys(OPENING_TYPE_LABELS) as OpeningType[];

export function roomTypeLabel(t: RoomType | null): string {
  return t ? (ROOM_TYPE_LABELS[t] ?? t) : 'ohne Nutzung';
}
export function surfaceTypeLabel(t: SurfaceType): string {
  return SURFACE_TYPE_LABELS[t] ?? t;
}
export function adjacentLabel(a: Adjacent): string {
  return ADJACENT_LABELS[a] ?? a;
}
export function orientationLabel(o: Orientation | null): string {
  return o ? (ORIENTATION_LABELS[o] ?? o) : '—';
}
export function openingTypeLabel(t: OpeningType): string {
  return OPENING_TYPE_LABELS[t] ?? t;
}

/**
 * Grenzt eine Hüllfläche gegen Kälte? Nur dann sind U-Wert und Temperaturfaktor
 * überhaupt nötig — reine Anzeige-Hilfe, die Rechnung macht der Server.
 */
export function istWaermeverlust(a: Adjacent): boolean {
  return a !== 'BEHEIZT';
}
