import { zahlAus } from './eingabe';

/**
 * Rechenkern der Werkzeuge — reine Funktionen, kein Angular, kein State.
 *
 * HERKUNFT: Heizlast, Einheiten-Umrechner und Volumenstrom sind aus der
 * bestehenden Windows-App des Anwenders (`NotizApp_Win`, C#/WPF, Controls/
 * `HeizlastRechner`, `EinheitenUmrechner`, `VolumenstromRechner`) **fachlich
 * 1:1 portiert**: gleiche Formeln, gleiche Konstanten, gleiche Kennwerte,
 * gleiche Rundung/Formatierung. Wer hier etwas ändert, ändert Zahlen, die der
 * Anwender seit Jahren so kennt — das ist kein Refactoring, das ist ein
 * fachlicher Eingriff.
 *
 * ABGRENZUNG ZU GELD: Diese Werkzeuge rechnen Überschlagswerte für die Anzeige.
 * Deshalb ist `number` hier zulässig. Es geht KEIN hier gerechneter Wert als
 * Betrag oder Menge in einen Beleg — die Übernahme in ein Angebot erzeugt eine
 * reine TEXTZEILE (Dokumentation der Annahme), keine bepreiste Position. Die
 * Invariante „Decimal bleibt String, der Server rechnet verbindlich" bleibt
 * damit unberührt.
 */

// ============================================================================
// Zahlformatierung — bildet die C#-Formatmuster der NotizApp exakt nach.
// (`0.##`, `#,##0`, `0.0`, `0.00`, `#,##0.####`) — `apiZuDeAnzeige` kann nur
// min == max Nachkommastellen und deckt „bis zu N Stellen" nicht ab.
// ============================================================================

function fmt(n: number, min: number, max: number, gruppieren: boolean): string {
  return new Intl.NumberFormat('de-DE', {
    minimumFractionDigits: min,
    maximumFractionDigits: max,
    useGrouping: gruppieren,
  }).format(n);
}

/** C# `"0.##"` — bis zu 2 Nachkommastellen, ohne Tausendertrennung. */
export const zahlKurz = (n: number): string => fmt(n, 0, 2, false);
/** C# `"0.####"` — bis zu 4 Nachkommastellen, ohne Tausendertrennung. */
export const zahlFein = (n: number): string => fmt(n, 0, 4, false);
/** C# `"#,##0"` — ganzzahlig mit Tausenderpunkt (Watt, l/h). */
export const zahlGanz = (n: number): string => fmt(n, 0, 0, true);
/** C# `"0.0"` — genau 1 Nachkommastelle (kW). */
export const zahlEine = (n: number): string => fmt(n, 1, 1, false);
/** C# `"0.00"` — genau 2 Nachkommastellen (m³/h). */
export const zahlZwei = (n: number): string => fmt(n, 2, 2, false);
/** C# `"#,##0.####"` — Tausenderpunkt, bis zu 4 Nachkommastellen (Umrechner). */
export const zahlListe = (n: number): string => fmt(n, 0, 4, true);
/** C# `"#,##0.#"` — Tausenderpunkt, bis zu 1 Nachkommastelle (Liter). */
export const zahlLiter = (n: number): string => fmt(n, 0, 1, true);
/** Aufmaß-Mengen: Tausenderpunkt, bis zu 3 Nachkommastellen (DB: numeric(15,3)). */
export const zahlMenge = (n: number): string => fmt(n, 0, 3, true);

// ============================================================================
// Haftung — steht im UI UND auf jeder Ausgabe (Kopieren, Angebotsposition).
// Ein Überschlagswert ist kein Nachweis. Formulierung bewusst hart.
// ============================================================================

/** Pflichthinweis für jede Heizlast-Ausgabe. */
export const HEIZLAST_HAFTUNG =
  'Überschlägiger Wert (Flächenverfahren). KEIN Nachweis nach DIN EN 12831, ' +
  'nicht förderfähig (BEG/KfW) und nicht für den hydraulischen Abgleich ' +
  '„Verfahren B" geeignet. Dafür ist die raumweise Normheizlast zu berechnen.';

/** Pflichthinweis für jede Heizkörper-Ausgabe. */
export const HEIZKOERPER_HAFTUNG =
  'Plausibilisierung, kein Auslegungsnachweis. Maßgeblich ist das Datenblatt ' +
  'des Herstellers (Normleistung nach DIN EN 442) und die raumweise Heizlast.';

// ============================================================================
// 1) Überschlägige Heizlast — NotizApp `HeizlastRechner`
//    Formel:  Q [W] = beheizte Fläche [m²] × spezifische Heizlast [W/m²]
//    Kennwerte: die vom Anwender in der NotizApp gepflegten Werte (ComboBox-
//    Tags 80 / 100 / 120 / 150). KEINE Normtabelle — bewusst nicht ergänzt.
// ============================================================================

export interface GebaeudeTyp {
  readonly wert: string;
  readonly label: string;
  /** Spezifische Heizlast in W/m² (Vorbelegung, im Feld überschreibbar). */
  readonly kennwert: string;
}

