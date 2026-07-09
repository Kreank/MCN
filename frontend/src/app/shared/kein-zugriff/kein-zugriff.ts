import { Component, input } from '@angular/core';
import { RouterLink } from '@angular/router';

/**
 * „Keine Berechtigung"-Hinweis. Zwei Verwendungen:
 *  - als eigene Route /kein-zugriff (Rechte-Guard leitet hierher um),
 *  - eingebettet in Feature-Seiten für den 403-Fall (mit Servermeldung).
 *
 * Bewusst OHNE „Erneut versuchen" — Wiederholen ändert an fehlenden Rechten
 * nichts. Der Weg zurück führt in einen Bereich, den der Nutzer sehen darf.
 */
@Component({
  selector: 'app-kein-zugriff',
  imports: [RouterLink],
  templateUrl: './kein-zugriff.html',
  styleUrl: './kein-zugriff.scss',
})
export class KeinZugriff {
  /** Optionale detail-Meldung des Servers (nur im eingebetteten 403-Fall). */
  readonly detail = input<string | null>(null);
}
