// A short plain-text version of Claude's reply for the summary bar. The full Markdown reply is
// in the chat; here only its first prose paragraph is shown, without tables, lists or markup.
const MAX_SUMMARY_LENGTH = 220
const NOT_PROSE = /^(\||>|#|[-*+]\s|\d+[.)]\s)/

export function replySummary(reply: string): string {
  const paragraph =
    reply
      .split(/\n\s*\n/)
      .map((block) => block.trim())
      .find((block) => block && !NOT_PROSE.test(block)) ?? ''
  const prose = paragraph
    .split('\n')
    .filter((line) => !NOT_PROSE.test(line.trim())) // e.g. a table started without a blank line
    .join(' ')
  const plain = prose
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1') // links keep their text
    .replace(/\*\*|__|`/g, '')
    .replace(/(^|\s)[*_]([^*_\s][^*_]*)[*_](?=\s|$|[.,;:!?])/g, '$1$2') // *italic*
    .replace(/\s+/g, ' ')
    .replace(/:$/, '.')
    .trim()
  if (plain.length <= MAX_SUMMARY_LENGTH) return plain

  const cut = plain.slice(0, MAX_SUMMARY_LENGTH)
  const lastSentenceEnd = Math.max(cut.lastIndexOf('. '), cut.lastIndexOf('? '), cut.lastIndexOf('! '))
  if (lastSentenceEnd > MAX_SUMMARY_LENGTH / 3) return cut.slice(0, lastSentenceEnd + 1)
  return `${cut.slice(0, cut.lastIndexOf(' '))}…`
}
