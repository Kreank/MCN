import { Component, computed, effect, inject, input, signal, viewChild } from '@angular/core';
import { AuthService } from '../../core/auth.service';
import { RaumService } from '../../core/raum.service';
import { Aufmass, Auslegung, Room, istStillgelegt, roomTypeLabel } from '../../core/raum.model';
import { Building, gebaeudeLabel, unitTypeLabel } from '../../core/property.model';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { fehlerDetail } from '../../shared/http-fehler';
import { AuslegungPanel } from './auslegung-panel';
import { GeschossPlan } from './grundriss/geschoss-plan';
import { RaumEditor } from './raum-editor';
import { RAUM_HEIZLAST_HAFTUNG, mitEinheit, summeApi, zeige } from './raum-rechnen';

type Zustand = 'loading' | 'ready' | 'error';

/** Liste oder Grundriss? Zwei Blicke auf dieselben Räume. */
export type Ansicht = 'liste' | 'grundriss';

/** Ein Geschoss mit seinen Räumen. Ohne Geschossangabe: „ohne Geschoss". */
interface Geschoss {
  readonly key: string;
  readonly label: string;
  readonly raeume: Room[];
  /** Nur die aktiven Räume zählen — Fläche, Volumen und Anzahl. */
  readonly aktivAnzahl: number;
  readonly inaktivAnzahl: number;
  readonly flaeche: number | null;
  readonly volumen: number | null;
}

/**
 * Eine Gruppe der Raumliste — eine Einheit, „Gebäude allgemein" oder „Ohne
 * Zuordnung". Das ist die Sicht des Betriebs: **in einer Einheit lege ich die
 * Räume an.** `building_id`/`unit_id` sind die Vorbelegung für „＋ Raum".
 */
interface RaumGruppe {
  readonly key: string;
  readonly titel: string;
  /** Kurzer Zusatz (Einheitstyp bzw. Erklärung) — nie nur der Titel. */
  readonly zusatz: string;
  readonly building_id: string | null;
  readonly unit_id: string | null;
  readonly raeume: Room[];
  readonly aktivAnzahl: number;
  readonly inaktivAnzahl: number;
  readonly flaeche: number | null;
  readonly volumen: number | null;
}

/** Sammelschlüssel für Räume ohne Geschossangabe — kollidiert mit keinem echten
 *  Geschossnamen. Ein gewöhnlicher String, **kein NUL-Byte**: das machte die
 *  Datei für git binär und damit nicht diffbar (und nicht reviewbar). */
const OHNE_GESCHOSS = '__ohne_geschoss__';

/**
 * Raumaufmaß einer Liegenschaft — Räume aufnehmen wie mit dem Zollstock in der
 * Hand: **ein Raum nach dem anderen, jeder Schritt für sich speicherbar.**
 *
 * Diese Komponente ist die Übersicht (Räume je Geschoss + Aufmaß-Summe der
 * Liegenschaft); die Erfassung selbst macht `RaumEditor`.
 *
 * **Die Heizlast rechnet der Server.** Fehlt sie, steht hier „unbekannt" mit dem
 * Grund — nie 0. Und über allem der Hinweis: überschlägig, kein Nachweis nach
 * DIN EN 12831.
 */
@Component({
  selector: 'app-raumaufmass',
  imports: [AuslegungPanel, RaumEditor, Bestaetigung, GeschossPlan],
  templateUrl: './raumaufmass.html',
  styleUrl: './raumaufmass.scss',
})
export class Raumaufmass {
  private readonly svc = inject(RaumService);
  private readonly auth = inject(AuthService);

  readonly propertyId = input.required<string>();
  /**
   * Gebäude/Einheiten der Liegenschaft (aus dem Struktur-Tab). Sie geben der
   * Raumliste ihre Gliederung: je Einheit eine Gruppe, dazu „Gebäude allgemein".
   * Leer = die Liegenschaft hat (noch) keine Struktur → die Ansicht fällt sauber
   * auf „Ohne Zuordnung" zurück.
   */
  readonly gebaeude = input<readonly Building[]>([]);

  private readonly panel = viewChild(AuslegungPanel);

  protected readonly haftung = RAUM_HEIZLAST_HAFTUNG;

  protected readonly zustand = signal<Zustand>('loading');
  protected readonly raeume = signal<Room[]>([]);
  protected readonly aufmass = signal<Aufmass | null>(null);
  protected readonly fehler = signal<string | null>(null);
  /** Fehler beim stillen Nachladen — die Liste bleibt stehen, die Lüge nicht. */
  protected readonly nachladeFehler = signal<string | null>(null);
  protected readonly ansage = signal('');

