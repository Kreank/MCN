import { Component, computed, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { AuthService } from '../../core/auth.service';

/**
 * „Mein Bereich" — der persönliche Bereich des angemeldeten Mitarbeiters.
 *
 * Sascha (Befund E6): „Wir sollten es vielleicht nicht ‚Meine Zeiten' nennen
 * sondern Persönlicher Bereich/Mein Bereich."
 *
 * Der Name war nicht bloß Kosmetik: Unter „Meine Zeiten" stand die Stempeluhr,
 * daneben lag verwaist „Meine Personalakte" — zwei Punkte für eine Sache. Wer
 * seinen Urlaub beantragen wollte, hatte keinen Ort dafür, weil beide Namen von
 * etwas Engerem sprachen als dem, was gemeint ist: **alles, was mich selbst
 * betrifft.**
 *
 * Diese Komponente ist bewusst nur der Rahmen. Sie hält die Reiter und den
 * Router-Outlet; die Inhalte bleiben die gewachsenen, eigenständigen Seiten
 * (Stempeluhr, Personalakte). Der Rahmen erbt kein Recht und prüft keines — das
 * tun die Kindrouten, die unterschiedliche Rechte verlangen (`hr/AENDERN` fürs
 * Stempeln, `hr/LESEN` für die Akte).
 *
 * WCAG 2.2 AA: Die Reiter sind echte Links in einer `<nav>` mit Beschriftung;
 * der aktive Reiter trägt `aria-current="page"` und ist zusätzlich zur Farbe an
 * Gewicht und Unterstrich erkennbar.
 */
@Component({
  selector: 'app-mein-bereich',
  imports: [RouterLink, RouterLinkActive, RouterOutlet],
  templateUrl: './mein-bereich.html',
  styleUrl: './mein-bereich.scss',
})
export class MeinBereich {
  private readonly auth = inject(AuthService);

  /**
   * Die Stempeluhr verlangt `hr/AENDERN`. Wer nur `hr/LESEN` trägt (etwa eine
   * reine Bürokraft ohne eigene Zeiterfassung), bekommt den Reiter gar nicht
   * erst zu sehen — sonst führte ein sichtbarer Reiter zwangsläufig auf „Kein
   * Zugriff", und mit ihm verschwände der Rahmen samt zweitem Reiter.
   *
   * `darf` statt `darfAlle`: Der Stempel-Endpunkt wertet den Scope aus und
   * bucht immer auf den Akteur — row_scope EIGENE ist hier genau richtig.
   */
  protected readonly darfStempeln = computed(() => this.auth.darf('hr', 'AENDERN'));

  /**
   * Verlauf und Personalakte verlangen `hr/LESEN`. Ein Reiter, der beim Klick
   * am Wächter abprallt, wäre eine Aussage über die Sichtbarkeit statt über die
   * Wirkung — dieselbe Regel wie beim Stempel-Reiter nebenan.
   */
  protected readonly darfLesen = computed(() => this.auth.darf('hr', 'LESEN'));
}
