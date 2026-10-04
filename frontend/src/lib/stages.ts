export const STAGES = ['new', 'exploring', 'interested', 'negotiating', 'ready_to_buy', 'won', 'lost'] as const
export type Stage = (typeof STAGES)[number]
export const STAGE_LABEL: Record<Stage, string> = { new: 'New', exploring: 'Exploring', interested: 'Interested', negotiating: 'Negotiating', ready_to_buy: 'Ready to buy', won: 'Won', lost: 'Lost' }
export const STAGE_TONE: Record<Stage, 'gray' | 'blue' | 'amber' | 'green' | 'red'> = { new: 'gray', exploring: 'blue', interested: 'amber', negotiating: 'amber', ready_to_buy: 'green', won: 'green', lost: 'red' }
export const STAGE_COLOR: Record<Stage, string> = { new: '#94a3b8', exploring: '#3b82f6', interested: '#f59e0b', negotiating: '#f97316', ready_to_buy: '#10b981', won: '#059669', lost: '#ef4444' }
export const HANDOFF_LABEL: Record<string, string> = {
  unknown_answer: 'Question the AI couldn’t answer', on_request_price: 'Asked for a price you quote yourself', below_floor: 'Wants a lower price than the AI may give',
  customer_asked_human: 'Asked to talk to you', complaint: 'Complaint', high_value: 'High-value order', system_failure: 'AI couldn’t reply', other: 'Needs your attention',
}
export const HANDOFF_TONE: Record<string, 'red' | 'amber' | 'blue'> = { complaint: 'red', customer_asked_human: 'amber', below_floor: 'amber', on_request_price: 'blue', unknown_answer: 'blue', high_value: 'amber', system_failure: 'red', other: 'amber' }
