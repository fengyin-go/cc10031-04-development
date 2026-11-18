<template>
  <section class="page batch-integrity" :data-module="meta.key">
    <header class="page-head">
      <div>
        <h2>批次完整性对账</h2>
        <p class="page-desc">
          按责任归属核对目标数、成功数与隔离数，每个遗漏项都能回查责任流程和来源批次。
        </p>
      </div>
      <div class="page-actions">
        <label class="inline-field">
          <span>操作身份</span>
          <select v-model="operator.actorRole" data-testid="actor-role">
            <option value="viewer">受控只读</option>
            <option value="flow_owner">流程负责人</option>
            <option value="admin">平台管理员</option>
          </select>
        </label>
        <label class="inline-field">
          <span>所属流程</span>
          <select v-model="operator.actorFlow" data-testid="actor-flow">
            <option v-for="flow in flowOptions" :key="flow" :value="flow">{{ flow }}</option>
          </select>
        </label>
        <button class="btn" data-testid="refresh" type="button" @click="reload">刷新报告</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">批次总数</span>
        <strong class="stat-value">{{ page.total }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">完整批次</span>
        <strong class="stat-value">{{ completeCount }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">遗漏批次</span>
        <strong class="stat-value">{{ missedCount }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待补采遗漏项</span>
        <strong class="stat-value">{{ pendingItems }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>所属流程</span>
        <select v-model="filters.flow" data-testid="filter-flow">
          <option value="">全部流程</option>
          <option v-for="flow in flowOptions" :key="flow" :value="flow">{{ flow }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>完整性</span>
        <select v-model="filters.completeness" data-testid="filter-completeness">
          <option value="">全部</option>
          <option value="完整">完整批次</option>
          <option value="遗漏">遗漏批次</option>
        </select>
      </label>
      <label class="filter-item">
        <span>批次编号</span>
        <input v-model="filters.keyword" data-testid="filter-keyword" placeholder="按批次编号检索" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <p class="readonly-hint" data-testid="role-hint">{{ roleHint }}</p>

    <table class="data-table">
      <thead>
        <tr>
          <th>批次编号</th>
          <th>所属流程</th>
          <th>目标</th>
          <th>成功</th>
          <th>隔离</th>
          <th>已恢复</th>
          <th>遗漏</th>
          <th>完整性</th>
          <th>最终责任人</th>
          <th>处理阶段</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in page.items"
          :key="row.batch_ref"
          :data-testid="`batch-${row.batch_ref}`"
          :data-completeness="row.completeness"
        >
          <td>{{ row.batch_ref }}</td>
          <td>{{ row.flow }}</td>
          <td>{{ row.target_count }}</td>
          <td>{{ row.success_count }}</td>
          <td>{{ row.quarantine_count }}</td>
          <td>{{ row.recovered_count }}</td>
          <td>{{ row.pending_count }}</td>
          <td>
            <span :class="['badge', row.completeness === '完整' ? 'badge-ok' : 'badge-warn']">
              {{ row.completeness }}
            </span>
          </td>
          <td>
            <template v-if="row.ownership">
              {{ row.ownership.owner_flow }} / {{ row.ownership.owner_person }}
              <span v-if="row.ownership.conflict" class="badge badge-danger">冲突</span>
            </template>
            <span v-else class="muted">未指定</span>
          </td>
          <td>{{ row.recovery_stage.code }} · {{ row.recovery_stage.terminal_state }}</td>
          <td class="row-actions">
            <button
              class="link"
              type="button"
              :data-testid="`open-${row.batch_ref}`"
              @click="openDetail(row.batch_ref)"
            >
              查看报告
            </button>
          </td>
        </tr>
        <tr v-if="!page.items.length">
          <td colspan="11" class="empty-state">没有符合条件的批次完整性报告</td>
        </tr>
      </tbody>
    </table>

    <div v-if="detail" class="detail-panel" data-testid="report-detail">
      <header class="detail-head">
        <div>
          <h3>{{ detail.batch_ref }} · {{ detail.flow }}</h3>
          <p class="page-desc">
            报告版本 v{{ detail.report_version }} · {{ detail.recovery_stage.code }}
            {{ detail.recovery_stage.name }} · {{ detail.recovery_stage.terminal_state }}
          </p>
        </div>
        <button class="btn ghost" type="button" @click="closeDetail">收起</button>
      </header>

      <div class="recon-grid">
        <div><span>目标条数</span><strong>{{ detail.reconciliation.target }}</strong></div>
        <div><span>成功条数</span><strong>{{ detail.reconciliation.collected }}</strong></div>
        <div><span>隔离条数</span><strong>{{ detail.reconciliation.quarantined }}</strong></div>
        <div><span>采集缺口</span><strong>{{ detail.reconciliation.gap }}</strong></div>
        <div><span>报告遗漏</span><strong>{{ detail.reconciliation.reconciled_missing }}</strong></div>
        <div><span>登记遗漏</span><strong>{{ detail.reconciliation.recorded_missed }}</strong></div>
        <div>
          <span>勾稽</span>
          <strong>{{ detail.reconciliation.balanced ? '平账' : '不平' }}</strong>
        </div>
      </div>
      <p class="formula">{{ detail.reconciliation.formula }}</p>

      <h4>按流程归属拆分</h4>
      <table class="data-table">
        <thead>
          <tr>
            <th>责任流程</th>
            <th>流程内遗漏</th>
            <th>外部隔离</th>
            <th>缺失合计</th>
            <th>归属</th>
            <th>补采清单版本</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="group in detail.flow_breakdown" :key="group.flow">
            <td>{{ group.flow }}</td>
            <td>{{ group.internal }}</td>
            <td>{{ group.external }}</td>
            <td>{{ group.missing }}</td>
            <td>{{ group.has_ownership ? '责任人已确立' : '待确立' }}</td>
            <td>v{{ detail.resupply_plan_versions[group.flow] ?? 0 }}</td>
          </tr>
        </tbody>
      </table>

      <h4>遗漏记录（每项可回查责任流程与来源批次）</h4>
      <table class="data-table">
        <thead>
          <tr>
            <th>记录键</th>
            <th>类型</th>
            <th>责任流程</th>
            <th>来源批次</th>
            <th>来源模块</th>
            <th>原因</th>
            <th>追溯</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in detail.items" :key="item.record_key" :data-testid="`item-${item.record_key}`">
            <td>{{ item.record_key }}</td>
            <td>{{ item.kind }}</td>
            <td>{{ item.owner_flow }}</td>
            <td>{{ item.source_batch }}</td>
            <td>{{ item.source_module }}</td>
            <td>{{ item.reason }}</td>
            <td>
              <button class="link" type="button" @click="showTrace(item.record_key)">查看链路</button>
            </td>
          </tr>
          <tr v-if="!detail.items.length">
            <td colspan="7" class="empty-state">该批次没有遗漏记录</td>
          </tr>
        </tbody>
      </table>

      <div v-if="trace" class="trace-panel" data-testid="trace-panel">
        <h4>责任链路 · {{ traceKey }}</h4>
        <ol class="trace-list">
          <li v-for="step in traceSteps" :key="step.step">
            <strong>{{ step.name }}</strong>：{{ step.detail }}
          </li>
        </ol>
      </div>

      <div v-if="detail.ownership" class="ownership-box" data-testid="ownership-box">
        <h4>补采归属</h4>
        <p>
          {{ detail.ownership.mode_label }} · 最终责任人
          <strong>{{ detail.ownership.owner_flow }} / {{ detail.ownership.owner_person }}</strong>
          （v{{ detail.ownership.version }}）
        </p>
        <p v-if="detail.ownership.conflict" class="error-text" data-testid="ownership-conflict">
          {{ detail.ownership.conflict_detail }}
          <button
            v-if="operator.actorRole === 'admin'"
            class="link"
            type="button"
            data-testid="resolve-conflict"
            @click="resolveConflict"
          >
            裁决并维持唯一责任人
          </button>
        </p>
        <ul class="scope-list">
          <li v-for="(keys, flow) in detail.ownership.item_scopes" :key="flow">
            {{ flow }}：{{ keys.length }} 项
          </li>
        </ul>
      </div>

      <div class="write-box">
        <h4>确立补采归属</h4>
        <p v-if="!canWrite" class="readonly-hint" data-testid="write-blocked">
          当前身份为受控只读，只能查看报告和当前责任人，不能改写其他流程的补采责任人。
        </p>
        <form class="filter-bar" @submit.prevent="submitOwnership">
          <label class="filter-item">
            <span>归属路径</span>
            <select v-model="ownershipForm.mode" data-testid="ownership-mode" :disabled="!canWrite">
              <option value="split">按流程拆分</option>
              <option value="freeze">整批冻结统一认领</option>
            </select>
          </label>
          <label class="filter-item">
            <span>责任流程</span>
            <select v-model="ownershipForm.ownerFlow" data-testid="ownership-flow" :disabled="!canWrite">
              <option v-for="flow in detail.flow_breakdown" :key="flow.flow" :value="flow.flow">
                {{ flow.flow }}（{{ flow.missing }} 项）
              </option>
            </select>
          </label>
          <label class="filter-item">
            <span>唯一最终责任人</span>
            <input
              v-model="ownershipForm.ownerPerson"
              data-testid="ownership-person"
              placeholder="填写责任人姓名"
              :disabled="!canWrite"
            />
          </label>
          <button class="btn primary" data-testid="submit-ownership" type="submit" :disabled="!canWrite || submitting">
            {{ submitting ? '提交中…' : '确认真任人' }}
          </button>
        </form>

        <h4>补采任务清单（处理项丁）</h4>
        <form class="filter-bar" @submit.prevent="submitResupply">
          <label class="filter-item">
            <span>为本流程生成</span>
            <select v-model="resupplyForm.flow" data-testid="resupply-flow" :disabled="!canWrite">
              <option v-for="group in detail.flow_breakdown" :key="group.flow" :value="group.flow">
                {{ group.flow }}（{{ group.missing }} 项）
              </option>
            </select>
          </label>
          <label class="filter-item">
            <span>补采责任人</span>
            <input
              v-model="resupplyForm.ownerPerson"
              data-testid="resupply-person"
              placeholder="留空则沿用批次最终责任人"
              :disabled="!canWrite"
            />
          </label>
          <label class="filter-item">
            <span>基于报告版本</span>
            <input :value="`v${detail.report_version}`" disabled />
          </label>
          <button class="btn primary" data-testid="submit-resupply" type="submit" :disabled="!canWrite || submitting">
            生成补采清单
          </button>
        </form>

        <table class="data-table" v-if="detail.resupply_plans.length">
          <thead>
            <tr>
              <th>版本</th>
              <th>流程</th>
              <th>责任人</th>
              <th>补采范围</th>
              <th>来源批次</th>
              <th>待办任务</th>
              <th>基于报告</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="plan in detail.resupply_plans" :key="plan.plan_id">
              <td>v{{ plan.version }}</td>
              <td>{{ plan.flow }}</td>
              <td>{{ plan.owner_person }}</td>
              <td>{{ plan.scope_count }} 项</td>
              <td>{{ plan.scope_batches.join('、') }}</td>
              <td>{{ plan.task_ref }}</td>
              <td>v{{ plan.report_version }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <footer class="page-foot">
      <span>共 {{ page.total }} 份批次完整性报告</span>
      <span v-if="message" :class="messageIsError ? 'error-text' : 'ok-text'" data-testid="message">
        {{ message }}
      </span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import {
  createResupplyPlan,
  establishOwnership,
  getReport,
  listReports,
  resolveOwnershipConflict,
  traceRecord,
  type BatchReport,
  type ReportPage,
  type ResponsibilityStep,
} from '@/api/batch-integrity'
import { moduleMeta } from '@/api/backend-service'

const props = defineProps<{ moduleKey: string }>()
const meta = moduleMeta(props.moduleKey)

const page = ref<ReportPage>({ items: [], total: 0, page: 1, size: 20 })
const filters = reactive({ flow: '', completeness: '', keyword: '' })
const operator = reactive({
  actor: '数据工程组',
  actorRole: 'flow_owner' as 'viewer' | 'flow_owner' | 'admin',
  actorFlow: '商品价格采集流程',
})
const detail = ref<BatchReport | null>(null)
const trace = ref<Record<string, unknown> | null>(null)
const traceKey = ref('')
const message = ref('')
const messageIsError = ref(false)
const submitting = ref(false)
const ownershipForm = reactive({ mode: 'split' as 'split' | 'freeze', ownerFlow: '', ownerPerson: '' })
const resupplyForm = reactive({ flow: '', ownerPerson: '' })

const flowOptions = [
  '商品价格采集流程',
  '订单明细采集流程',
  '库存快照采集流程',
]

const completeCount = computed(() => page.value.items.filter((row) => row.completeness === '完整').length)
const missedCount = computed(() => page.value.items.filter((row) => row.completeness === '遗漏').length)
const pendingItems = computed(() => page.value.items.reduce((sum, row) => sum + row.pending_count, 0))
const canWrite = computed(() => operator.actorRole !== 'viewer')
const roleHint = computed(() =>
  operator.actorRole === 'viewer'
    ? '受控只读：可查看全部流程的完整性报告与当前责任人，不能发起归属或补采。'
    : operator.actorRole === 'admin'
      ? '平台管理员：可代任意流程裁决归属。'
      : `流程负责人（${operator.actorFlow}）：只能为本流程缺失项确立责任人并生成补采清单。`,
)
const traceSteps = computed<ResponsibilityStep[]>(() =>
  (trace.value?.trace as ResponsibilityStep[] | undefined) ?? [],
)

function notify(text: string, isError = false) {
  message.value = text
  messageIsError.value = isError
}

async function reload() {
  try {
    page.value = await listReports(filters)
    notify('')
    if (detail.value) {
      const ref = detail.value.batch_ref
      detail.value = page.value.items.find((row) => row.batch_ref === ref)
        ?? (await getReport(ref))
    }
  } catch (error) {
    notify(error instanceof Error ? error.message : '批次完整性报告读取失败', true)
  }
}

function resetFilters() {
  filters.flow = ''
  filters.completeness = ''
  filters.keyword = ''
  void reload()
}

async function openDetail(batchRef: string) {
  try {
    detail.value = await getReport(batchRef)
    trace.value = null
    const primary = detail.value.flow_breakdown.find((g) => g.flow === operator.actorFlow)
      ?? detail.value.flow_breakdown[0]
    ownershipForm.mode = 'split'
    ownershipForm.ownerFlow = primary?.flow ?? ''
    ownershipForm.ownerPerson = detail.value.ownership?.owner_person ?? ''
    resupplyForm.flow = primary?.flow ?? ''
    resupplyForm.ownerPerson = ''
    notify('')
  } catch (error) {
    notify(error instanceof Error ? error.message : '报告详情读取失败', true)
  }
}

function closeDetail() {
  detail.value = null
  trace.value = null
}

async function showTrace(recordKey: string) {
  try {
    const payload = await traceRecord(recordKey, detail.value?.batch_ref)
    trace.value = payload
    traceKey.value = recordKey
  } catch (error) {
    notify(error instanceof Error ? error.message : '责任链路追溯失败', true)
  }
}

async function submitOwnership() {
  if (!detail.value || !canWrite.value) return
  submitting.value = true
  try {
    const result = await establishOwnership(
      detail.value.batch_ref,
      {
        mode: ownershipForm.mode,
        owner_flow: ownershipForm.ownerFlow,
        owner_person: ownershipForm.ownerPerson,
      },
      operator,
    )
    if (result.report) detail.value = result.report
    if (!result.ok) {
      notify(result.message, true)
    } else {
      await reload()
      notify(result.message)
    }
  } catch (error) {
    notify(error instanceof Error ? error.message : '归属确立失败', true)
  } finally {
    submitting.value = false
  }
}

async function resolveConflict() {
  if (!detail.value) return
  submitting.value = true
  try {
    const result = await resolveOwnershipConflict(detail.value.batch_ref, operator)
    if (result.report) detail.value = result.report
    if (result.ok) await reload()
    notify(result.message, !result.ok)
  } catch (error) {
    notify(error instanceof Error ? error.message : '冲突裁决失败', true)
  } finally {
    submitting.value = false
  }
}

async function submitResupply() {
  if (!detail.value || !canWrite.value) return
  submitting.value = true
  try {
    const result = await createResupplyPlan(
      detail.value.batch_ref,
      {
        flow: resupplyForm.flow,
        owner_person: resupplyForm.ownerPerson || undefined,
        report_version: detail.value.report_version,
      },
      operator,
    )
    if (result.report) detail.value = result.report
    if (result.ok) await reload()
    notify(result.message, !result.ok)
  } catch (error) {
    notify(error instanceof Error ? error.message : '补采清单生成失败', true)
  } finally {
    submitting.value = false
  }
}

onMounted(reload)
</script>

<style scoped>
.inline-field { display: flex; flex-direction: column; font-size: 12px; color: var(--muted); gap: 4px; }
.inline-field select, .filter-item select { padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; background: #fff; }
.muted { color: var(--muted); }
.readonly-hint { font-size: 12px; color: #92400e; background: #fffbeb; border: 1px solid #fde68a; border-radius: 6px; padding: 8px 10px; }
.ok-text { color: #15803d; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; }
.badge-ok { background: #dcfce7; color: #166534; }
.badge-warn { background: #fef3c7; color: #92400e; }
.badge-danger { background: #fee2e2; color: #b91c1c; margin-left: 6px; }
.detail-panel { margin-top: 16px; background: #fff; border: 1px solid var(--border); border-radius: 10px; padding: 16px; }
.detail-head { display: flex; justify-content: space-between; align-items: flex-start; }
.detail-panel h4 { margin: 16px 0 8px; font-size: 14px; }
.recon-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 10px; margin-top: 12px; }
.recon-grid div { background: #f8fafc; border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; }
.recon-grid span { display: block; font-size: 12px; color: var(--muted); }
.recon-grid strong { font-size: 18px; }
.formula { font-size: 12px; color: var(--muted); margin: 8px 0 0; }
.trace-panel { margin-top: 12px; padding: 12px; border: 1px dashed var(--border); border-radius: 8px; background: #fafbfc; }
.trace-list { margin: 0; padding-left: 20px; font-size: 13px; line-height: 1.8; }
.ownership-box { margin-top: 12px; padding: 12px; border: 1px solid var(--border); border-radius: 8px; background: #f8fafc; }
.ownership-box p { margin: 4px 0; font-size: 13px; }
.scope-list { margin: 6px 0 0; padding-left: 20px; font-size: 13px; color: var(--muted); }
.write-box { margin-top: 16px; border-top: 1px solid var(--border); padding-top: 8px; }
</style>
