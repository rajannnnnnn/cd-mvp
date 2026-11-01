/** The filter chips above the conversation list. Shared with the loading skeleton so its labels never drift from the real ones. */
export const FILTERS = [
  { id: 'all', key: 'chat.all', label: 'All' }, { id: 'attention', key: 'chat.attention', label: 'Needs you' }, { id: 'negotiating', key: 'chat.negotiating', label: 'Negotiating' },
  { id: 'ready', key: 'chat.ready', label: 'Ready to buy' }, { id: 'paused', key: 'chat.paused', label: 'Paused' },
] as const