/** 1:1 die ComboBox der NotizApp; `ALTBAU_UNGEDAEMMT` ist dort vorausgewählt. */
export const GEBAEUDE_TYPEN: readonly GebaeudeTyp[] = [
  { wert: 'NEUBAU', label: 'Neubau', kennwert: '80' },
  { wert: 'ALTBAU_GEDAEMMT', label: 'Altbau, von außen gedämmt', kennwert: '100' },
  { wert: 'ALTBAU_UNGEDAEMMT', label: 'Altbau, nicht gedämmt', kennwert: '120' },
  { wert: 'ALTBAU_ALTE_FENSTER', label: 'Altbau, nicht gedämmt + alte Fenster', kennwert: '150' },
];

export const HEIZLAST_STANDARD_TYP = 'ALTBAU_UNGEDAEMMT';

export interface HeizlastErgebnis {
  /** Heizlast in Watt. */
  readonly watt: number;
  /** Heizlast in Kilowatt. */
  readonly kw: number;
}

/**
 * Überschlägige Heizlast. Gültig nur für Fläche > 0 und Kennwert > 0 — genau
 * wie in der NotizApp (dort: `flaeche <= 0 || q <= 0` → Ergebnis „—").
 */
export function heizlast(flaecheM2: number, kennwertWm2: number): HeizlastErgebnis | null {
  if (!(flaecheM2 > 0) || !(kennwertWm2 > 0)) return null;
  const watt = flaecheM2 * kennwertWm2;
  return { watt, kw: watt / 1000 };
}

// ============================================================================
// 2) Volumenstrom aus Leistung und Spreizung — NotizApp `VolumenstromRechner`
//    Formel:  V̇ [l/h] = Q [W] / (c · ΔT)   mit c = 1,163 Wh/(l·K)
//    (c ist das Produkt ρ·c_p für Wasser — dieselbe Physik wie
//     V̇ = Q / (ρ · c · ΔT), nur in der im Handwerk üblichen Schreibweise.)
// ============================================================================

/** Wärmekapazität Wasser in Wh/(l·K) — Konstante der NotizApp, unverändert. */
export const C_WASSER = 1.163;

export interface VolumenstromErgebnis {
  /** Volumenstrom in Litern pro Stunde. */
  readonly lh: number;
  /** Volumenstrom in Kubikmetern pro Stunde. */
  readonly m3h: number;
}

/** Gültig nur für Leistung > 0 und Spreizung > 0 (wie in der NotizApp). */
export function volumenstrom(leistungKw: number, spreizungK: number): VolumenstromErgebnis | null {
  if (!(leistungKw > 0) || !(spreizungK > 0)) return null;
  const lh = (leistungKw * 1000) / (C_WASSER * spreizungK);
  return { lh, m3h: lh / 1000 };
}

/** Spreizungs-Schnellwahl der NotizApp (Button-Tags 20 / 10 / 7). */
export const SPREIZUNG_PRESETS: readonly { readonly wert: string; readonly label: string }[] = [
  { wert: '20', label: '20 K (Heizkörper)' },
  { wert: '10', label: '10 K' },
  { wert: '7', label: '7 K (FBH)' },
];

// ============================================================================
// 3) Einheiten-Umrechner — NotizApp `EinheitenUmrechner`
//    Lineare Größen über einen Faktor zur Basiseinheit
//    (Basis = Wert × Faktor), Temperatur gesondert über Offsets.
//
//    Die fünf Kategorien Leistung/Druck/Temperatur/Volumenstrom/Energie sind
//    1:1 übernommen (gleiche Einheiten, gleiche Faktoren, gleiche Reihenfolge).
//    NEU ergänzt (in der NotizApp nicht vorhanden, vom Auftrag verlangt):
//    „Länge / Zoll" und „Wasserhärte".
// ============================================================================

export interface Einheit {
  readonly name: string;
  /** Basiswert = Wert × Faktor. Bei Temperatur ohne Bedeutung. */
  readonly faktor: number;
}

export interface Kategorie {
  readonly name: string;
  readonly einheiten: readonly Einheit[];
  /** Temperatur wird über Offsets umgerechnet, nicht über einen Faktor. */
  readonly istTemperatur?: boolean;
  /** Blendet die Gewinde-/Nennweiten-Zuordnung ein (siehe `GEWINDE_DN`). */
  readonly zeigtGewinde?: boolean;
  /** Zusatzhinweis unter der Ergebnisliste (Annahmen offenlegen). */
  readonly hinweis?: string;
}