  /** Welcher Raum wird gerade erfasst? '' = Liste, 'neu' = neuer Raum. */
  protected readonly offenerRaum = signal<string | null>(null);

  /**
   * Vorbelegung der Zuordnung für einen NEUEN Raum — gesetzt, wenn „＋ Raum" aus
   * einer Einheiten-Gruppe geklickt wurde. Beim Öffnen eines bestehenden Raumes
   * und in der allgemeinen „＋ Raum"-Aktion ist sie `null`.
   */
  protected readonly neuVorbelegung = signal<{
    building_id: string | null;
    unit_id: string | null;
  } | null>(null);

  /**
   * Liste oder Grundriss. Die Koordinaten des Umrisses gelten je GESCHOSS
   * (Modulkopf 0091) — die Etagenübersicht ist deshalb nichts weiter als „alle
   * Räume dieses Geschosses zusammen zeichnen".
   */
  protected readonly ansicht = signal<Ansicht>('liste');

  /** Wie viele Räume haben überhaupt einen Umriss? (Sonst wäre der Plan leer.) */
  protected readonly gezeichnetAnzahl = computed(
    () => this.raeume().filter((r) => (r.vertices?.length ?? 0) >= 3).length,
  );

  /**
   * Stillgelegte Räume mitladen? Standardmäßig nein — **der Server** entscheidet
   * das (`?mit_inaktiven=true`), nicht ein Filter im Client. So können die Summen
   * hier gar nicht erst mit Werten arbeiten, die der Server nicht mitzählt.
   */
  protected readonly mitInaktiven = signal(false);

  /** Raum, für den gerade die Stilllegung bestätigt werden soll. */
  protected readonly stillzulegen = signal<Room | null>(null);
  protected readonly statusLaeuft = signal(false);

  private ladeReq = 0;

  // Räume anlegen/bearbeiten (inkl. Aufbau und Grundriss): `require_scoped` —
  // genau die Arbeit, die der Monteur am Objekt macht. Deshalb `darf`.
  protected readonly darfAnlegen = computed(() => this.auth.darf('property', 'ANLEGEN'));
  protected readonly darfAendern = computed(() => this.auth.darf('property', 'AENDERN'));
  /**
   * Auslegungsdaten des OBJEKTS (Norm-Außentemperatur u. a.): `darfAlle`.
   *
   * `PATCH /api/property/properties/{id}/auslegung` ist fail-closed (`require`)
   * — anders als die Raum-Endpunkte daneben. Ein Konto mit row_scope EIGENE
   * bekommt dort 403; der Speichern-Knopf und die beiden Sprungknöpfe
   * („Auslegungsdaten ergänzen") dürfen ihm deshalb nicht angeboten werden. Die
   * Werte selbst bleiben lesbar — er braucht sie für die Heizlast.
   */
  protected readonly darfAuslegung = computed(() => this.auth.darfAlle('property', 'AENDERN'));

  protected readonly bearbeiteterRaum = computed(() => {
    const id = this.offenerRaum();
    if (!id || id === 'neu') return null;
    return this.raeume().find((r) => r.id === id) ?? null;
  });

  /**
   * Die aktiven Räume — **Grundlage jeder Summe hier**. Ein stillgelegter Raum
   * bleibt sichtbar (Nachweis über den Bestand), zählt aber nicht mit: sonst
   * widerspräche die Liste der Aufmaß-Summe des Servers, der ihn ebenfalls
   * herausnimmt.
   */
  protected readonly aktiveRaeume = computed(() => this.raeume().filter((r) => !istStillgelegt(r)));

  protected readonly inaktivAnzahl = computed(
    () => this.raeume().filter((r) => istStillgelegt(r)).length,
  );

  /** Räume nach Geschoss gruppiert, mit Fläche/Volumen je Geschoss (nur aktive). */
  protected readonly geschosse = computed<Geschoss[]>(() => {
    const gruppen = new Map<string, Room[]>();
    for (const r of this.raeume()) {
      const key = (r.storey ?? '').trim() || OHNE_GESCHOSS;
      const liste = gruppen.get(key);
      if (liste) liste.push(r);
      else gruppen.set(key, [r]);
    }
    return [...gruppen.entries()]
      .sort((a, b) => a[0].localeCompare(b[0], 'de'))
      .map(([key, raeume]) => {
        const aktive = raeume.filter((r) => !istStillgelegt(r));
        return {
          key,
          label: key === OHNE_GESCHOSS ? 'Ohne Geschossangabe' : key,
          // Stillgelegte ans Ende — sie sind Bestandsnachweis, nicht Arbeitsvorrat.
          raeume: [...raeume].sort(
            (a, b) =>
              Number(istStillgelegt(a)) - Number(istStillgelegt(b)) ||
              a.name.localeCompare(b.name, 'de'),
          ),
          aktivAnzahl: aktive.length,
          inaktivAnzahl: raeume.length - aktive.length,
          flaeche: summeApi(aktive.map((r) => r.floor_area_m2)),
          volumen: summeApi(aktive.map((r) => r.volume_m3)),
        };
      });
  });

