import { Component, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Dialog } from './dialog';

/**
 * Der Dialog darf Eingaben nicht durch einen Fehlklick verlieren.
 *
 * Saschas Befund beim Testen: *„Wenn ich mich verklicke, wird das Formular
 * geschlossen. Total uncool, wenn man dabei ist etwas anzulegen."* Diese Tests
 * halten die Auflösung fest — sie ist subtil genug, um bei der nächsten
 * Änderung an der Dialoghülle unbemerkt zu verschwinden:
 *
 * * **unberührt** → Klick daneben und Escape schließen wie bisher. Es gibt
 *   nichts zu verlieren, und ein Bestätigungsklick wäre reine Schikane.
 * * **mit Eingaben** → der Klick daneben schließt **nicht** (Versehen), Escape
 *   und der X-Knopf **fragen** (Absicht). Escape bleibt damit ein Weg hinaus
 *   (WCAG 2.1.2), nur eben ein bestätigter.
 */
@Component({
  imports: [Dialog],
  template: `
    <app-dialog [offen]="offen()" titel="Testdialog" (schliessen)="schliessenZaehler = schliessenZaehler + 1">
      <input id="feld" type="text" />
    </app-dialog>
  `,
})
class Wirt {
  readonly offen = signal(true);
  schliessenZaehler = 0;
}

describe('Dialog — Schutz vor Datenverlust', () => {
  let fixture: ComponentFixture<Wirt>;
  let wirt: Wirt;

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [Wirt] }).compileComponents();
    fixture = TestBed.createComponent(Wirt);
    wirt = fixture.componentInstance;
    fixture.detectChanges();
    await fixture.whenStable();
  });

  /** Das native <dialog>-Element selbst ist die abgedunkelte Fläche. */
  function backdrop(): HTMLElement {
    return fixture.nativeElement.querySelector('dialog') as HTMLElement;
  }

  function tippen(): void {
    const feld = fixture.nativeElement.querySelector('#feld') as HTMLInputElement;
    feld.value = 'halb erfasst';
    feld.dispatchEvent(new Event('input', { bubbles: true }));
    fixture.detectChanges();
  }

  function klickDaneben(): void {
    // Klick auf das <dialog> selbst = neben das Panel (der Handler prüft genau das).
    backdrop().dispatchEvent(new MouseEvent('click', { bubbles: true }));
    fixture.detectChanges();
  }

  function escape(): void {
    backdrop().dispatchEvent(new Event('cancel', { bubbles: false, cancelable: true }));
    fixture.detectChanges();
  }

  function xKnopf(): HTMLButtonElement {
    return fixture.nativeElement.querySelector('.dialog__x') as HTMLButtonElement;
  }

  function frageSichtbar(): boolean {
    return !!fixture.nativeElement.querySelector('.dialog__frage');
  }

  it('schließt beim Klick daneben, solange nichts eingegeben wurde', () => {
    klickDaneben();
    expect(wirt.schliessenZaehler).toBe(1);
  });

  it('schließt NICHT beim Klick daneben, sobald etwas eingegeben wurde', () => {
    tippen();
    klickDaneben();
    expect(wirt.schliessenZaehler).toBe(0);
    // Statt zu schließen sagt der Dialog, warum er offen bleibt.
    const hinweis = fixture.nativeElement.querySelector('.dialog__schutz') as HTMLElement;
    expect(hinweis.textContent?.trim().length).toBeGreaterThan(0);
    // Und er fragt hier NICHT — ein Versehen verdient keine Rückfrage.
    expect(frageSichtbar()).toBe(false);
  });

  it('schließt bei Escape, solange nichts eingegeben wurde', () => {
    escape();
    expect(wirt.schliessenZaehler).toBe(1);
  });

  it('fragt bei Escape nach, sobald etwas eingegeben wurde', () => {
    tippen();
    escape();
    expect(wirt.schliessenZaehler).toBe(0);
    expect(frageSichtbar()).toBe(true);
  });

  it('„Verwerfen“ in der Rückfrage schließt wirklich', () => {
    tippen();
    escape();
    const verwerfen = fixture.nativeElement.querySelector(
      '.dialog__frage .btn--gefahr',
    ) as HTMLButtonElement;
    verwerfen.click();
    fixture.detectChanges();
    expect(wirt.schliessenZaehler).toBe(1);
  });

  it('„Weiter bearbeiten“ nimmt die Rückfrage zurück und lässt den Dialog stehen', () => {
    tippen();
    escape();
    const weiter = fixture.nativeElement.querySelector(
      '.dialog__frage .btn--primary',
    ) as HTMLButtonElement;
    weiter.click();
    fixture.detectChanges();
    expect(wirt.schliessenZaehler).toBe(0);
    expect(frageSichtbar()).toBe(false);
  });

  it('Escape bei offener Rückfrage nimmt die Frage zurück, statt zu schließen', () => {
    // Sonst wäre die Frage „verwerfen?" mit derselben Taste beantwortbar, die
    // sie ausgelöst hat — zweimal Escape löschte die Eingaben doch.
    tippen();
    escape();
    escape();
    expect(wirt.schliessenZaehler).toBe(0);
    expect(frageSichtbar()).toBe(false);
  });

  it('der X-Knopf fragt ebenfalls nach', () => {
    tippen();
    xKnopf().click();
    fixture.detectChanges();
    expect(wirt.schliessenZaehler).toBe(0);
    expect(frageSichtbar()).toBe(true);
  });

  it('der X-Knopf schließt ohne Eingaben sofort', () => {
    xKnopf().click();
    fixture.detectChanges();
    expect(wirt.schliessenZaehler).toBe(1);
  });
});
