import { trackAmplitudeEvent } from '../lib/amplitude'
import { formatResultPrice } from '../lib/format'
import { getResultDisplayName, getResultLinkLabel, isIndiaMartUrl } from '../lib/resultPresentation'
import SearchResultCard from './SearchResultCard'

function ChatBubble({
  message,
  onAction,
  onSuggestedReply,
  actionLoading = false,
  replyLoading = false,
  apiBaseUrl,
  displayCurrency = 'INR',
}) {
  const isAssistant = message.role === 'assistant'
  const results = message.kind === 'results' ? message.payload?.results || [] : []
  const actions = message.payload?.actions || (message.payload?.action ? [message.payload.action] : [])
  const suggestedReplies = message.suggestedReplies || message.payload?.suggested_replies || []

  return (
    <div className={`flex ${isAssistant ? 'justify-start' : 'justify-end'}`}>
      <div
        className={`max-w-[88%] rounded-[1.5rem] px-4 py-3 text-sm leading-6 ${
          isAssistant
            ? 'border border-slate-200 bg-white text-slate-800 shadow-[0_12px_32px_rgba(15,23,42,0.06)]'
            : 'bg-slate-900 text-white shadow-[0_14px_34px_rgba(15,23,42,0.16)]'
        }`}
      >
        <p className="mb-1 text-[11px] uppercase tracking-[0.28em] text-slate-400">
          {isAssistant ? 'Zwig' : 'You'}
        </p>
        <p className="whitespace-pre-wrap break-words">
          {message.boldPrefix ? (
            <>
              <span className="font-semibold">{message.boldPrefix}</span>
              {message.content ? ` ${message.content}` : null}
            </>
          ) : (
            message.content
          )}
        </p>

        {isAssistant && suggestedReplies.length > 0 ? (
          <div className="mt-4 flex flex-wrap gap-2">
            {suggestedReplies.map((reply) => (
              <button
                key={reply}
                type="button"
                onClick={() => onSuggestedReply?.(reply)}
                disabled={replyLoading || actionLoading}
                className="rounded-full border border-slate-200 bg-white px-3 py-2 text-left text-sm text-slate-700 transition hover:border-slate-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {reply}
              </button>
            ))}
          </div>
        ) : null}

        {results.length > 0 ? (
          <div className="mt-4 space-y-3">
            {results.map((result) => (
              <SearchResultCard
                key={result.id}
                result={result}
                apiBaseUrl={apiBaseUrl}
                displayCurrency={displayCurrency}
              />
            ))}
          </div>
        ) : null}

        {actions.length > 0 ? (
          <div className="mt-4 flex flex-wrap gap-2">
            {actions.map((item) => (
              <button
                key={`${item.type}-${item.label}`}
                type="button"
                onClick={() => onAction?.(item, message)}
                disabled={actionLoading}
                className="inline-flex items-center rounded-full bg-slate-900 px-4 py-2 text-xs font-medium text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {actionLoading ? item.loadingLabel || 'Working...' : item.label}
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  )
}

export default ChatBubble
