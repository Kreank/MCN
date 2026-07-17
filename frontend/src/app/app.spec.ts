import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { provideHttpClient } from '@angular/common/http';
import { App } from './app';

// jsdom kennt matchMedia nicht — fuer ThemeService.init() minimal bereitstellen.
beforeAll(() => {
  if (!window.matchMedia) {
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;
  }
});

describe('App', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [provideRouter([]), provideHttpClient()],
    }).compileComponents();
  });

  it('should create the app', () => {
    const fixture = TestBed.createComponent(App);
    expect(fixture.componentInstance).toBeTruthy();
  });

  it('should render the Mitra Sanitär wordmark and Leitstand context', async () => {
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('.header__product')?.textContent).toContain('Mitra Sanitär');
    expect(compiled.querySelector('.header__context')?.textContent).toContain('Leitstand');
  });

  it('should expose an accessible theme toggle', () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    const toggle = (fixture.nativeElement as HTMLElement).querySelector('.theme-toggle');
    expect(toggle?.getAttribute('aria-pressed')).toBe('false');
  });
});
