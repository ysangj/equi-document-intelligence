import { readMockFlag } from './mock-flag';

describe('readMockFlag', () => {
  const dev = { production: false, mock: false };

  beforeEach(() => sessionStorage.clear());

  it('is OFF by default', () => {
    expect(readMockFlag('', dev)).toBe(false);
    expect(readMockFlag('?view=statements', dev)).toBe(false);
  });

  it('turns on with ?mock=1 and remembers it for the tab', () => {
    expect(readMockFlag('?mock=1', dev)).toBe(true);
    expect(readMockFlag('', dev)).toBe(true);
  });

  it('turns off with ?mock=0', () => {
    readMockFlag('?mock=1', dev);
    expect(readMockFlag('?mock=0', dev)).toBe(false);
    expect(readMockFlag('', dev)).toBe(false);
  });

  it('is always on with the mock environment (npm run start:mock)', () => {
    expect(readMockFlag('', { production: false, mock: true })).toBe(true);
  });

  it('ignores ?mock=1 in production builds', () => {
    expect(readMockFlag('?mock=1', { production: true, mock: false })).toBe(false);
  });
});
