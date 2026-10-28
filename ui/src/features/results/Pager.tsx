const PAGE_SIZE_OPTIONS = [10, 25, 50, 100]

interface PagerProps {
  page: number
  totalPages: number
  totalCount: number
  pageSize: number
  isLoading: boolean
  onPageChange: (page: number) => void
  onPageSizeChange: (pageSize: number) => void
}

export function Pager({ page, totalPages, totalCount, pageSize, isLoading, onPageChange, onPageSizeChange }: PagerProps) {
  const lastPage = Math.max(totalPages, 1)
  const sizes = PAGE_SIZE_OPTIONS.includes(pageSize)
    ? PAGE_SIZE_OPTIONS
    : [...PAGE_SIZE_OPTIONS, pageSize].sort((a, b) => a - b)

  return (
    <nav className="pager" aria-label="Pagination">
      <button type="button" onClick={() => onPageChange(page - 1)} disabled={page <= 1 || isLoading}>
        ‹ Previous
      </button>
      <span className="pager__status">
        Page {page} of {lastPage} · {totalCount} {totalCount === 1 ? 'match' : 'matches'}
      </span>
      <button type="button" onClick={() => onPageChange(page + 1)} disabled={page >= lastPage || isLoading}>
        Next ›
      </button>
      <label className="pager__size">
        Rows per page{' '}
        <select value={pageSize} onChange={(event) => onPageSizeChange(Number(event.target.value))}>
          {sizes.map((size) => (
            <option key={size} value={size}>
              {size}
            </option>
          ))}
        </select>
      </label>
    </nav>
  )
}
