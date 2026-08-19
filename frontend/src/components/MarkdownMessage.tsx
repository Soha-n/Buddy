/**
 * Renders assistant message content as formatted markdown.
 *
 * Ollama replies are plain markdown text (headings, lists, code fences,
 * tables, bold/italic), so showing it raw reads as clutter - this renders it
 * properly instead. User messages stay as plain text (see ChatView) since
 * markdown syntax in a question is something the user typed, not meant to be
 * interpreted.
 */
import { memo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import type { ChartRunState } from '../hooks/useChartRunner'
import { ChartCodeBlock } from './ChartCodeBlock'

interface MarkdownMessageProps {
  content: string
  /** Absent for a plain transcript render, so charts stay opt-in per caller. */
  chartRunner?: {
    stateFor: (code: string) => ChartRunState
    run: (code: string) => void
  }
}

function CodeBlock({
  className,
  children,
}: {
  className?: string
  children?: React.ReactNode
}) {
  const [copied, setCopied] = useState(false)
  const language = /language-(\w+)/.exec(className || '')?.[1]
  const text = String(children).replace(/\n$/, '')

  const handleCopy = () => {
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  return (
    <div className="code-block">
      <div className="code-block-header">
        <span className="code-block-lang">{language || 'text'}</span>
        <button type="button" className="code-block-copy" onClick={handleCopy}>
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre>
        <code className={className}>{text}</code>
      </pre>
    </div>
  )
}

export const MarkdownMessage = memo(function MarkdownMessage({
  content,
  chartRunner,
}: MarkdownMessageProps) {
  return (
    <div className="markdown-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code(props) {
            const { className, children, node } = props
            // A fenced block's <code> is wrapped in <pre> by remark; inline
            // code (single backticks) is not - that distinction is what
            // decides plain <code> styling vs. the full block treatment.
            const isBlock = node?.position
              ? className?.includes('language-')
              : false
            if (isBlock) {
              const language = /language-(\w+)/.exec(className || '')?.[1]
              const text = String(children).replace(/\n$/, '')
              // Only Python blocks get a Run button, and only when a runner was
              // provided: a shell snippet or a JSON sample has nothing to run,
              // and offering it would imply the app can execute anything.
              if (chartRunner && language === 'python') {
                return (
                  <ChartCodeBlock
                    code={text}
                    state={chartRunner.stateFor(text)}
                    onRun={() => chartRunner.run(text)}
                  />
                )
              }
              return <CodeBlock className={className}>{children}</CodeBlock>
            }
            return <code className="inline-code">{children}</code>
          },
          a(props) {
            return <a {...props} target="_blank" rel="noopener noreferrer" />
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
})
