// One page of a plan's results, fetched through /api/plans/execute (never calls Claude).
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import type { AgentApiClient } from '../../api/agentApiClient'
import type { PlanPageResponse, QueryPlan } from '../../api/agentApiTypes'

const RESULTS_FRESH_FOR_MS = 60_000

export function usePlanResults(
  api: AgentApiClient,
  plan: QueryPlan,
  page: number,
  pageSize: number,
  // The page the chat answer already returned. Pass it only for that exact page (same plan,
  // page and size): TanStack Query seeds whichever key is current with initialData.
  matchingInitialPage: PlanPageResponse | undefined,
) {
  const [receivedAt] = useState(() => Date.now())
  return useQuery({
    queryKey: ['plan-results', plan, page, pageSize],
    queryFn: () => api.executePlan(plan, page, pageSize),
    initialData: matchingInitialPage,
    initialDataUpdatedAt: matchingInitialPage ? receivedAt : undefined,
    staleTime: RESULTS_FRESH_FOR_MS,
    placeholderData: keepPreviousData, // keep showing rows while the next page loads
  })
}
