import { TestBed } from '@angular/core/testing';

import { ConfigResponse } from '../api/models';
import { DEMO_CONFIG, DRIVE_CONFIG } from '../testing/fixtures';
import { DemoBannerComponent } from './demo-banner.component';

describe('DemoBannerComponent', () => {
  function render(config: ConfigResponse | null) {
    const fixture = TestBed.createComponent(DemoBannerComponent);
    fixture.componentRef.setInput('config', config);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  it('shows the "Demo mode" banner when demo_mode is true', () => {
    const el = render(DEMO_CONFIG);
    const banner = el.querySelector('[data-testid=demo-banner]');
    expect(banner).not.toBeNull();
    expect(banner!.textContent).toContain('Demo mode');
    expect(banner!.textContent).toContain('./sample_docs');
    expect(banner!.textContent).toContain('fixture');
    expect(banner!.textContent).toContain('Ask is disabled');
  });

  it('hides the banner when demo_mode is false', () => {
    const el = render(DRIVE_CONFIG);
    expect(el.querySelector('[data-testid=demo-banner]')).toBeNull();
  });

  it('hides the banner while config is unknown', () => {
    const el = render(null);
    expect(el.querySelector('[data-testid=demo-banner]')).toBeNull();
  });

  it('omits the fixture note when extraction uses Claude', () => {
    const el = render({ ...DEMO_CONFIG, extraction_source: 'claude', features: DRIVE_CONFIG.features });
    const text = el.querySelector('[data-testid=demo-banner]')!.textContent!;
    expect(text).not.toContain('fixture');
    expect(text).not.toContain('Ask is disabled');
  });
});
