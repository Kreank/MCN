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
    const celsius = von.name === 'K' ? wert - 273.15 : von.name === '°F' ? ((wert - 32) * 5) / 9 : wert;
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

export type HeizkoerperFehler =
  | 'VL_UNTER_RAUM'
  | 'RL_UNTER_RAUM'
  | 'VL_KLEINER_RL'
  | 'EINGABE';

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
