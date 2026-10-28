// Sorting changes the plan itself (server-side), so every page is ordered the same way.
import type { QueryPlan } from '../../api/agentApiTypes'
import type { components } from '../../api/agent-api-schema'

export type SortSpec = components['schemas']['SortSpec']
type SearchStep = components['schemas']['SearchStep-Output']
type PlanStep = QueryPlan['steps'][number]

function isSearchStep(step: PlanStep): step is SearchStep {
  return step.tool_name === 'search_transactions' || step.tool_name === 'search_chargebacks'
}

export function sortOfPlan(plan: QueryPlan): SortSpec | null {
  const searches = plan.steps.filter(isSearchStep)
  return searches.at(-1)?.sort ?? null
}

// A copy of the plan whose last search uses `sort`.
export function withSort(plan: QueryPlan, sort: SortSpec): QueryPlan {
  const lastSearchIndex = plan.steps.findLastIndex(isSearchStep)
  if (lastSearchIndex === -1) return plan
  return {
    ...plan,
    steps: plan.steps.map((step, index) =>
      index === lastSearchIndex && isSearchStep(step) ? { ...step, sort } : step,
    ),
  }
}

// Clicking a column: sort by it descending first, then toggle the direction.
export function nextSort(current: SortSpec | null, field: string): SortSpec {
  if (current?.field === field) {
    return { field, direction: current.direction === 'desc' ? 'asc' : 'desc' }
  }
  return { field, direction: 'desc' }
}