export const KATEGORIEN: readonly Kategorie[] = [
  {
    name: 'Leistung',
    einheiten: [
      { name: 'kW', faktor: 1000 },
      { name: 'W', faktor: 1 },
      { name: 'kcal/h', faktor: 1.163 },
      { name: 'MW', faktor: 1_000_000 },
    ],
  },
  {
    name: 'Druck',
    einheiten: [
      { name: 'bar', faktor: 100_000 },
      { name: 'mbar', faktor: 100 },
      { name: 'kPa', faktor: 1000 },
      { name: 'Pa', faktor: 1 },
      { name: 'mWS', faktor: 9806.65 },
    ],
  },
  {
    name: 'Temperatur',
    istTemperatur: true,
    einheiten: [
      { name: '°C', faktor: 0 },
      { name: 'K', faktor: 0 },
      { name: '°F', faktor: 0 },
    ],
  },
  {
    name: 'Volumenstrom',
    einheiten: [
      { name: 'l/h', faktor: 1 },
      { name: 'm³/h', faktor: 1000 },
      { name: 'l/min', faktor: 60 },
      { name: 'l/s', faktor: 3600 },
    ],
  },
  {
    name: 'Energie',
    einheiten: [
      { name: 'kWh', faktor: 1 },
      { name: 'Wh', faktor: 0.001 },
      { name: 'MJ', faktor: 1 / 3.6 },
      { name: 'kcal', faktor: 1 / 860.4 },
    ],
  },
  {
    // Neu. Zoll ist hier die LÄNGE (1 Zoll = 25,4 mm — eine Definition, keine
    // Normtabelle). Die Gewinde-/Nennweiten-Zuordnung (½" → DN 15) ist KEINE
    // Länge und steht deshalb getrennt in `GEWINDE_DN` (siehe dort).
    name: 'Länge / Zoll',
    zeigtGewinde: true,
    einheiten: [
      { name: 'mm', faktor: 1 },
      { name: 'cm', faktor: 10 },
      { name: 'm', faktor: 1000 },
      { name: 'Zoll (")', faktor: 25.4 },
    ],
    hinweis:
      'Zoll als Längenmaß (1" = 25,4 mm). Die Rohr-Gewindegröße („½ Zoll") ist ' +
      'dagegen eine Verkehrsbezeichnung und KEIN Durchmesser — siehe die ' +
      'Zuordnungsliste im Formular.',
  },
  {
    // Neu. Basis: mmol/l (Erdalkali, also Ca+Mg). Die Faktoren sind aus den
    // molaren Massen abgeleitet, nicht aus einer Norm abgeschrieben:
    //   1 °dH  = 10 mg/l CaO    → 10 / 56,077 g/mol  = 0,178326 mmol/l
    //   1 °fH  = 10 mg/l CaCO₃  → 10 / 100,087 g/mol = 0,099913 mmol/l
    //   1 ppm  =  1 mg/l CaCO₃  →  1 / 100,087 g/mol = 0,0099913 mmol/l
    // Probe: 1 mmol/l → 5,6077 °dH; 1 °dH → 1,7848 °fH → 17,848 ppm.
    name: 'Wasserhärte',
    einheiten: [
      { name: '°dH', faktor: 10 / 56.077 },
      { name: 'mmol/l', faktor: 1 },
      { name: '°fH', faktor: 10 / 100.087 },
      { name: 'ppm CaCO₃ (mg/l)', faktor: 1 / 100.087 },
    ],
    hinweis:
      'Härte als Erdalkali (Ca + Mg). Umrechnung über die molaren Massen ' +
      '(°dH = 10 mg/l CaO, °fH und ppm bezogen auf CaCO₃).',
  },
];

/**
 * Gebräuchliche Zuordnung Rohrgewinde (Zoll) → Nennweite DN. Reine
 * Verkehrsbezeichnung — **keine Maßtabelle**: hier stehen bewusst weder
 * Außendurchmesser noch Wandstärken. Maßgeblich ist das Datenblatt.
 */
export const GEWINDE_DN: readonly { readonly zoll: string; readonly dn: string }[] = [
  { zoll: '⅜"', dn: 'DN 10' },
  { zoll: '½"', dn: 'DN 15' },
  { zoll: '¾"', dn: 'DN 20' },
  { zoll: '1"', dn: 'DN 25' },
  { zoll: '1¼"', dn: 'DN 32' },
  { zoll: '1½"', dn: 'DN 40' },
  { zoll: '2"', dn: 'DN 50' },
  { zoll: '2½"', dn: 'DN 65' },
  { zoll: '3"', dn: 'DN 80' },
  { zoll: '4"', dn: 'DN 100' },
];

export interface UmrechnungsZeile {
  readonly name: string;
  /** Bereits deutsch formatiert (wie in der NotizApp). */
  readonly wert: string;
  /** Die Einheit, in der eingegeben wurde (wird hervorgehoben). */
  readonly istQuelle: boolean;
}

/**
 * Rechnet einen Wert in alle Einheiten derselben Größe um — 1:1 die Methode
 * `Umrechnen` der NotizApp (inkl. Formatierung `#,##0.####` bzw. `0.##` bei
 * Temperatur).
 */
export function umrechnen(kat: Kategorie, vonIndex: number, wert: number): UmrechnungsZeile[] {
  const von = kat.einheiten[vonIndex];
  if (!von) return [];

  if (kat.istTemperatur) {
    const celsius =
      von.name === 'K' ? wert - 273.15 : von.name === '°F' ? ((wert - 32) * 5) / 9 : wert;
    return kat.einheiten.map((eh) => {
      const aus =
        eh.name === 'K' ? celsius + 273.15 : eh.name === '°F' ? (celsius * 9) / 5 + 32 : celsius;
      return { name: eh.name, wert: zahlKurz(aus), istQuelle: eh.name === von.name };
    });
  }

  const basis = wert * von.faktor;
  return kat.einheiten.map((eh) => ({
    name: eh.name,
    wert: zahlListe(basis / eh.faktor),
    istQuelle: eh.name === von.name,
  }));
}