  /** Hat die Liegenschaft überhaupt eine Struktur (Gebäude)? */
  protected readonly hatStruktur = computed(() => this.gebaeude().length > 0);

  /**
   * Räume nach **Einheit** gruppiert (die Sicht des Betriebs) — je Einheit eine
   * Gruppe (auch leer, denn genau da will man anlegen), dazu je Gebäude „Gebäude
   * allgemein" (Keller/Technik: Raum nur mit `building_id`) und „Ohne Zuordnung"
   * (Altbestand: Raum ohne `building_id`).
   *
   * Reihenfolge = Struktur-Tab: Gebäude nach Nummer, Einheiten nach Nummer. Ein
   * Raum, dessen Einheit/Gebäude nicht (mehr) in der Struktur steht, fällt in
   * „Ohne Zuordnung" — er verschwindet nicht.
   */
  protected readonly einheitenGruppen = computed<RaumGruppe[]>(() => {
    const alle = this.raeume();
    const zugeordnet = new Set<string>();

    const bauen = (
      key: string,
      titel: string,
      zusatz: string,
      building_id: string | null,
      unit_id: string | null,
      raeume: Room[],
    ): RaumGruppe => {
      const aktive = raeume.filter((r) => !istStillgelegt(r));
      return {
        key,
        titel,
        zusatz,
        building_id,
        unit_id,
        // Stillgelegte ans Ende — Bestandsnachweis, nicht Arbeitsvorrat.
        raeume: [...raeume].sort(
          (a, b) =>
            Number(istStillgelegt(a)) - Number(istStillgelegt(b)) ||
            a.name.localeCompare(b.name, 'de'),
        ),
        aktivAnzahl: aktive.length,
        inaktivAnzahl: raeume.length - aktive.length,
        flaeche: summeApi(aktive.map((r) => r.floor_area_m2)),
        volumen: summeApi(aktive.map((r) => r.volume_m3)),
      };
    };

    const gruppen: RaumGruppe[] = [];
    for (const b of [...this.gebaeude()].sort((x, y) =>
      x.building_number.localeCompare(y.building_number, 'de'),
    )) {
      const gLabel = gebaeudeLabel(b);
      for (const u of [...b.units].sort((x, y) =>
        x.unit_number.localeCompare(y.unit_number, 'de'),
      )) {
        const rs = alle.filter((r) => r.unit_id === u.id);
        rs.forEach((r) => zugeordnet.add(r.id));
        gruppen.push(
          bauen(
            `u-${u.id}`,
            `${gLabel} · ${u.unit_number}`,
            unitTypeLabel(u.unit_type),
            b.id,
            u.id,
            rs,
          ),
        );
      }
      const allg = alle.filter((r) => r.building_id === b.id && r.unit_id == null);
      allg.forEach((r) => zugeordnet.add(r.id));
      gruppen.push(
        bauen(
          `b-${b.id}`,
          `${gLabel} · allgemein`,
          'Keller, Technik, Treppenhaus …',
          b.id,
          null,
          allg,
        ),
      );
    }

    const ohne = alle.filter((r) => !zugeordnet.has(r.id));
    // „Ohne Zuordnung" nur zeigen, wenn dort etwas liegt — oder wenn es gar keine
    // Struktur gibt (dann ist es die einzige Gruppe, in der man anlegen kann).
    if (ohne.length || gruppen.length === 0) {
      gruppen.push(bauen('ohne', 'Ohne Zuordnung', 'Altbestand ohne Gebäude/Einheit', null, null, ohne));
    }
    return gruppen;
  });

  /** Auslegungsdaten des Objekts — sie kommen mit dem Aufmaß. */
  protected readonly auslegung = computed<Auslegung | null>(() => {
    const a = this.aufmass();
    if (!a) return null;
    return {
      design_outdoor_temp_c: a.design_outdoor_temp_c,
      heat_load_w_per_m2: a.heat_load_w_per_m2,
    };
  });

