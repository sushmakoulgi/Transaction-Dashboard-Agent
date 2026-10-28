import { describe, expect, it } from 'vitest'

import { replySummary } from './replySummary'

describe('replySummary', () => {
  it('keeps a short plain reply as it is', () => {
    expect(replySummary('40 transactions have chargebacks.')).toBe('40 transactions have chargebacks.')
  })

  it('uses the first prose paragraph and strips the markup', () => {
    const reply = [
      'I found **4 Rogers transactions with a chargeback** in 2026. I searched for `merchant_name`.',
      '',
      '| Date | Transaction ID |',
      '|---|---|',
      '| 2026-10-21 | 250741448879065 |',
      '',
      '**What stands out:** two are still open.',
    ].join('\n')
    expect(replySummary(reply)).toBe(
      'I found 4 Rogers transactions with a chargeback in 2026. I searched for merchant_name.',
    )
  })

  it('skips leading lists and drops a table that starts without a blank line', () => {
    const reply = '- first\n- second\n\nBoth refunds are *done*. Together they come to **CAD 15.28**:\n| a | b |\n|---|---|'
    expect(replySummary(reply)).toBe('Both refunds are done. Together they come to CAD 15.28.')
  })

  it('cuts a long paragraph at a sentence end, or else at a word with an ellipsis', () => {
    const sentence = 'This sentence is about forty-five characters. '
    const atSentence = replySummary(sentence.repeat(10))
    expect(atSentence.endsWith('characters.')).toBe(true)
    expect(atSentence.length).toBeLessThanOrEqual(220)

    const oneLongSentence = replySummary('word '.repeat(80))
    expect(oneLongSentence.endsWith('word…')).toBe(true)
    expect(oneLongSentence.length).toBeLessThanOrEqual(221)
  })

  it('returns nothing when the reply has no prose', () => {
    expect(replySummary('| a |\n|---|\n| 1 |')).toBe('')
  })
})