// ============================================================================
// 4) Heizkörper-Umrechnung auf eine andere Spreizung (NEU — nicht in NotizApp)
//
//    Q = Q_norm · (ΔΘ_ln / ΔΘ_ln,norm)^n
//    ΔΘ_ln = (T_VL − T_RL) / ln((T_VL − T_Raum) / (T_RL − T_Raum))
//    Normbedingung 75/65/20 → ΔΘ_ln,norm = 10 / ln(55/45) = 49,83 K
//
//    Der Exponent n ist ein Erfahrungs-/Herstellerwert (Bauart), KEINE
//    Normtabelle. Er ist vorbelegt und frei überschreibbar.
// ============================================================================

/** Übertemperatur der Normbedingung 75/65/20 (DIN EN 442) in K. */
export const DELTA_NORM = 49.83;

/** Normbedingung, gegen die die Katalogleistung angegeben ist. */
export const NORM_BEDINGUNG = { vl: 75, rl: 65, raum: 20 } as const;

export interface HeizkoerperBauart {
  readonly wert: string;
  readonly label: string;
  /** Heizkörperexponent n (Erfahrungswert, herstellerabhängig). */
  readonly exponent: string;
}

/**
 * Gebräuchliche Exponenten je Bauart — Erfahrungswerte aus der Praxis, KEIN
 * Normauszug. Der maßgebliche Wert steht im Datenblatt des Herstellers und
 * lässt sich im Formular überschreiben.
 */
export const BAUARTEN: readonly HeizkoerperBauart[] = [
  { wert: 'PLATTE', label: 'Flach-/Plattenheizkörper', exponent: '1,30' },
  { wert: 'GLIEDER', label: 'Gliederheizkörper', exponent: '1,30' },
  { wert: 'ROEHREN', label: 'Röhrenradiator', exponent: '1,30' },
  { wert: 'KONVEKTOR', label: 'Konvektor', exponent: '1,35' },
  { wert: 'FBH', label: 'Fußbodenheizung', exponent: '1,10' },
];

export type HeizkoerperFehler = 'VL_UNTER_RAUM' | 'RL_UNTER_RAUM' | 'VL_KLEINER_RL' | 'EINGABE';

export interface HeizkoerperErgebnis {
  /** Logarithmische Übertemperatur des neuen Betriebspunkts in K. */
  readonly deltaLn: number;
  /** Leistungsfaktor gegenüber der Normleistung (z. B. 0,62). */
  readonly faktor: number;
  /** Leistung am neuen Betriebspunkt in Watt. */
  readonly watt: number;
  /** Leistung am neuen Betriebspunkt in kW. */
  readonly kw: number;
}

/**
 * Logarithmische Übertemperatur. Für T_VL == T_RL geht die Formel in den
 * Grenzwert (T − T_Raum) über — sonst wäre der Nenner 0. Übertemperaturen
 * müssen positiv sein, sonst gibt der Heizkörper keine Wärme ab.
 */
export function deltaLogarithmisch(
  vl: number,
  rl: number,
  raum: number,
): { ok: true; wert: number } | { ok: false; fehler: HeizkoerperFehler } {
  if (vl < rl) return { ok: false, fehler: 'VL_KLEINER_RL' };
  if (vl <= raum) return { ok: false, fehler: 'VL_UNTER_RAUM' };
  if (rl <= raum) return { ok: false, fehler: 'RL_UNTER_RAUM' };
  const dVl = vl - raum;
  const dRl = rl - raum;
  // Grenzfall VL == RL: ln(1) = 0 → Division durch null. Der mathematische
  // Grenzwert ist die (dann einheitliche) Übertemperatur selbst.
  if (Math.abs(vl - rl) < 1e-9) return { ok: true, wert: dVl };
  return { ok: true, wert: (vl - rl) / Math.log(dVl / dRl) };
}

/**
 * Heizkörperleistung an einem anderen Betriebspunkt.
 * `normleistungW` ist die Katalogleistung bei 75/65/20.
 */
export function heizkoerper(
  normleistungW: number,
  vl: number,
  rl: number,
  raum: number,
  exponent: number,
): { ok: true; ergebnis: HeizkoerperErgebnis } | { ok: false; fehler: HeizkoerperFehler } {
  if (!(normleistungW > 0) || !(exponent > 0)) return { ok: false, fehler: 'EINGABE' };
  const d = deltaLogarithmisch(vl, rl, raum);
  if (!d.ok) return d;
  const faktor = Math.pow(d.wert / DELTA_NORM, exponent);
  const watt = normleistungW * faktor;
  return { ok: true, ergebnis: { deltaLn: d.wert, faktor, watt, kw: watt / 1000 } };
}

export function heizkoerperFehlerText(f: HeizkoerperFehler): string {
  switch (f) {
    case 'VL_KLEINER_RL':
      return 'Die Vorlauftemperatur muss über der Rücklauftemperatur liegen.';
    case 'VL_UNTER_RAUM':
      return 'Die Vorlauftemperatur muss über der Raumtemperatur liegen — sonst heizt der Heizkörper nicht.';
    case 'RL_UNTER_RAUM':
      return 'Die Rücklauftemperatur muss über der Raumtemperatur liegen — sonst heizt der Heizkörper nicht.';
    case 'EINGABE':
      return 'Bitte Normleistung und Exponent als positive Zahlen eingeben.';
  }
}

