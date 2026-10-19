import { TestBed } from '@angular/core/testing';

import { DemoBannerComponent } from '../shell/demo-banner.component';
import { DEMO_CONFIG } from '../testing/fixtures';
import { ConfigResponse, DocumentItem, EXTRACTION_SOURCES, ExtractionRef, ExtractionSource } from './models';

/**
 * Pins wire values to the API contract (docs/progress.md -> API Contract), so a
 * spelling drift like BUG-Q2-01 (`'fixtures'` in the UI, `"fixture"` from the api)
 * fails here instead of silently hiding UI.
 */

// ---- compile time: every extraction_source field uses the one union -------------------
type Equal<A, B> = (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false;
type Expect<T extends true> = T;
export type ContractTypeChecks = [
  Expect<Equal<ConfigResponse['extraction_source'], ExtractionSource>>,
  Expect<Equal<NonNullable<DocumentItem['extraction_source']>, ExtractionSource>>,
  Expect<Equal<NonNullable<ExtractionRef['source']>, ExtractionSource>>,
  Expect<Equal<(typeof EXTRACTION_SOURCES)[number], ExtractionSource>>,
];

/**
 * `GET /api/config` contract example, verbatim as JSON text (C7/FR-14: demo mode with
 * no ANTHROPIC_API_KEY). Parsed untyped, the way it arrives over the wire, so the TS
 * types cannot paper over a mismatch.
 */
const CONTRACT_CONFIG_JSON = `{
  "demo_mode": true,
  "demo_reason": "GOOGLE_CLIENT_ID_UNSET",
  "extraction_source": "fixture",
  "claude_model": "claude-opus-5-5",
  "sync_interval_seconds": 30,
  "features": {
    "browse": {"enabled": true,  "reason": null},
    "search": {"enabled": true,  "reason": null},
    "review": {"enabled": true,  "reason": null},
    "ask":    {"enabled": false, "reason": "Ask is disabled because ANTHROPIC_API_KEY is not set."},
    "drive":  {"enabled": false, "reason": "Demo mode: documents are read from ./sample_docs instead of Google Drive."}
  }
}`;

describe('API contract: extraction_source (BUG-Q2-01)', () => {
  it('the TS union matches the contract values exactly', () => {
    // contract: `"fixture"` only in demo mode with no ANTHROPIC_API_KEY; otherwise `"claude"`
    expect([...EXTRACTION_SOURCES].sort()).toEqual(['claude', 'fixture']);
  });

  it('the contract example value is a known ExtractionSource', () => {
    const wire = JSON.parse(CONTRACT_CONFIG_JSON) as Record<string, unknown>;
    expect(EXTRACTION_SOURCES as readonly unknown[]).toContain(wire['extraction_source']);
  });

  it('the shared test fixture uses the wire spelling', () => {
    expect(DEMO_CONFIG.extraction_source).toBe('fixture');
    expect(JSON.parse(CONTRACT_CONFIG_JSON)).toEqual(DEMO_CONFIG);
  });

  it('the demo banner says values come from fixtures for the contract payload', () => {
    const fixture = TestBed.createComponent(DemoBannerComponent);
    fixture.componentRef.setInput('config', JSON.parse(CONTRACT_CONFIG_JSON));
    fixture.detectChanges();
    const text = (fixture.nativeElement as HTMLElement).querySelector('[data-testid=demo-banner]')!.textContent!;
    expect(text).toContain('Values come from committed fixture extractions.');
    expect(text).toContain('Ask is disabled.');
  });
});
