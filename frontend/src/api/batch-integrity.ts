/** 批次完整性报告 API：对账、追溯、补采归属与补采任务清单。 */
import { fetchJson, postJson } from './client'

export type ResponsibilityStep = {
  step: number
  name: string
  detail: string
}

export type IntegrityItem = {
  record_key: string
  kind: string
  owner_flow: string
  source_batch: string
  source_module: string
  record_id: number | null
  reason: string
  detected_at: string
  recovered: boolean
  recovered_to: string | null
  internal: boolean
  responsibility: ResponsibilityStep[]
}

export type FlowGroup = {
  flow: string
  internal: number
  external: number
  missing: number
  scope_keys: string[]
  is_batch_flow: boolean
  has_ownership: boolean
}

export type Ownership = {
  batch_ref: string
  mode: string
  mode_label: string
  owner_flow: string
  owner_person: string
  item_scopes: Record<string, string[]>
  version: number
  decided_at: string
  decided_by: string
  conflict: boolean
  conflict_detail: string
}

export type ResupplyPlan = {
  plan_id: number
  batch_ref: string
  version: number
  report_version: number
  flow: string
  owner_person: string
  scope_keys: string[]
  scope_batches: string[]
  scope_count: number
  task_ref: string
  created_at: string
  created_by: string
}

export type RecoveryStage = {
  code: string
  stage: number
  name: string
  terminal_state: string
}

export type BatchReport = {
  batch_ref: string
  batch_id: number
  flow: string
  collected_at: string
  status: string
  check_status: string
  target_count: number
  success_count: number
  quarantine_count: number
  recovered_count: number
  pending_count: number
  gap_count: number
  recorded_missed_count: number
  reconciliation: {
    formula: string
    target: number
    collected: number
    quarantined: number
    gap: number
    balanced: boolean
    recorded_missed: number
    reconciled_missing: number
    delta_vs_recorded: number
  }
  completeness: string
  report_version: number
  flow_breakdown: FlowGroup[]
  items: IntegrityItem[]
  missing_count: number
  ownership: Ownership | null
  resupply_plans: ResupplyPlan[]
  resupply_plan_versions: Record<string, number>
  recovery_stage: RecoveryStage
}

export type ReportPage = {
  items: BatchReport[]
  total: number
  page: number
  size: number
}

export type Operator = {
  actor: string
  actorRole: 'viewer' | 'flow_owner' | 'admin'
  actorFlow: string
}

export async function listReports(params: {
  flow?: string
  completeness?: string
  keyword?: string
}): Promise<ReportPage> {
  const query = new URLSearchParams()
  if (params.flow) query.set('flow', params.flow)
  if (params.completeness) query.set('completeness', params.completeness)
  if (params.keyword) query.set('keyword', params.keyword)
  return await fetchJson<ReportPage>(`/api/batch-integrity/reports?${query.toString()}`)
}

export async function getReport(batchRef: string): Promise<BatchReport> {
  return await fetchJson<BatchReport>(
    `/api/batch-integrity/reports/${encodeURIComponent(batchRef)}`,
  )
}

export async function traceRecord(
  recordKey: string,
  batchRef?: string,
): Promise<Record<string, unknown>> {
  const query = batchRef ? `?batch_ref=${encodeURIComponent(batchRef)}` : ''
  return await fetchJson<Record<string, unknown>>(
    `/api/batch-integrity/trace/${encodeURIComponent(recordKey)}${query}`,
  )
}

export async function establishOwnership(
  batchRef: string,
  payload: {
    mode: 'split' | 'freeze'
    owner_flow: string
    owner_person: string
    report_version?: number
  },
  operator: Operator,
): Promise<{
  ok: boolean
  message: string
  conflict?: boolean
  ownership?: Ownership
  recovery_stage?: RecoveryStage
  report?: BatchReport
}> {
  return await postJson(
    `/api/batch-integrity/reports/${encodeURIComponent(batchRef)}/ownership`,
    {
      ...payload,
      actor: operator.actor,
      actor_role: operator.actorRole,
      actor_flow: operator.actorFlow,
    },
  )
}

export async function resolveOwnershipConflict(
  batchRef: string,
  operator: Operator,
): Promise<{
  ok: boolean
  message: string
  ownership?: Ownership
  report?: BatchReport
}> {
  return await postJson(
    `/api/batch-integrity/reports/${encodeURIComponent(batchRef)}/ownership/resolve`,
    {
      keep_owner_person: '',
      actor: operator.actor,
      actor_role: operator.actorRole,
    },
  )
}

export async function createResupplyPlan(
  batchRef: string,
  payload: {
    flow: string
    owner_person?: string
    report_version?: number
  },
  operator: Operator,
): Promise<{
  ok: boolean
  message: string
  duplicated?: boolean
  plan?: ResupplyPlan
  report?: BatchReport
}> {
  return await postJson(
    `/api/batch-integrity/reports/${encodeURIComponent(batchRef)}/resupply`,
    {
      ...payload,
      actor: operator.actor,
      actor_role: operator.actorRole,
      actor_flow: operator.actorFlow,
    },
  )
}