// ============================================================================
// 5) Anlagenwasserinhalt — NotizApp `WasserinhaltRechner`
//    Summe = Rohr(L × l/m) + FBH(L × l/m) + HK(Anzahl × Inhalt) + Erzeuger + Puffer
//    Die l/m-Werte sind die vom Anwender in der NotizApp gepflegten Kennwerte
//    (lichter Querschnitt gängiger Dimensionen) — 1:1 übernommen, keine
//    Normtabelle, nichts ergänzt und nichts geändert.
// ============================================================================

/**
 * Ein Feldwert wie in der NotizApp (`static double Wert(TextBox)`): leer,
 * unlesbar oder **nicht positiv** zählt als 0 — nicht als Fehler. Ein negativer
 * Eintrag zieht also nichts ab, er wird ignoriert.
 */
export const positivOderNull = (n: number | null): number => (n != null && n > 0 ? n : 0);

export interface RohrTyp {
  readonly wert: string;
  readonly label: string;
  /** Wasserinhalt je Meter in Litern. */
  readonly lProM: number;
}

/** 1:1 die ComboBox `RohrTyp` der NotizApp (Vorauswahl: Kupfer 22×1). */
export const ROHR_TYPEN: readonly RohrTyp[] = [
  { wert: 'CU_15', label: 'Kupfer 15×1 (0,13 l/m)', lProM: 0.133 },
  { wert: 'CU_18', label: 'Kupfer 18×1 (0,20 l/m)', lProM: 0.201 },
  { wert: 'CU_22', label: 'Kupfer 22×1 (0,31 l/m)', lProM: 0.314 },
  { wert: 'CU_28', label: 'Kupfer 28×1,5 (0,49 l/m)', lProM: 0.491 },
  { wert: 'CU_35', label: 'Kupfer 35×1,5 (0,80 l/m)', lProM: 0.804 },
  { wert: 'VB_16', label: 'Verbund 16×2 (0,11 l/m)', lProM: 0.113 },
  { wert: 'VB_20', label: 'Verbund 20×2 (0,20 l/m)', lProM: 0.201 },
  { wert: 'VB_26', label: 'Verbund 26×3 (0,31 l/m)', lProM: 0.314 },
  { wert: 'ST_15', label: 'Stahl DN15 ½″ (0,20 l/m)', lProM: 0.201 },
  { wert: 'ST_20', label: 'Stahl DN20 ¾″ (0,37 l/m)', lProM: 0.366 },
  { wert: 'ST_25', label: 'Stahl DN25 1″ (0,58 l/m)', lProM: 0.581 },
];
export const ROHR_STANDARD = 'CU_22';

/** 1:1 die ComboBox `FbhTyp` der NotizApp (Vorauswahl: 16×2). */
export const FBH_TYPEN: readonly RohrTyp[] = [
  { wert: 'FBH_16', label: '16×2 (0,11 l/m)', lProM: 0.113 },
  { wert: 'FBH_17', label: '17×2 (0,13 l/m)', lProM: 0.133 },
  { wert: 'FBH_20', label: '20×2 (0,20 l/m)', lProM: 0.201 },
];
export const FBH_STANDARD = 'FBH_16';

/** Vorbelegung „Inhalt je Heizkörper" der NotizApp (`HkInhalt.Text = "5"`). */
export const HK_INHALT_STANDARD = '5';

export interface WasserinhaltEingabe {
  readonly rohrLaenge: number | null;
  readonly rohrLProM: number;
  readonly fbhLaenge: number | null;
  readonly fbhLProM: number;
  readonly hkAnzahl: number | null;
  readonly hkInhalt: number | null;
  readonly erzeuger: number | null;
  readonly puffer: number | null;
}

export interface WasserinhaltTeil {
  readonly label: string;
  readonly liter: number;
}

export interface WasserinhaltErgebnis {
  readonly summe: number;
  /** Nur die Komponenten > 0 — wie die Aufschlüsselung der NotizApp. */
  readonly teile: readonly WasserinhaltTeil[];
}

/** Summe ≤ 0 → kein Ergebnis (NotizApp zeigt dann „—"). */
export function wasserinhalt(e: WasserinhaltEingabe): WasserinhaltErgebnis | null {
  const rohr = positivOderNull(e.rohrLaenge) * e.rohrLProM;
  const fbh = positivOderNull(e.fbhLaenge) * e.fbhLProM;
  const hk = positivOderNull(e.hkAnzahl) * positivOderNull(e.hkInhalt);
  const erz = positivOderNull(e.erzeuger);
  const puf = positivOderNull(e.puffer);
  const summe = rohr + fbh + hk + erz + puf;
  if (summe <= 0) return null;

  const teile: WasserinhaltTeil[] = [];
  if (rohr > 0) teile.push({ label: 'Rohr', liter: rohr });
  if (fbh > 0) teile.push({ label: 'FBH', liter: fbh });
  if (hk > 0) teile.push({ label: 'Heizkörper', liter: hk });
  if (erz > 0) teile.push({ label: 'Erzeuger', liter: erz });
  if (puf > 0) teile.push({ label: 'Puffer', liter: puf });
  return { summe, teile };
}

