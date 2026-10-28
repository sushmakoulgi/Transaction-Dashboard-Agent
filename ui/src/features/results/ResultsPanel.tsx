// The latest answer's results: a table with server-side paging and sorting. Mount it with a
// new `key` for each chat answer so it starts again at page 1 with the answer's own plan.
import { useCallback, useState } from 'react'

import type { AgentApiClient } from '../../api/agentApiClient'
import type { ChatResponse, PlanPageResponse, QueryPlan } from '../../api/agentApiTypes'
import { describeAgentError } from '../chat/describeAgentError'
import { ReplyMarkdown } from '../chat/ReplyMarkdown'
import { TransactionDetailDialog } from '../detail/TransactionDetailDialog'
import { Pager } from './Pager'
import { nextSort, sortOfPlan, withSort, type SortSpec } from './planSorting'
import { ResultsSummary } from './ResultsSummary'
import { ResultsTable } from './ResultsTable'
import { usePlanResults } from './usePlanResults'
import './ResultsPanel.css'

interface ResultsPanelProps {
  api: AgentApiClient
  answer: ChatResponse | null
}

export function ResultsPanel({ api, answer }: ResultsPanelProps) {
  const plan = answer?.query_plan
  const answerPage = answer?.result_page && answer.result_entity ? answer : null

  return (
    <section className="results-panel" aria-label="Results">
      <h2>Results</h2>
      {plan && answerPage ? (
        <PlanResults
          api={api}
          plan={plan}
          reply={answerPage.reply}
          answerPage={{ result_entity: answerPage.result_entity!, result_page: answerPage.result_page! }}
        />
      ) : (
        <div className="results-empty">
          <p className="placeholder">
            {answer ? 'This answer has no table of results.' : 'Results from your questions will appear here.'}
          </p>
          {answer && (
            <div className="results-summary__reply">
              <ReplyMarkdown text={answer.reply} />
            </div>
          )}
        </div>
      )}
    </section>
  )
}

interface PlanResultsProps {
  api: AgentApiClient
  plan: QueryPlan
  reply: string
  answerPage: PlanPageResponse
}

function PlanResults({ api, plan, reply, answerPage }: PlanResultsProps) {
  const [page, setPage] = useState(answerPage.result_page.page)
  const [pageSize, setPageSize] = useState(answerPage.result_page.page_size)
  const [sortOverride, setSortOverride] = useState<SortSpec | null>(null)
  const [openTransactionId, setOpenTransactionId] = useState<string | null>(null)
  const closeDetail = useCallback(() => setOpenTransactionId(null), []) // stable: the dialog's effect depends on it

  const effectivePlan = sortOverride ? withSort(plan, sortOverride) : plan
  const isAnswersOwnPage =
    !sortOverride && page === answerPage.result_page.page && pageSize === answerPage.result_page.page_size
  const results = usePlanResults(api, effectivePlan, page, pageSize, isAnswersOwnPage ? answerPage : undefined)

  function changePageSize(size: number) {
    setPageSize(size)
    setPage(1)
  }

  function sortBy(field: string) {
    setSortOverride(nextSort(sortOverride ?? sortOfPlan(plan), field))
    setPage(1)
  }

  if (results.isError) {
    return (
      <div className="results-error" role="alert">
        <p>{describeAgentError(results.error)}</p>
        <button type="button" onClick={() => void results.refetch()}>
          Try again
        </button>
      </div>
    )
  }
  if (!results.data) return <p className="placeholder">Loading results…</p>

  const { result_entity: entity, result_page: resultPage } = results.data
  const sort = sortOverride ?? sortOfPlan(plan)
  return (
    <div className={results.isPlaceholderData ? 'results results--loading' : 'results'}>
      {resultPage.items.length === 0 ? (
        <p className="placeholder">No {entity} matched.</p>
      ) : (
        <ResultsTable
          entity={entity}
          rows={resultPage.items}
          sort={sort}
          onSortBy={sortBy}
          onOpenTransaction={setOpenTransactionId}
        />
      )}
      <Pager
        page={page}
        totalPages={resultPage.total_pages}
        totalCount={resultPage.total_count}
        pageSize={pageSize}
        isLoading={results.isFetching}
        onPageChange={setPage}
        onPageSizeChange={changePageSize}
      />
      <ResultsSummary
        entity={entity}
        totalCount={resultPage.total_count}
        page={page}
        totalPages={resultPage.total_pages}
        reply={reply}
        plan={effectivePlan}
        sort={sort}
      />
      {openTransactionId && (
        <TransactionDetailDialog
          api={api}
          transactionId={openTransactionId}
          onClose={closeDetail}
        />
      )}
    </div>
  )
}