  /**
   * Fehlt die Auslegungs-Außentemperatur, ist die Heizlast JEDES Raumes
   * unbekannt — und zwar nicht wegen des Raumes, sondern wegen des Objekts. Die
   * Raumliste muss den Weg dorthin zeigen, statt nur „unbekannt" zu sagen.
   */
  protected readonly aussentemperaturFehlt = computed(() => {
    const a = this.auslegung();
    return a != null && (a.design_outdoor_temp_c == null || a.design_outdoor_temp_c === '');
  });

  protected readonly gesamtFlaeche = computed(() =>
    summeApi(this.aktiveRaeume().map((r) => r.floor_area_m2)),
  );
  protected readonly gesamtVolumen = computed(() =>
    summeApi(this.aktiveRaeume().map((r) => r.volume_m3)),
  );

  constructor() {
    // Der Schalter „stillgelegte anzeigen" hängt mit drin: er ist kein Client-
    // Filter, sondern eine ANDERE Anfrage (der Server liefert sie sonst nicht).
    effect(() => {
      const id = this.propertyId();
      const mitInaktiven = this.mitInaktiven();
      if (id) this.laden(id, mitInaktiven);
    });
  }

  private laden(propertyId: string, mitInaktiven = this.mitInaktiven()): void {
    const rid = ++this.ladeReq;
    this.zustand.set('loading');
    this.fehler.set(null);
    this.nachladeFehler.set(null);
    this.svc.list(propertyId, mitInaktiven).subscribe({
      next: (rooms) => {
        if (rid !== this.ladeReq) return;
        this.raeume.set(rooms);
        this.zustand.set('ready');
        this.aufmassLaden(propertyId, rid);
      },
      error: (err) => {
        if (rid !== this.ladeReq) return;
        this.zustand.set('error');
        this.fehler.set(fehlerDetail(err) ?? 'Die Räume konnten nicht geladen werden.');
      },
    });
  }

  /**
   * Die Aufmaß-Summe ist eine ZWEITE Anfrage: scheitert sie, bleibt die Raumliste
   * trotzdem bedienbar (und die Summe zeigt ehrlich, dass sie fehlt) — auf der
   * Baustelle ist eine halb geladene Seite besser als gar keine.
   */
  private aufmassLaden(propertyId: string, rid: number): void {
    this.svc.aufmass(propertyId).subscribe({
      next: (a) => {
        if (rid === this.ladeReq) this.aufmass.set(a);
      },
      error: () => {
        if (rid === this.ladeReq) this.aufmass.set(null);
      },
    });
  }

  neuLaden(): void {
    this.laden(this.propertyId());
  }

  /**
   * Auslegungsdaten wurden gespeichert: **jede** Raum-Heizlast hängt daran, also
   * kommen die Kennzahlen frisch vom Server. Ohne Ladezustand — das Panel (und
   * seine Erfolgsmeldung) bleibt stehen, die Liste aktualisiert sich darunter.
   */
  auslegungGespeichert(): void {
    const propertyId = this.propertyId();
    const rid = ++this.ladeReq;
    this.nachladeFehler.set(null);
    this.svc.list(propertyId, this.mitInaktiven()).subscribe({
      next: (rooms) => {
        if (rid !== this.ladeReq) return;
        this.raeume.set(rooms);
        this.ansage.set('Auslegungsdaten gespeichert. Kennzahlen der Räume aktualisiert.');
      },
      error: (err) => {
        if (rid !== this.ladeReq) return;
        this.nachladeFehler.set(
          fehlerDetail(err) ??
            'Die Kennzahlen konnten nach dem Speichern nicht neu geholt werden. ' +
              'Die Liste zeigt den Stand von vorher.',
        );
      },
    });
    this.aufmassLaden(propertyId, rid);
  }

  /** Aus der „unbekannt"-Meldung heraus zu den Auslegungsdaten — Fokus mitnehmen. */
  zuAuslegung(): void {
    this.panel()?.fokus();
  }

  // --- Navigation zwischen Liste und Erfassung -----------------------------
  raumOeffnen(id: string): void {
    this.neuVorbelegung.set(null);
    this.offenerRaum.set(id);
  }

  /** Allgemeines „＋ Raum" (Kopf) — ohne Vorbelegung. */
  raumAnlegen(): void {
    this.neuVorbelegung.set(null);
    this.offenerRaum.set('neu');
  }

