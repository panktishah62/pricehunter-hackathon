function SessionSidebar({
  sessions,
  activeSessionId,
  onNewChat,
  onSelectSession,
  location,
  onChangeLocation,
}) {
  return (
    <aside className="flex h-full w-full flex-col border-r border-slate-200 bg-[#f7f7f4]">
      <div className="flex items-center justify-between px-4 py-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-400">Zwig</p>
          <h1 className="mt-1 text-xl font-semibold text-slate-900">Chats</h1>
        </div>
      </div>

      <div className="px-4">
        <button
          type="button"
          onClick={onNewChat}
          className="w-full rounded-2xl bg-slate-900 px-4 py-3 text-left text-sm font-medium text-white transition hover:bg-slate-800"
        >
          + New chat
        </button>
      </div>

      <div className="px-4 pt-4">
        <div className="rounded-2xl border border-slate-200 bg-white px-3 py-3 text-sm text-slate-600">
          <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-slate-400">Location</p>
          <div className="mt-2 flex items-center justify-between gap-3">
            <span className="line-clamp-2 text-sm text-slate-800">{location || 'Not set'}</span>
            <button
              type="button"
              onClick={onChangeLocation}
              className="shrink-0 rounded-full border border-slate-200 px-3 py-1 text-xs font-medium text-slate-700 transition hover:border-slate-300 hover:bg-slate-50"
            >
              Edit
            </button>
          </div>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-4 pt-5">
        <p className="px-3 text-[11px] font-semibold uppercase tracking-[0.24em] text-slate-400">Recent</p>
        <div className="mt-3 space-y-1">
          {sessions.length === 0 ? (
            <div className="px-3 py-4 text-sm text-slate-500">Your recent searches will show up here.</div>
          ) : (
            sessions.map((session) => {
              const isActive = session.session_id === activeSessionId
              return (
                <button
                  key={session.session_id}
                  type="button"
                  onClick={() => onSelectSession(session.session_id)}
                  className={`w-full rounded-2xl px-3 py-3 text-left transition ${
                    isActive ? 'bg-white shadow-sm ring-1 ring-slate-200' : 'hover:bg-white/70'
                  }`}
                >
                  <p className="truncate text-sm font-medium text-slate-900">{session.title}</p>
                  {session.last_message ? (
                    <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-500">{session.last_message}</p>
                  ) : null}
                </button>
              )
            })
          )}
        </div>
      </div>
    </aside>
  )
}

export default SessionSidebar
