import { Component, ElementRef, inject, input, model } from '@angular/core';
import { RouterLink } from '@angular/router';

export interface MappeTab {
  id: string;
  label: string;
}

/**
 * Wiederverwendbare Detail-„Mappe" (Hero-Muster): Kopfbereich mit
 * Zurück-Link, Kicker, Titel und projizierter Kopf-Zone (Status/Aktionen),
 * darunter eine Tab-Leiste und der projizierte Inhalt des aktiven Tabs.
 *
 * Der Elternteil bindet den aktiven Tab zweiseitig ([(aktiv)]) und schaltet
 * die Inhalte per @if(aktiv() === '…') im projizierten Bereich um.
 */
@Component({
  selector: 'app-mappe',
  imports: [RouterLink],
  templateUrl: './mappe.html',
  styleUrl: './mappe.scss',
})
export class Mappe {
  readonly kicker = input('');
  readonly titel = input('');
  readonly backLink = input<string | null>(null);
  readonly backLabel = input('Zurück');
  readonly tabs = input<MappeTab[]>([]);
  readonly aktiv = model<string>('');

  private readonly host: ElementRef<HTMLElement> = inject(ElementRef);

  select(id: string): void {
    this.aktiv.set(id);
  }

  /** Tastaturnavigation nach WAI-ARIA Tabs-Pattern (automatische Aktivierung). */
  onTabKey(event: KeyboardEvent, index: number): void {
    const tabs = this.tabs();
    if (tabs.length === 0) return;
    let next = index;
    switch (event.key) {
      case 'ArrowRight':
      case 'ArrowDown':
        next = (index + 1) % tabs.length;
        break;
      case 'ArrowLeft':
      case 'ArrowUp':
        next = (index - 1 + tabs.length) % tabs.length;
        break;
      case 'Home':
        next = 0;
        break;
      case 'End':
        next = tabs.length - 1;
        break;
      default:
        return;
    }
    event.preventDefault();
    const id = tabs[next].id;
    this.aktiv.set(id);
    // Der Ziel-Button existiert bereits im DOM (roving tabindex -1 ist
    // programmatisch fokussierbar), daher direktes Fokussieren.
    this.host.nativeElement
      .querySelector<HTMLElement>(`#mappe-tab-${CSS.escape(id)}`)
      ?.focus();
  }
}