  /** „＋ Raum" an einer Gruppe — Gebäude/Einheit sind vorbelegt. */
  raumAnlegenIn(g: RaumGruppe): void {
    this.neuVorbelegung.set({ building_id: g.building_id, unit_id: g.unit_id });
    this.offenerRaum.set('neu');
  }

  zurueckZurListe(): void {
    this.offenerRaum.set(null);
    this.fokusAufListe();
  }

  /** Nach dem Speichern: Stand neu holen (Kennzahlen kommen vom Server). */
  gespeichert(raum: Room): void {
    const vorhanden = this.raeume().some((r) => r.id === raum.id);
    this.raeume.update((rs) =>
      vorhanden ? rs.map((r) => (r.id === raum.id ? raum : r)) : [...rs, raum],
    );
    this.offenerRaum.set(raum.id);
    this.ansage.set(`Raum „${raum.name}" gespeichert.`);
    this.aufmassLaden(this.propertyId(), this.ladeReq);
  }

  // --- Stilllegen / reaktivieren -------------------------------------------
  /**
   * **Gelöscht wird nie.** Ein Aufmaß ist ein Nachweis über den Bestand: Wird ein
   * Raum umgebaut oder fällt er weg (zwei Zimmer zusammengelegt), wird er
   * stillgelegt — er bleibt lesbar, zählt aber in keiner Summe mehr mit.
   */
  stilllegenFragen(r: Room): void {
    if (!this.darfAendern()) return;
    this.stillzulegen.set(r);
  }

  stilllegenAbbrechen(): void {
    if (this.statusLaeuft()) return;
    this.stillzulegen.set(null);
  }

  stilllegenBestaetigen(): void {
    const r = this.stillzulegen();
    if (!r || this.statusLaeuft()) return;
    this.statusSetzen(r, 'INAKTIV');
  }

  reaktivieren(r: Room): void {
    if (!this.darfAendern() || this.statusLaeuft()) return;
    this.statusSetzen(r, 'AKTIV');
  }

  private statusSetzen(r: Room, status: 'AKTIV' | 'INAKTIV'): void {
    this.statusLaeuft.set(true);
    this.nachladeFehler.set(null);
    this.svc.setStatus(r.id, status).subscribe({
      next: () => {
        this.statusLaeuft.set(false);
        this.stillzulegen.set(null);
        this.ansage.set(
          status === 'INAKTIV'
            ? `Raum „${r.name}" stillgelegt. Er zählt nicht mehr in die Summen.`
            : `Raum „${r.name}" wieder aktiv.`,
        );
        // Frisch vom Server: welche Räume sichtbar sind und was in die Summen
        // zählt, entscheidet er — nicht der Client.
        this.neuLaden();
      },
      error: (err) => {
        this.statusLaeuft.set(false);
        this.stillzulegen.set(null);
        this.nachladeFehler.set(
          fehlerDetail(err) ?? 'Der Status des Raumes konnte nicht geändert werden.',
        );
      },
    });
  }

  stillgelegt(r: Room): boolean {
    return istStillgelegt(r);
  }

  /**
   * Fokus zurück auf die Raumliste. `setTimeout` statt `queueMicrotask`: das
   * Ziel existiert erst, nachdem Angular gerendert hat — ein Microtask liefe
   * davor und der Fokus landete im Nichts (Tastaturbedienung).
   */
  private fokusAufListe(): void {
    setTimeout(() => document.getElementById('raum-liste-titel')?.focus(), 0);
  }

  // --- Darstellung ---------------------------------------------------------
  nutzung(r: Room): string {
    return roomTypeLabel(r.room_type);
  }

  /**
   * Zahl aus der API **mit Einheit** — `null` bleibt „unbekannt" (ohne Einheit)
   * und wird NIE zu 0.
   */
  wert(
    w: string | number | null | undefined,
    einheit: string,
    nachkomma = 2,
    unbekannt = 'unbekannt',
  ): string {
    return mitEinheit(w, einheit, nachkomma, unbekannt);
  }

  /** Lokal gerechnete Summe (Geometrie) — null heißt: nichts Bekanntes dabei. */
  summe(n: number | null, nachkomma = 2): string {
    return n == null ? '—' : zeige(n, nachkomma);
  }

  /** Heizlast in kW aus Watt — reine Anzeigeumrechnung, kein Rechnen von Fachwerten. */
  kw(watt: string | number | null | undefined): string {
    if (watt == null || watt === '') return 'unbekannt';
    const n = Number(watt);
    return Number.isFinite(n) ? `${zeige(n / 1000, 1)} kW` : 'unbekannt';
  }
}
