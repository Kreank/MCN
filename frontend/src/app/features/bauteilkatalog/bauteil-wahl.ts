import { Component, computed, input, output } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Bauteil, ohneUWert } from '../../core/bauteilkatalog.model';
import { UHerkunft, uHerkunft, vorlagenWert } from './uwert-herkunft';

/**
 * Auswahlfeld „Bauteil" für eine Hüllfläche oder eine Öffnung im Raum-Editor —
 * plus die **Herkunftsanzeige des U-Werts**.
 *
 * Diese Komponente rechnet nichts und speichert nichts: sie meldet die gewählte
 * Vorlage nach oben (der Editor setzt `template_id` und belegt das U-Wert-Feld
 * vor) und sagt dem Bediener, woher der Wert stammt:
 *
 *  - **aus Vorlage** — er ist der Katalogwert (eine KOPIE: eine spätere
 *    Katalogänderung zieht dieses Aufmaß nicht nach),
 *  - **abweichend** — er wurde überschrieben (ein gemessener Wert schlägt die
 *    Vorlage; der Katalogwert steht daneben und ist mit einem Klick zurückholbar),
 *  - **U-Wert fehlt** — die Vorlage hat (noch) keinen. Das ist der
 *    Auslieferungszustand des Katalogs, kein Fehler — aber die Heizlast bleibt
 *    dann unbekannt, und das steht an Ort und Stelle, mit dem Weg zum Katalog.
 */
@Component({
  selector: 'app-bauteil-wahl',
  imports: [RouterLink],
  templateUrl: './bauteil-wahl.html',
  styleUrl: './bauteil-wahl.scss',
})
export class BauteilWahl {
  /** Die wählbaren Vorlagen — vom Editor bereits nach Gattung gefiltert. */
  readonly vorlagen = input.required<Bauteil[]>();
  readonly templateId = input<string | null>(null);
  /** Der U-Wert **so wie er im Eingabefeld steht** (deutsche Eingabeform). */
  readonly uWert = input('');
  /** Id des <select> — der Editor vergibt sie (Label-/Fokus-Bezug). */
  readonly feldId = input.required<string>();

  /** Vorlage gewählt (oder abgewählt: `null`). */
  readonly gewaehlt = output<Bauteil | null>();
  /** „Vorlagenwert übernehmen" — holt den Katalogwert zurück ins Feld. */
  readonly wertUebernehmen = output<Bauteil>();

  protected readonly katalogPfad = '/einstellungen/bauteilkatalog';

  protected readonly vorlage = computed<Bauteil | null>(
    () => this.vorlagen().find((v) => v.id === this.templateId()) ?? null,
  );

  /**
   * Erfasst mit einer Vorlage, die es im aktiven Katalog nicht mehr gibt (sie
   * wurde stillgelegt). Der erfasste U-Wert bleibt trotzdem gültig — er ist eine
   * Kopie. Das Feld darf hier NICHT still auf „ohne Vorlage" zurückfallen.
   */
  protected readonly stillgelegteVorlage = computed(() => !!this.templateId() && !this.vorlage());

  protected readonly herkunft = computed<UHerkunft>(() => uHerkunft(this.vorlage(), this.uWert()));

  /** Der U-Wert der gewählten Vorlage in deutscher Anzeige — oder null. */
  protected readonly katalogWert = computed<string | null>(() => {
    const v = this.vorlage();
    return v ? vorlagenWert(v) : null;
  });

  protected ohne(v: Bauteil): boolean {
    return ohneUWert(v);
  }

  protected waehlen(id: string): void {
    this.gewaehlt.emit(this.vorlagen().find((v) => v.id === id) ?? null);
  }

  protected uebernehmen(): void {
    const v = this.vorlage();
    if (v) this.wertUebernehmen.emit(v);
  }
}
