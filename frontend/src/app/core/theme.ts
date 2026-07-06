import { Injectable, signal } from '@angular/core';

export type ThemeMode = 'light' | 'dark';
const STORAGE_KEY = 'mcn-theme';

/**
 * Theme-Steuerung: prefers-color-scheme als Default, expliziter Override per
 * Toggle, persistiert in localStorage. Setzt data-theme am :root.
 */
@Injectable({ providedIn: 'root' })
export class ThemeService {
  /** Aktuell wirksames Theme (auch wenn es dem System folgt). */
  readonly theme = signal<ThemeMode>('light');
  /** True, sobald der Nutzer bewusst gewaehlt hat (nicht mehr Systemfolge). */
  private explicit = false;
  private media?: MediaQueryList;

  init(): void {
    const stored = this.read();
    this.media = window.matchMedia('(prefers-color-scheme: dark)');
    if (stored) {
      this.explicit = true;
      this.apply(stored);
    } else {
      this.apply(this.media.matches ? 'dark' : 'light');
    }
    // Systemwechsel nur folgen, solange keine explizite Wahl vorliegt.
    this.media.addEventListener('change', (e) => {
      if (!this.explicit) {
        this.apply(e.matches ? 'dark' : 'light');
      }
    });
  }

  toggle(): void {
    this.explicit = true;
    const next: ThemeMode = this.theme() === 'dark' ? 'light' : 'dark';
    this.apply(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* Speicher nicht verfuegbar — Wahl gilt nur fuer diese Sitzung. */
    }
  }

  private apply(mode: ThemeMode): void {
    this.theme.set(mode);
    document.documentElement.setAttribute('data-theme', mode);
  }

  private read(): ThemeMode | null {
    try {
      const v = localStorage.getItem(STORAGE_KEY);
      return v === 'dark' || v === 'light' ? v : null;
    } catch {
      return null;
    }
  }
}
