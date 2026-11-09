import { computed, onScopeDispose, ref } from 'vue'
import { getTaskGenerationJob } from '@/api/tasks'
import type { TaskGenerationJob } from '@/types/task.d.ts'

const POLL_INTERVAL_MS = 800

function isTerminalStatus(status: TaskGenerationJob['status']) {
  return status === 'completed' || status === 'failed'
}

export function useTaskGenerationJob() {
  const activeJob = ref<TaskGenerationJob | null>(null)
  const pollingError = ref<Error | null>(null)
  const isPolling = ref(false)
  let pollTimer: ReturnType<typeof window.setTimeout> | null = null

  function clearTimer() {
    if (pollTimer === null) return
    window.clearTimeout(pollTimer)
    pollTimer = null
  }

  async function refreshJob() {
    if (!activeJob.value) return
    // 记录发出时的 job_id：切换/重开新任务后，旧任务的在途响应不得写回 activeJob
    const requestJobId = activeJob.value.job_id
    try {
      const nextJob = await getTaskGenerationJob(requestJobId)
      if (activeJob.value?.job_id !== requestJobId) return
      activeJob.value = nextJob
      pollingError.value = null
      if (isTerminalStatus(nextJob.status)) {
        isPolling.value = false
        clearTimer()
        return
      }
      scheduleNextPoll()
    } catch (error) {
      if (activeJob.value?.job_id !== requestJobId) return
      isPolling.value = false
      clearTimer()
      pollingError.value = error as Error
    }
  }

  function scheduleNextPoll() {
    clearTimer()
    pollTimer = window.setTimeout(() => {
      void refreshJob()
    }, POLL_INTERVAL_MS)
  }

  function beginPolling(job: TaskGenerationJob) {
    activeJob.value = job
    pollingError.value = null
    if (isTerminalStatus(job.status)) {
      isPolling.value = false
      clearTimer()
      return
    }
    isPolling.value = true
    scheduleNextPoll()
  }

  function clearJob() {
    activeJob.value = null
    pollingError.value = null
    isPolling.value = false
    clearTimer()
  }

  onScopeDispose(clearJob)

  return {
    activeJob,
    pollingError,
    isGenerating: computed(() => isPolling.value),
    beginPolling,
    clearJob,
    refreshJob,
  }
}
