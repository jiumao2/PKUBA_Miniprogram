import { describe, expect, it } from 'vitest';

import { isArchivedCorrectionConfirmed } from './api';

describe('archived scoresheet correction route', () => {
  it('binds explicit confirmation to exactly one document', () => {
    const search = '?archived_view=1&archived_correction=sheet-one';

    expect(isArchivedCorrectionConfirmed('sheet-one', search)).toBe(true);
    expect(isArchivedCorrectionConfirmed('sheet-two', search)).toBe(false);
    expect(isArchivedCorrectionConfirmed('sheet-one', '?archived_view=1')).toBe(false);
  });
});
