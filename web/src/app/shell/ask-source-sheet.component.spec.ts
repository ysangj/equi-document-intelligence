import { MAIN_MIN_WIDTH, maxPanelWidth } from './ask-panel.service';
import { SOURCE_SHEET_MIN_WIDTH, SOURCE_SHEET_WIDTH, sheetPlacement } from './ask-source-sheet.component';

describe('sheetPlacement (citation source sheet beside the docked Ask panel)', () => {
  it('default width: 380 px, flush against the panel', () => {
    expect(sheetPlacement(1440, 440)).toEqual({ mode: 'beside', right: 440, width: SOURCE_SHEET_WIDTH });
  });

  it('panel at its widest: still beside it, narrowed to the main area that is left', () => {
    for (const vw of [1024, 1100, 1440, 1920]) {
      const p = sheetPlacement(vw, maxPanelWidth(vw));
      expect(p.mode).toBe('beside');
      expect(p.right).toBe(maxPanelWidth(vw));
      expect(p.width).toBe(MAIN_MIN_WIDTH);
      // fully on screen
      expect(vw - p.right - p.width).toBeGreaterThanOrEqual(0);
    }
  });

  it('less room than the sheet minimum: overlays the panel instead, still fully on screen', () => {
    const p = sheetPlacement(1024, 1024 - (SOURCE_SHEET_MIN_WIDTH - 20));
    expect(p.mode).toBe('over');
    expect(p.width).toBe(SOURCE_SHEET_WIDTH);
    expect(p.right).toBe(1024 - SOURCE_SHEET_WIDTH);
    expect(1024 - p.right - p.width).toBe(0);
  });

  it('panel closed: the sheet sits at the right edge', () => {
    expect(sheetPlacement(1440, 0)).toEqual({ mode: 'beside', right: 0, width: SOURCE_SHEET_WIDTH });
  });
});