// ============================================================================
// 6) Membran-Ausdehnungsgefäß (MAG) — NotizApp `AusdehnungsgefaessRechner`
//
//    V_n = (V_e + V_wv) · (p_e + 1) / (p_e − p_0)
//      V_e  = Anlageninhalt · Ausdehnungskoeffizient (max. Vorlauftemperatur)
//      V_wv = Wasservorlage = max(0,5 % · Anlageninhalt; 3 l)
//      p_0  = Vordruck  = statische Höhe / 10 + 0,3 bar
//      p_e  = Enddruck  = Ansprechdruck SV − 0,5 bar
//
//    Rechenvorschrift und Konventionen 1:1 aus der NotizApp. Die
//    Ausdehnungskoeffizienten und die Nenngrößenliste sind die dort vom
//    Anwender gepflegten Werte — es ist KEINE Normtabelle abgedruckt.
// ============================================================================

/** Handelsübliche Nenngrößen in Litern — Liste der NotizApp, unverändert. */
export const MAG_NENNGROESSEN: readonly number[] = [
  8, 12, 18, 25, 35, 50, 80, 100, 140, 200, 250, 300, 400, 500, 600, 800, 1000,
];

export interface MagTemperatur {
  readonly wert: string;
  readonly label: string;
  /** Ausdehnungskoeffizient (Füllung bei ~10 °C). */
  readonly beta: number;
}

/** ComboBox `TempBox` der NotizApp (Vorauswahl 70 °C). */
export const MAG_TEMPERATUREN: readonly MagTemperatur[] = [
  { wert: '50', label: '50 °C', beta: 0.0121 },
  { wert: '60', label: '60 °C', beta: 0.0171 },
  { wert: '70', label: '70 °C', beta: 0.0228 },
  { wert: '80', label: '80 °C', beta: 0.0289 },
  { wert: '90', label: '90 °C', beta: 0.0359 },
];
export const MAG_TEMP_STANDARD = '70';

/** ComboBox `SvBox` der NotizApp (Vorauswahl 3,0 bar). */
export const MAG_SICHERHEITSVENTILE: readonly { readonly wert: string; readonly label: string }[] =
  [
    { wert: '2.5', label: '2,5 bar' },
    { wert: '3.0', label: '3,0 bar' },
  ];
export const MAG_SV_STANDARD = '3.0';

/** Vorbelegung „statische Höhe" der NotizApp (`HoeheBox.Text = "5"`). */
export const MAG_HOEHE_STANDARD = '5';

/** Pflichthinweis auf jeder MAG-Ausgabe. Fehldimensionierung = Anlagenschaden. */
export const MAG_HAFTUNG =
  'Auslegungshilfe zur Plausibilisierung, KEIN Nachweis. Maßgeblich sind die ' +
  'Anlagendaten und die Herstellerangaben (DIN EN 12828 / DIN 4807-2); ' +
  'Wasservorlage, Vordruck und Enddruck sind hier als gängige Konvention ' +
  'angesetzt und im Einzelfall zu prüfen.';

export type MagFehler = 'INHALT' | 'DRUCK';

export interface MagErgebnis {
  /** Ausdehnungsvolumen V_e in Litern. */
  readonly ve: number;
  /** Wasservorlage V_wv in Litern. */
  readonly vwv: number;
  /** Vordruck p_0 in bar. */
  readonly p0: number;
  /** Enddruck p_e in bar. */
  readonly pe: number;
  /** Rechnerisch nötiges Nennvolumen in Litern. */
  readonly vn: number;
  /** Kleinste Nenngröße ≥ V_n, oder null (dann Sonderauslegung). */
  readonly empfohlen: number | null;
}

/**
 * MAG-Nennvolumen. `hoehe` darf leer sein (dann 0 — wie in der NotizApp, wo ein
 * unlesbares Höhenfeld als 0 gilt). Anlageninhalt ≤ 0 → kein Ergebnis.
 */
export function ausdehnungsgefaess(
  inhalt: number | null,
  beta: number,
  pSv: number,
  hoehe: number | null,
): { ok: true; ergebnis: MagErgebnis } | { ok: false; fehler: MagFehler } {
  if (inhalt == null || !(inhalt > 0)) return { ok: false, fehler: 'INHALT' };
  const h = hoehe ?? 0;

  const ve = inhalt * beta;
  const vwv = Math.max(0.005 * inhalt, 3);
  const p0 = h / 10 + 0.3;
  const pe = pSv - 0.5;

  // Zu kleine Druckdifferenz: die Formel liefert sonst absurd große Gefäße
  // (oder ein negatives Volumen). Grenze 0,1 bar — wie in der NotizApp.
  if (pe - p0 <= 0.1) return { ok: false, fehler: 'DRUCK' };

  const vn = ((ve + vwv) * (pe + 1)) / (pe - p0);
  const empfohlen = MAG_NENNGROESSEN.find((g) => g >= vn) ?? null;
  return { ok: true, ergebnis: { ve, vwv, p0, pe, vn, empfohlen } };
}

export function magFehlerText(f: MagFehler): string {
  switch (f) {
    case 'INHALT':
      return 'Bitte den Anlagenwasserinhalt in Litern eingeben (größer als 0).';
    case 'DRUCK':
      return (
        'Enddruck ≤ Vordruck: größeres Sicherheitsventil oder geringere statische Höhe ' +
        'nötig (p_e muss deutlich über p_0 liegen).'
      );
  }
}

