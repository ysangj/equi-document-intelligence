import { TestBed } from '@angular/core/testing';

import { ReviewActionRequest, ReviewItem } from '../../api/models';
import { SourceRef } from '../../shared/source-loader';
import {
  ACCOUNT_ALIAS_CANONICAL,
  CONTRACT_REVIEW_LIST,
  CONTRACT_REVIEW_STATEMENT,
  NORTHWIND_ALIAS_CANONICAL,
  NORTHWIND_JAN_3,
} from '../../testing/fixtures';
import { ReviewItemComponent } from './review-item.component';

const [CONFLICT, ALIAS, YTD] = CONTRACT_REVIEW_LIST.items;

describe('ReviewItemComponent', () => {
  async function render(item: ReviewItem, entities: { id: number; name: string }[] = []) {
    const fixture = TestBed.createComponent(ReviewItemComponent);
    fixture.componentRef.setInput('item', item);
    fixture.componentRef.setInput('entities', entities);
    const acts: ReviewActionRequest[] = [];
    const sources: SourceRef[] = [];
    fixture.componentInstance.act.subscribe((a) => acts.push(a));
    fixture.componentInstance.source.subscribe((s) => sources.push(s));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const q = (id: string) => el.querySelector(`[data-testid=${id}]`) as HTMLButtonElement | null;
    return { fixture, el, acts, sources, q };
  }

  const buttons = (el: HTMLElement) =>
    ['accept', 'edit', 'choose', 'confirm-alias', 'reject-alias', 'make-canonical'].filter((id) => el.querySelector(`[data-testid=${id}]`));

  it('value item: only Accept and Edit', async () => {
    const { el } = await render(YTD);
    expect(buttons(el)).toEqual(['accept', 'edit']);
  });

  it('conflict item: Choose per candidate and Edit, no Accept', async () => {
    const { el } = await render(CONFLICT);
    expect(buttons(el)).toEqual(['edit', 'choose']);
    expect(Array.from(el.querySelectorAll('[data-testid=choose]')).map((b) => b.textContent!.trim())).toEqual(['Use +1.92%', 'Use +1.89%']);
  });

  it('conflict item: groups agreeing candidates before choosing (2 sources vs 1 source)', async () => {
    const { el, acts } = await render(NORTHWIND_JAN_3);
    const groups = Array.from(el.querySelectorAll('[data-testid=candidate-group]')).map((g) => g.textContent!.replace(/\s+/g, ' ').trim());
    expect(groups).toEqual(['+1.92% (2 sources)', '+1.89% (1 source)']);
    expect(el.querySelector('[data-testid=choose-hint]')!.textContent).toContain('keeps the sources that agree');
    const agrees = (id: number) => el.querySelector(`[data-candidate="${id}"] [data-testid=agrees]`)?.textContent?.trim() ?? null;
    expect([agrees(214), agrees(219), agrees(231)]).toEqual(['agrees with 1 other', 'agrees with 1 other', null]);
    const choose = Array.from(el.querySelectorAll('[data-testid=choose]')) as HTMLButtonElement[];
    expect(choose.map((b) => b.title)).toEqual([
      'Use this value; keeps 1 agreeing source, removes 1 with a different value.',
      'Use this value; keeps 1 agreeing source, removes 1 with a different value.',
      'Use this value; removes 2 with a different value.',
    ]);
    expect(el.querySelector('[data-reason=conflict]')!.textContent).toContain('3 documents report 2 different values');
    choose[0].click();
    expect(acts).toEqual([{ action: 'choose', chosen_id: 214 }]);
  });

  it('conflict item: no grouping hint when every candidate has its own value', async () => {
    const { el } = await render(CONFLICT);
    expect(el.querySelectorAll('[data-testid=candidate-group]').length).toBe(2);
    expect(el.querySelector('[data-testid=choose-hint]')).toBeNull();
    expect(el.querySelector('[data-testid=agrees]')).toBeNull();
  });

  it('value item: snippet_not_found with value_missing reads "Snippet found, but it doesn\'t contain the value"', async () => {
    const item: ReviewItem = {
      ...YTD,
      review_reasons: ['snippet_not_found'],
      reasons: [{ code: 'snippet_not_found', label: 'Snippet not found', message: 'The value is not written in its source snippet, so it cannot be traced.' }],
      detail: { snippet_not_found: { snippet: 'Alder Asia Fund  Q1', value_missing: true } },
    };
    const { el } = await render(item);
    expect(el.querySelector('[data-testid=reason-chip]')!.textContent!.trim()).toBe('Value not in snippet');
    const why = el.querySelector('[data-reason=snippet_not_found]')!.textContent!;
    expect(why).toContain("Snippet found, but it doesn't contain the value");
    expect(why).not.toContain('not found in');
  });

  it('alias item: only Same fund / Different fund', async () => {
    const { el } = await render(ALIAS);
    expect(buttons(el)).toEqual(['confirm-alias', 'reject-alias']);
    expect(el.querySelector('[data-testid=confirm-alias]')!.textContent!.trim()).toBe('Same fund');
  });

  it('shows the reason chips and human explanation', async () => {
    const { el } = await render(CONTRACT_REVIEW_STATEMENT);
    expect(Array.from(el.querySelectorAll('[data-testid=reason-chip]')).map((c) => c.textContent)).toEqual(['Roll-forward mismatch']);
    expect(el.querySelector('[data-testid=item-why]')!.textContent).toContain('= expected 13,080,000.00 USD');
    expect(el.querySelector('[data-testid=rollforward-table]')!.textContent).toContain('Stated closing');
  });

  it('Accept emits {action: accept}, with the optional note', async () => {
    const { fixture, el, acts, q } = await render(YTD);
    q('accept')!.click();
    expect(acts).toEqual([{ action: 'accept' }]);
    (Array.from(el.querySelectorAll('button')).find((b) => b.textContent!.trim() === 'Add note') as HTMLButtonElement).click();
    await fixture.whenStable();
    const note = el.querySelector('[data-testid=note]') as HTMLInputElement;
    note.value = 'checked the PDF';
    note.dispatchEvent(new Event('input'));
    q('accept')!.click();
    expect(acts[1]).toEqual({ action: 'accept', note: 'checked the PDF' });
  });

  it('Choose emits the candidate id', async () => {
    const { el, acts } = await render(CONFLICT);
    (el.querySelectorAll('[data-testid=choose]')[1] as HTMLButtonElement).click();
    expect(acts).toEqual([{ action: 'choose', chosen_id: 231 }]);
  });

  it('Edit validates inline and emits value_pct', async () => {
    const { fixture, el, acts, q } = await render(YTD);
    q('edit')!.click();
    await fixture.whenStable();
    const input = el.querySelector('[data-testid=edit-value]') as HTMLInputElement;
    expect(input.value).toBe('0.15'); // prefilled with the current value_pct
    input.value = 'twelve';
    input.dispatchEvent(new Event('input'));
    await fixture.whenStable();
    expect(el.querySelector('[data-testid=edit-error]')!.textContent).toContain('Enter a percentage');
    expect((el.querySelector('[data-testid=edit-save]') as HTMLButtonElement).disabled).toBe(true);
    input.value = '-0.15';
    input.dispatchEvent(new Event('input'));
    await fixture.whenStable();
    (el.querySelector('[data-testid=edit-save]') as HTMLButtonElement).click();
    expect(acts).toEqual([{ action: 'edit', value_pct: -0.15 }]);
  });

  it('Edit on a statement picks the field and emits field + value', async () => {
    const { fixture, el, acts, q } = await render(CONTRACT_REVIEW_STATEMENT);
    q('edit')!.click();
    await fixture.whenStable();
    const select = el.querySelector('[data-testid=edit-field]') as HTMLSelectElement;
    expect(select.options[select.selectedIndex].textContent).toBe('Closing balance');
    (Array.from(el.querySelectorAll('.sugg .chip')).find((b) => b.textContent!.includes('Use expected')) as HTMLButtonElement).click();
    await fixture.whenStable();
    (el.querySelector('[data-testid=edit-save]') as HTMLButtonElement).click();
    expect(acts).toEqual([{ action: 'edit', field: 'closing_balance', value: 13080000 }]);
  });

  it('statement item shows the period dates, each opening its source', async () => {
    const { el, sources } = await render(CONTRACT_REVIEW_STATEMENT);
    const period = el.querySelector('[data-testid=statement-period]')!;
    expect(period.textContent!.replace(/\s+/g, ' ').trim()).toMatch(/^Statement period .+2026 – .+2026$/);
    const [start, end] = Array.from(period.querySelectorAll('button')) as HTMLButtonElement[];
    expect(start.dataset['field']).toBe('period_start');
    end.click();
    expect(sources).toEqual([{ table: 'statements', id: 12, field: 'period_end' }]);
    expect((await render(YTD)).el.querySelector('[data-testid=statement-period]')).toBeNull();
  });

  it('Edit a statement period date uses a date input and emits field + YYYY-MM-DD', async () => {
    const { fixture, el, acts, q } = await render(CONTRACT_REVIEW_STATEMENT);
    q('edit')!.click();
    await fixture.whenStable();
    const select = el.querySelector('[data-testid=edit-field]') as HTMLSelectElement;
    const i = Array.from(select.options).findIndex((o) => o.textContent === 'Period end');
    expect(i).toBeGreaterThan(-1);
    select.value = String(i);
    select.dispatchEvent(new Event('change'));
    await fixture.whenStable();
    const input = el.querySelector('[data-testid=edit-value]') as HTMLInputElement;
    expect(input.getAttribute('type')).toBe('date');
    expect(input.value).toBe('2026-01-31');
    input.value = '2026-01-30';
    input.dispatchEvent(new Event('input'));
    await fixture.whenStable();
    (el.querySelector('[data-testid=edit-save]') as HTMLButtonElement).click();
    expect(acts).toEqual([{ action: 'edit', field: 'period_end', value: '2026-01-30' }]);
    // back to a money field: a text input again
    select.value = String(Array.from(select.options).findIndex((o) => o.textContent === 'Fees'));
    select.dispatchEvent(new Event('change'));
    await fixture.whenStable();
    expect(input.getAttribute('type')).toBe('text');
    expect(input.value).toBe('0');
  });

  it('Different fund: new fund by default, or an existing one', async () => {
    const { fixture, el, acts, q } = await render(ALIAS, [
      { id: 4, name: 'Alder Asia Fund' },
      { id: 5, name: 'Alder Dubai Fund' },
    ]);
    q('reject-alias')!.click();
    await fixture.whenStable();
    expect(el.querySelector('[data-testid=reassign]')!.textContent).toContain('New fund named “Alder Asia Fund SPC – Class A”');
    // the suggested fund itself is not offered as "existing"
    expect(Array.from((el.querySelector('[data-testid=reassign-select]') as HTMLSelectElement).options).map((o) => o.textContent)).toEqual(['Alder Dubai Fund']);
    q('reject-confirm')!.click();
    expect(acts[0]).toEqual({ action: 'reject_alias', reassign_to_id: null });
    (el.querySelectorAll('[data-testid=reassign] input[type=radio]')[1] as HTMLInputElement).click();
    await fixture.whenStable();
    q('reject-confirm')!.click();
    expect(acts[1]).toEqual({ action: 'reject_alias', reassign_to_id: 5 });
  });

  it('Same fund emits confirm_alias', async () => {
    const { acts, q } = await render(ALIAS);
    q('confirm-alias')!.click();
    expect(acts).toEqual([{ action: 'confirm_alias' }]);
  });

  it('"Use as main name" is hidden unless make_canonical is allowed', async () => {
    const { el } = await render(ALIAS); // contract example: confirm/reject only
    expect(buttons(el)).toEqual(['confirm-alias', 'reject-alias']);
    const noValue = await render({ ...YTD, allowed_actions: ['accept', 'edit', 'make_canonical'] });
    expect(buttons(noValue.el)).toEqual(['accept', 'edit']);
  });

  it('"Use as main name" sits next to Same/Different fund, explains itself and emits exactly make_canonical', async () => {
    const { el, acts, q, fixture } = await render(NORTHWIND_ALIAS_CANONICAL);
    expect(buttons(el)).toEqual(['confirm-alias', 'reject-alias', 'make-canonical']);
    const btn = q('make-canonical')!;
    expect(btn.textContent!.trim()).toBe('Use as main name');
    expect(btn.title).toBe("Makes 'Northwind Global Macro Fund' the fund's display name everywhere; 'Northwind Macro Fund Ltd' is kept as an alias.");
    // a typed note is not sent: the contract body has no other fields
    (el.querySelector('.btn.link.small') as HTMLButtonElement).click();
    await fixture.whenStable();
    const note = q('note') as unknown as HTMLInputElement;
    note.value = 'factsheet name';
    note.dispatchEvent(new Event('input'));
    btn.click();
    expect(acts).toEqual([{ action: 'make_canonical' }]);
    fixture.componentRef.setInput('busy', true);
    await fixture.whenStable();
    expect(q('make-canonical')!.disabled).toBe(true);
  });

  it('account alias: "Use as main name" speaks of the account', async () => {
    const { el, acts, q } = await render(ACCOUNT_ALIAS_CANONICAL);
    expect(buttons(el)).toEqual(['confirm-alias', 'reject-alias', 'make-canonical']);
    expect(q('confirm-alias')!.textContent!.trim()).toBe('Same account');
    expect(q('make-canonical')!.title).toBe("Makes 'Acme Family Office' the account's display name everywhere; 'Acme Family Office LLC' is kept as an alias.");
    q('make-canonical')!.click();
    expect(acts).toEqual([{ action: 'make_canonical' }]);
  });

  it('every value opens the source panel', async () => {
    const ytd = await render(YTD);
    (ytd.q('current-value') as HTMLButtonElement).click();
    (ytd.el.querySelector('[data-testid=ytd-table] .link-val') as HTMLButtonElement).click();
    expect(ytd.sources).toEqual([
      { table: 'returns', id: 188, field: null },
      { table: 'returns', id: 175, field: null },
    ]);
    const st = await render(CONTRACT_REVIEW_STATEMENT);
    (st.el.querySelector('[data-testid=rollforward-table] .link-val') as HTMLButtonElement).click();
    expect(st.sources).toEqual([{ table: 'statements', id: 12, field: 'opening_balance' }]);
  });

  it('shows an action error from the C2 envelope', async () => {
    const { fixture, el } = await render(YTD);
    fixture.componentRef.setInput('error', {
      status: 422,
      code: 'invalid_action',
      message: "Action 'choose' is not allowed for a value item; allowed: accept, edit.",
      details: [{ loc: 'body.action', message: "not allowed for kind 'value'" }],
      envelope: true,
    });
    await fixture.whenStable();
    const err = el.querySelector('[data-testid=item-error]')!.textContent!;
    expect(err).toContain("Action 'choose' is not allowed");
    expect(err).toContain("body.action: not allowed for kind 'value'");
  });
});
