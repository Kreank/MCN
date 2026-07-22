import { Component, input } from '@angular/core';
import { Dokumentkopf } from '../../core/beleg.model';

/**
 * Ein Beleg als **Blatt Papier** — die Hülle für jede Leseansicht eines
 * Dokuments (Befund G1).
 *
 * Sascha: „Es sieht in der Übersicht halt nicht aus wie ein Dokument, sondern
 * wie statisch auf der Seite eingebacken. Es sollte halt aussehen wie ein
 * Dokument und nicht wie fest auf der Seite."
 *
 * Was fehlte, war die **Papier-Metapher**. Eine Positionstabelle, die randlos in
 * der Seite klebt, ist eine Tabelle; erst Blatt, Briefkopf, Anschriftfeld und
 * Betreff machen daraus ein Schriftstück. Genau das trennt die Leseansicht vom
 * Editor: Im Editor bearbeitet man Daten, hier sieht man, was der Kunde bekommt.
 *
 * Bewusst **kein** eingebettetes PDF, obwohl das exakt wäre: Ein PDF im iframe
 * ist auf dem Handy kaum bedienbar, seine Textebene ist für Screenreader ein
 * Glücksspiel, und für Entwürfe müsste bei jeder Änderung neu gerendert werden.
 * WCAG 2.2 AA ist in diesem Projekt nicht verhandelbar. Das PDF bleibt daneben
 * erreichbar — es ist die Ausfertigung, dies hier ist die Ansicht.
 *
 * Die Komponente ist eine reine Hülle mit Inhaltsprojektion. Positionen, Summen
 * und Fußbereich liefert der Aufrufer über die Slots — damit teilen Angebot,
 * Rechnung und Baustellenbericht denselben Rahmen, ohne dass diese Komponente
 * je einen von ihnen kennen müsste (Dokumentenkonfigurator-Prinzip).
 *
 * Kein A4-Seitenumbruch und keine feste Millimeter-Geometrie: Der Bildschirm ist
 * kein Papier, und eine erzwungene Seitenhöhe erzeugt auf dem Handy nur
 * Zoom-Elend. Das Blatt hat eine Papierbreite als Maximum, ist darunter aber
 * flüssig — die Anmutung trägt, die Bedienbarkeit bleibt.
 */
@Component({
  selector: 'app-dokument-blatt',
  imports: [],
  templateUrl: './dokument-blatt.html',
  styleUrl: './dokument-blatt.scss',
})
export class DokumentBlatt {
  /** Absender-/Empfängerzeilen; null blendet den Briefkopf aus. */
  readonly kopf = input<Dokumentkopf | null>(null);
  /** Die große Zeile über den Positionen, z. B. „Angebot 2026-0042". */
  readonly betreff = input<string>('');
  /** Zeile darunter, z. B. der Angebotstitel. */
  readonly unterzeile = input<string>('');
  /**
   * Metablock rechts neben dem Anschriftfeld (Belegnummer, Datum, Objekt).
   * DIN 5008 nennt ihn Informationsblock; er steht rechts, damit das
   * Anschriftfeld links im Fensterkuvert sichtbar bleibt.
   */
  readonly meta = input<{ label: string; wert: string }[]>([]);
  /** Anschreiben über den Positionen (Freitext des Belegkopfs). */
  readonly anschreiben = input<string | null>(null);
  /**
   * Entwurfskennzeichnung. Zeigt einen Aufdruck wie im PDF — damit ein
   * Bildschirmfoto eines Entwurfs nicht mit einem gestellten Beleg verwechselt
   * werden kann.
   */
  readonly entwurf = input(false);
}
