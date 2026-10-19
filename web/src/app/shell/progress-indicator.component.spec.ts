import { TestBed } from '@angular/core/testing';

import { ProgressResponse } from '../api/models';
import { PROGRESS } from '../testing/fixtures';
import { ProgressIndicatorComponent, progressPercent, progressText } from './progress-indicator.component';

describe('progressText', () => {
  it('reads "processed / total documents processed" plus in-flight and failures', () => {
    expect(progressText(PROGRESS)).toBe('17 / 20 documents processed · 2 processing · 1 failed');
  });

  it('mentions queued and unsupported documents', () => {
    const p = { ...PROGRESS, counts: { ...PROGRESS.counts, pending: 3, failed: 0, unsupported: 1 } };
    expect(progressText(p)).toBe('17 / 20 documents processed · 2 processing · 3 queued · 1 unsupported');
  });

  it('shows just the ratio when everything is finished', () => {
    const p: ProgressResponse = {
      ...PROGRESS,
      counts: { pending: 0, processing: 0, done: 16, needs_review: 4, failed: 0, unsupported: 0 },
      processed: 20,
      finished: 20,
      in_flight: 0,
      active: false,
    };
    expect(progressText(p)).toBe('20 / 20 documents processed');
  });

  it('handles an empty folder', () => {
    expect(progressText({ ...PROGRESS, total: 0, processed: 0, finished: 0 })).toBe('No documents yet');
  });
});

describe('progressPercent', () => {
  it('uses finished / total for the bar width', () => {
    expect(progressPercent(PROGRESS)).toBe(90);
    expect(progressPercent({ ...PROGRESS, total: 0 })).toBe(0);
  });
});

describe('ProgressIndicatorComponent', () => {
  it('renders the text and bar width', () => {
    const fixture = TestBed.createComponent(ProgressIndicatorComponent);
    fixture.componentRef.setInput('progress', PROGRESS);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('17 / 20 documents processed');
    expect(el.querySelector('[role=progressbar]')!.getAttribute('aria-valuenow')).toBe('90');
    expect((el.querySelector('.bar i') as HTMLElement).style.width).toBe('90%');
  });

  it('shows an unobtrusive message when progress is unavailable', () => {
    const fixture = TestBed.createComponent(ProgressIndicatorComponent);
    fixture.componentRef.setInput('error', {
      status: 404,
      code: 'not_implemented',
      message: 'This endpoint is not available on the api yet.',
      details: null,
      envelope: false,
    });
    fixture.detectChanges();
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('Progress unavailable');
  });

  it('renders nothing before the first poll', () => {
    const fixture = TestBed.createComponent(ProgressIndicatorComponent);
    fixture.detectChanges();
    expect((fixture.nativeElement as HTMLElement).textContent!.trim()).toBe('');
  });
});