// ============================================================================
// 7) Aufmaß / Mengenermittlung mit Verschnitt (NEU — nicht in der NotizApp)
//
//    Teilmaße (mit Bezeichnung, addierend oder abziehend)
//      → Nettomenge
//      → + Verschnitt %
//      → aufgerundet auf die Gebinde-/Verpackungseinheit
//      → Bestellmenge
//
//    ABGRENZUNG ZU GELD: Hier wird eine **Menge** ermittelt, kein Betrag. Sie
//    geht als Punkt-String (`mengeApi`) in die Belegposition — genau so, wie
//    ein Mensch sie eintippen würde. Der Server rechnet daraus (und nur dort)
//    Positionsnetto und Summen. Es wird KEIN Geldwert im Browser gerechnet.
// ============================================================================

/** Kaufmännisch auf `stellen` Nachkommastellen runden (DB-Skala: 3). */
export function runde(n: number, stellen = 3): number {
  const f = 10 ** stellen;
  return Math.round((n + Number.EPSILON * Math.abs(n)) * f) / f;
}

/**
 * Menge als API-Dezimalstring (Punkt, max. 3 Nachkommastellen, ohne
 * Tausendertrenner) — die Form, die `QuoteLineInput.quantity` erwartet.
 * Niemals `number` ins Datenmodell: die Zahl wird hier zum String und bleibt es.
 */
export function mengeApi(n: number): string {
  const s = runde(n, 3).toFixed(3); // enthält immer einen Punkt
  return s.replace(/0+$/, '').replace(/\.$/, '');
}

/**
 * Gebinde-/VE-Eingabe auswerten.
 *
 * **Leer** heißt „keine Gebindegröße" (gültig, es wird nicht aufgerundet).
 * Eine **ausgefüllte, aber unlesbare, mehrdeutige („1.500") oder nicht positive**
 * Eingabe ist ein FEHLER und darf nicht still als „keine Gebindegröße" gelten:
 * sonst bliebe die Aufrundung auf volle Kartons aus und der Anwender bestellte
 * zu wenig, ohne es zu merken.
 */
export function gebindeAus(roh: string | null | undefined): {
  readonly gebinde: number | null;
  readonly ungueltig: boolean;
} {
  const s = (roh ?? '').trim();
  if (s === '') return { gebinde: null, ungueltig: false };
  const n = zahlAus(s);
  return n != null && n > 0 ? { gebinde: n, ungueltig: false } : { gebinde: null, ungueltig: true };
}

/** Aufrundung mit Toleranz gegen Fließkomma-Artefakte (3 × 1,44 = 4,32 …0001). */
function aufrunden(n: number): number {
  return Math.ceil(n - 1e-9);
}

export type MessArt = 'FLAECHE' | 'LAENGE' | 'STUECK' | 'VOLUMEN';
export type MassFeld = 'laenge' | 'breite' | 'hoehe';

export interface MessArtDef {
  readonly wert: MessArt;
  readonly label: string;
  /** Einheit der Menge — geht als `unit` in die Belegposition. */
  readonly einheit: string;
  /** Welche Maßfelder das Teilmaß braucht (alle in Metern). */
  readonly masse: readonly MassFeld[];
  readonly zweck: string;
}

export const MESS_ARTEN: readonly MessArtDef[] = [
  {
    wert: 'FLAECHE',
    label: 'Fläche',
    einheit: 'm²',
    masse: ['laenge', 'breite'],
    zweck: 'Wand, Boden, Decke — Länge × Höhe bzw. Breite. Fenster/Türen als Abzug erfassen.',
  },
  {
    wert: 'LAENGE',
    label: 'Länge',
    einheit: 'm',
    masse: ['laenge'],
    zweck: 'Rohrleitung, Sockelleiste, Kabelkanal — Länge je Strang.',
  },
  {
    wert: 'STUECK',
    label: 'Stückzahl',
    einheit: 'Stk',
    masse: [],
    zweck: 'Auslässe, Ventile, Heizkörper — nur die Anzahl je Teilmaß.',
  },
  {
    wert: 'VOLUMEN',
    label: 'Volumen',
    einheit: 'm³',
    masse: ['laenge', 'breite', 'hoehe'],
    zweck: 'Estrich, Aushub, Schüttung — Länge × Breite × Höhe.',
  },
];

export const MESS_ART_STANDARD: MessArt = 'FLAECHE';

/** Bezeichnung eines Maßfelds (für Label und Rechenweg). */
export const MASS_LABEL: Record<MassFeld, string> = {
  laenge: 'Länge',
  breite: 'Breite / Höhe',
  hoehe: 'Höhe / Dicke',
};

export interface TeilmassEingabe {
  readonly bezeichnung: string;
  /** Faktor bzw. — bei STUECK — die Menge selbst. Leer = 1 (nicht bei STUECK). */
  readonly anzahl: number | null;
  readonly laenge: number | null;
  readonly breite: number | null;
  readonly hoehe: number | null;
  /** true = die Menge wird abgezogen (Fenster, Tür, Aussparung). */
  readonly abzug: boolean;
}

export type TeilmassStatus = 'OK' | 'LEER' | 'UNVOLLSTAENDIG';

