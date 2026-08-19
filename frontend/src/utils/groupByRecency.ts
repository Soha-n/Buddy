/** Buckets conversations into ChatGPT-style recency groups for the sidebar. */
import type { ConversationSummary } from '../types/api'

export interface RecencyGroups {
  today: ConversationSummary[]
  yesterday: ConversationSummary[]
  previous7Days: ConversationSummary[]
  older: ConversationSummary[]
}

const DAY_MS = 24 * 60 * 60 * 1000

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
}

export function groupByRecency(
  conversations: ConversationSummary[],
  now: Date = new Date(),
): RecencyGroups {
  const todayStart = startOfDay(now)
  const yesterdayStart = todayStart - DAY_MS
  const weekStart = todayStart - 7 * DAY_MS

  const groups: RecencyGroups = {
    today: [],
    yesterday: [],
    previous7Days: [],
    older: [],
  }

  for (const conversation of conversations) {
    const updatedAt = new Date(conversation.updated_at).getTime()
    if (updatedAt >= todayStart) {
      groups.today.push(conversation)
    } else if (updatedAt >= yesterdayStart) {
      groups.yesterday.push(conversation)
    } else if (updatedAt >= weekStart) {
      groups.previous7Days.push(conversation)
    } else {
      groups.older.push(conversation)
    }
  }

  return groups
}
