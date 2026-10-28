import type { QueryPlan, SearchableEntity } from '../../api/agentApiTypes'
import { describeSort, filterChips } from './describePlan'
import type { SortSpec } from './planSorting'

interface ResultsSummaryProps {
  entity: SearchableEntity
  totalCount: number
  page: number
  totalPages: number
  reply: string
  plan: QueryPlan
  sort: SortSpec | null
}

const ENTITY_TITLES: Record<SearchableEntity, string> = {
  transactions: 'Transactions',
  chargebacks: 'Chargebacks',
}

// The bar below the results table and pager: what was searched and how much matched.
export function ResultsSummary({ entity, totalCount, page, totalPages, reply, plan, sort }: ResultsSummaryProps) {
  const chips = filterChips(plan)
  return (
    <div className={`results-summary results-summary--${entity}`} aria-label="Results summary">
      <div className="results-summary__headline">
        <span className="results-summary__entity">{ENTITY_TITLES[entity]}</span>
        <span className="results-summary__count" data-testid="results-count">
          {totalCount.toLocaleString('en-US')}
        </span>
        <span className="results-summary__count-label">{totalCount === 1 ? 'match' : 'matches'}</span>
        <span className="results-summary__meta">
          Page {page} of {Math.max(totalPages, 1)} · sorted by {describeSort(sort)}
        </span>
      </div>
      {reply && <p className="results-summary__reply">{reply}</p>}
      <ul className="filter-chips" aria-label="Filters">
        {chips.length === 0 ? (
          <li className="filter-chip filter-chip--none">No filters (all of this year)</li>
        ) : (
          chips.map((chip) => (
            <li key={chip} className="filter-chip">
              {chip}
            </li>
          ))
        )}
      </ul>
    </div>
  )
}
