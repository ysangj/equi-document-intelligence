import { MATERIAL_ANIMATIONS } from '@angular/material/core';

/** Material dialogs/menus close synchronously in tests (no CSS animation to wait for). */
export const NO_MATERIAL_ANIMATIONS = { provide: MATERIAL_ANIMATIONS, useValue: { animationsDisabled: true } };

/** Click a button inside the open confirm dialog (rendered in the CDK overlay, outside the fixture). */
export function clickDialog(testId: 'confirm-ok' | 'confirm-cancel'): void {
  const btn = document.querySelector<HTMLButtonElement>(`[data-testid=${testId}]`);
  if (!btn) throw new Error('confirm dialog is not open');
  btn.click();
}

export function dialogText(): string | null {
  return document.querySelector('[data-testid=confirm-dialog]')?.textContent ?? null;
}

/** Let promise chains (firstValueFrom(afterClosed), router navigation) settle. */
export async function flushAsync(rounds = 5): Promise<void> {
  for (let k = 0; k < rounds; k++) await new Promise((r) => setTimeout(r, 0));
}
