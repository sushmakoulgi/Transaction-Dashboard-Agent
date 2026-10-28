import { useTable, type ColumnDef } from '@tanstack/react-table'

import type { ChargebackSummary, SearchableEntity, TransactionSummary } from '../../api/agentApiTypes'
import {
  chargebackColumns,
  resultTableFeatures,
  ROW_ID_FIELD,
  SORT_FIELD_BY_COLUMN,
  transactionColumns,
} from './columnDefinitions'
import type { SortSpec } from './planSorting'

type ResultRow = TransactionSummary | ChargebackSummary

interface ResultsTableProps {
  entity: SearchableEntity
  rows: ResultRow[]
  sort: SortSpec | null
  onSortBy: (field: string) => void
}

export function ResultsTable({ entity, rows, sort, onSortBy }: ResultsTableProps) {
  const columns = (entity === 'transactions' ? transactionColumns : chargebackColumns) as ColumnDef<
    typeof resultTableFeatures,
    ResultRow
  >[]
  const rowIdField = ROW_ID_FIELD[entity]
  const table = useTable({
    features: resultTableFeatures,
    columns,
    data: rows,
    getRowId: (row) => String((row as Record<string, unknown>)[rowIdField]),
  })
  const sortFieldByColumn = SORT_FIELD_BY_COLUMN[entity]

  return (
    <div className="results-table-wrapper">
      <table className="results-table">
        <thead>
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((header) => {
                const sortField = sortFieldByColumn[header.column.id]
                // Compare only for sortable columns: undefined === undefined must not match.
                const direction = sortField && sort?.field === sortField ? sort.direction : undefined
                return (
                  <th
                    key={header.id}
                    scope="col"
                    aria-sort={direction ? (direction === 'asc' ? 'ascending' : 'descending') : undefined}
                  >
                    {sortField ? (
                      <button type="button" className="sort-button" onClick={() => onSortBy(sortField)}>
                        <table.FlexRender header={header} />
                        <span aria-hidden="true">{direction === 'asc' ? ' ▲' : direction === 'desc' ? ' ▼' : ' ↕'}</span>
                      </button>
                    ) : (
                      <table.FlexRender header={header} />
                    )}
                  </th>
                )
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id} data-row-id={row.id}>
              {row.getAllCells().map((cell) => (
                <td key={cell.id}>
                  <table.FlexRender cell={cell} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