export interface TeilmassErgebnis {
  readonly status: TeilmassStatus;
  readonly bezeichnung: string;
  readonly abzug: boolean;
  /** Immer positiv; das Vorzeichen trägt `abzug`. 0, wenn nicht `OK`. */
  readonly menge: number;
  /** „3 × 2,5 m × 2,6 m = 19,5 m²" — nur bei `OK`. */
  readonly rechenweg: string;
}

/**
 * Ein Teilmaß auswerten.
 *  - `LEER`: nichts eingetragen → wird stillschweigend übergangen.
 *  - `UNVOLLSTAENDIG`: etwas eingetragen, aber ein Maß fehlt oder ist ≤ 0 →
 *    das Teilmaß zählt NICHT und der Rechner blockiert die Übernahme. Ein still
 *    verschlucktes Teilmaß wäre eine falsche Menge im Angebot.
 */
export function teilmass(art: MessArtDef, t: TeilmassEingabe): TeilmassErgebnis {
  const werte: Record<MassFeld, number | null> = {
    laenge: t.laenge,
    breite: t.breite,
    hoehe: t.hoehe,
  };
  const leer =
    t.bezeichnung.trim() === '' &&
    t.anzahl == null &&
    art.masse.every((m) => werte[m] == null) &&
    !t.abzug;
  const basis = { bezeichnung: t.bezeichnung.trim(), abzug: t.abzug, menge: 0, rechenweg: '' };
  if (leer) return { ...basis, status: 'LEER' };

  // Anzahl: leer = 1 (ein Teilmaß ohne Zählangabe ist eines). Bei STUECK ist die
  // Anzahl das Maß selbst — dort ist sie Pflicht.
  const anzahl = t.anzahl ?? (art.wert === 'STUECK' ? null : 1);
  if (anzahl == null || !(anzahl > 0)) return { ...basis, status: 'UNVOLLSTAENDIG' };
  if (art.masse.some((m) => werte[m] == null || !(werte[m]! > 0))) {
    return { ...basis, status: 'UNVOLLSTAENDIG' };
  }

  const faktoren = art.masse.map((m) => werte[m]!);
  const menge = runde(
    faktoren.reduce((a, b) => a * b, anzahl),
    3,
  );
  if (!(menge > 0)) return { ...basis, status: 'UNVOLLSTAENDIG' };

  // Bei STUECK ist die Anzahl schon das Ergebnis — kein „12 Stk = 12 Stk".
  const rechenweg =
    art.wert === 'STUECK'
      ? `${zahlMenge(menge)} ${art.einheit}`
      : [
          ...(t.anzahl != null ? [`${zahlKurz(anzahl)} ×`] : []),
          faktoren.map((f) => `${zahlKurz(f)} m`).join(' × '),
          `= ${zahlMenge(menge)} ${art.einheit}`,
        ].join(' ');
  return { ...basis, status: 'OK', menge, rechenweg };
}

export interface AufmassErgebnis {
  readonly einheit: string;
  readonly teile: readonly TeilmassErgebnis[];
  /** Anzahl angefangener, aber unvollständiger Teilmaße (blockiert die Übernahme). */
  readonly unvollstaendig: number;
  /** Summe der Teilmaße (Zugänge minus Abzüge), auf 3 Stellen gerundet. */
  readonly netto: number;
  readonly verschnittProzent: number;
  /** `brutto − netto` — passt damit immer exakt zur Anzeige. */
  readonly verschnittMenge: number;
  readonly brutto: number;
  /** Gebinde-/Verpackungsgröße in der Einheit der Menge, oder null. */
  readonly gebinde: number | null;
  /** Zahl der Gebinde (aufgerundet), oder null. */
  readonly gebindeAnzahl: number | null;
  /** Was bestellt wird: brutto, bzw. auf volle Gebinde aufgerundet. */
  readonly bestellmenge: number;
}

/**
 * Aufmaß rechnen. Ergebnis nur, wenn die Nettomenge **positiv** ist — eine
 * Menge ≤ 0 (nur Abzüge, oder Abzüge größer als die Fläche) ist keine
 * Bestellmenge und darf nicht in einen Beleg (die DB verlangt `quantity > 0`).
 */
export function aufmass(
  art: MessArtDef,
  eingaben: readonly TeilmassEingabe[],
  verschnittProzent: number,
  gebinde: number | null,
): AufmassErgebnis | null {
  const teile = eingaben.map((t) => teilmass(art, t));
  const unvollstaendig = teile.filter((t) => t.status === 'UNVOLLSTAENDIG').length;
  const netto = runde(
    teile.filter((t) => t.status === 'OK').reduce((s, t) => s + (t.abzug ? -t.menge : t.menge), 0),
    3,
  );
  if (!(netto > 0) || !(verschnittProzent >= 0)) return null;

  const brutto = runde(netto * (1 + verschnittProzent / 100), 3);
  const verschnittMenge = runde(brutto - netto, 3);
  const ve = gebinde != null && gebinde > 0 ? gebinde : null;
  const gebindeAnzahl = ve ? aufrunden(brutto / ve) : null;
  const bestellmenge = ve && gebindeAnzahl ? runde(gebindeAnzahl * ve, 3) : brutto;

  return {
    einheit: art.einheit,
    teile,
    unvollstaendig,
    netto,
    verschnittProzent,
    verschnittMenge,
    brutto,
    gebinde: ve,
    gebindeAnzahl,
    bestellmenge,
  };
}
