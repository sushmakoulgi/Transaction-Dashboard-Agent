import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import './ReplyMarkdown.css'

// Claude's replies are Markdown (bold, lists, tables). Raw HTML is never rendered, and images
// are dropped so a reply can't make the browser fetch anything.
const DISALLOWED_ELEMENTS = ['img']

interface ReplyMarkdownProps {
  text: string
}

export function ReplyMarkdown({ text }: ReplyMarkdownProps) {
  return (
    <div className="reply-markdown">
      <Markdown
        remarkPlugins={[remarkGfm]}
        disallowedElements={DISALLOWED_ELEMENTS}
        components={{
          table: ({ node: _node, ...props }) => (
            <div className="reply-markdown__table">
              <table {...props} />
            </div>
          ),
          a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer noopener" />,
        }}
      >
        {text}
      </Markdown>
    </div>
  )
}
