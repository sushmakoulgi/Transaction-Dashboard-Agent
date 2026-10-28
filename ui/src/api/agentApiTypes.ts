// Friendly names for the types generated from the agent service's OpenAPI schema.
// Regenerate with `make ui-api-types` whenever the backend contracts change.
import type { components } from './agent-api-schema'

type Schemas = components['schemas']

export type ChatRequest = Schemas['ChatRequest']
export type ChatResponse = Schemas['ChatResponse']
export type QueryPlan = Schemas['QueryPlan-Output']
export type ExecutePlanRequest = Schemas['ExecutePlanRequest']
export type PlanPageResponse = Schemas['PlanPageResponse']
export type SearchableEntity = Schemas['SearchableEntity']
export type TransactionSummary = Schemas['TransactionSummary']
export type ChargebackSummary = Schemas['ChargebackSummary']
export type TransactionPage = Schemas['PaginatedResult_TransactionSummary_']
export type ChargebackPage = Schemas['PaginatedResult_ChargebackSummary_']
export type ResultPage = TransactionPage | ChargebackPage
export type TransactionDetail = Schemas['TransactionDetail']
export type SessionEntry = Schemas['SessionEntry']
export type ErrorResponse = Schemas['ErrorResponse']
export type FilterValidationIssue = Schemas['FilterValidationIssue']
