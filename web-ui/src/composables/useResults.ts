import { ref, reactive, watch, onMounted, computed } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import type { ResultInsights, ResultItem } from '@/types/result.d.ts'
import * as resultsApi from '@/api/results'
import type { GetResultContentParams } from '@/api/results'
import { useWebSocket } from '@/composables/useWebSocket'
import * as tasksApi from '@/api/tasks'

export function useResults() {
  const { t } = useI18n()
  const route = useRoute()
  // State
  const files = ref<string[]>([])
  const selectedFile = ref<string | null>(null)
  const results = ref<ResultItem[]>([])
  const insights = ref<ResultInsights | null>(null)
  const totalItems = ref(0)
  const page = ref(1)
  const limit = ref(100)
  const blacklistKeywords = ref<string[]>([])
  const taskNameByKeyword = ref<Record<string, string>>({})
  const isFileOptionsReady = ref(false)
  const hasFetchedFiles = ref(false)
  const hasFetchedTasks = ref(false)
  const isSavingBlacklist = ref(false)
  const readyDelayMs = 200
  let readyTimer: ReturnType<typeof setTimeout> | null = null

  // Per-fetch request sequence counters: discard stale responses that resolve
  // after a newer request for the same reactive state has been issued.
  let fetchFilesSeq = 0
  let fetchResultsSeq = 0
  let fetchInsightsSeq = 0
  let fetchBlacklistSeq = 0
  let fetchTaskNameSeq = 0

  const STORAGE_KEY_FILTERS = 'resultFilters'

  function loadPersistedFilters(): Required<Omit<GetResultContentParams, 'page' | 'limit'>> {
    const defaults: Required<Omit<GetResultContentParams, 'page' | 'limit'>> = {
      recommended_only: false,
      ai_recommended_only: false,
      keyword_recommended_only: false,
      include_hidden: false,
      sort_by: 'crawl_time',
      sort_order: 'desc',
    }
    try {
      const saved = localStorage.getItem(STORAGE_KEY_FILTERS)
      if (saved) return { ...defaults, ...JSON.parse(saved) }
    } catch { /* ignore */ }
    return defaults
  }

  const filters = reactive<Required<Omit<GetResultContentParams, 'page' | 'limit'>>>(loadPersistedFilters())

  const isLoading = ref(false)
  const error = ref<Error | null>(null)
  const { on } = useWebSocket()

  function normalizeKeyword(value: string) {
    return value.trim().toLowerCase().replace(/\s+/g, '_')
  }

  function getKeywordFromFilename(filename: string) {
    return filename.replace(/_full_data\.jsonl$/i, '').toLowerCase()
  }

  // Methods
  async function fetchFiles() {
    const seq = ++fetchFilesSeq
    try {
      const fileList = await resultsApi.getResultFiles()
      if (seq !== fetchFilesSeq) return
      files.value = fileList
      // If a file is selected that no longer exists, reset it.
      // Otherwise, if nothing is selected, select the first file by default.
      if (selectedFile.value && fileList.includes(selectedFile.value)) {
        return
      }

      const lastSelected = localStorage.getItem('lastSelectedResultFile')
      if (lastSelected && fileList.includes(lastSelected)) {
        selectedFile.value = lastSelected
        return
      }

      selectedFile.value = fileList[0] || null
    } catch (e) {
      if (e instanceof Error) error.value = e
    } finally {
      hasFetchedFiles.value = true
      scheduleFileOptionsReady()
    }
  }

  async function fetchResults() {
    if (!selectedFile.value) {
      // Invalidate any in-flight request so it cannot overwrite the cleared state.
      fetchResultsSeq++
      isLoading.value = false
      results.value = []
      totalItems.value = 0
      return
    }

    isLoading.value = true
    error.value = null
    const seq = ++fetchResultsSeq
    try {
      const data = await resultsApi.getResultContent(selectedFile.value, {
        ...filters,
        page: page.value,
        limit: limit.value,
      })
      if (seq !== fetchResultsSeq) return
      results.value = data.items
      totalItems.value = data.total_items
    } catch (e) {
      if (seq !== fetchResultsSeq) return
      if (e instanceof Error) error.value = e
      results.value = []
      totalItems.value = 0
    } finally {
      // Only the newest request may reset the shared loading flag.
      if (seq === fetchResultsSeq) isLoading.value = false
    }
  }

  async function fetchInsights() {
    if (!selectedFile.value) {
      // Invalidate any in-flight request so it cannot overwrite the cleared state.
      fetchInsightsSeq++
      insights.value = null
      return
    }

    const seq = ++fetchInsightsSeq
    try {
      const data = await resultsApi.getResultInsights(selectedFile.value)
      if (seq !== fetchInsightsSeq) return
      insights.value = data
    } catch (e) {
      if (seq !== fetchInsightsSeq) return
      if (e instanceof Error) error.value = e
      insights.value = null
    }
  }

  async function fetchBlacklistRules() {
    if (!selectedFile.value) {
      // Invalidate any in-flight request so it cannot overwrite the cleared state.
      fetchBlacklistSeq++
      blacklistKeywords.value = []
      return
    }

    const seq = ++fetchBlacklistSeq
    try {
      const data = await resultsApi.getResultBlacklistRules(selectedFile.value)
      if (seq !== fetchBlacklistSeq) return
      blacklistKeywords.value = data.keywords || []
    } catch (e) {
      if (seq !== fetchBlacklistSeq) return
      if (e instanceof Error) error.value = e
      blacklistKeywords.value = []
    }
  }

  async function fetchTaskNameMap() {
    const seq = ++fetchTaskNameSeq
    try {
      const tasks = await tasksApi.getAllTasks()
      if (seq !== fetchTaskNameSeq) return
      const mapping: Record<string, string> = {}
      tasks.forEach((task) => {
        if (task.keyword) {
          mapping[normalizeKeyword(task.keyword)] = task.task_name
        }
      })
      taskNameByKeyword.value = mapping
    } catch (e) {
      if (e instanceof Error) error.value = e
    } finally {
      hasFetchedTasks.value = true
      scheduleFileOptionsReady()
    }
  }

  function scheduleFileOptionsReady() {
    if (isFileOptionsReady.value || !hasFetchedFiles.value || !hasFetchedTasks.value) return
    if (readyTimer) return
    readyTimer = setTimeout(() => {
      isFileOptionsReady.value = true
      readyTimer = null
    }, readyDelayMs)
  }

  // Real-time updates
  on('results_updated', async () => {
    const oldFile = selectedFile.value
    await fetchFiles()
    // If the selected file remains the same, refresh its content (in case of append)
    // If it changed (e.g. from null to new file), the watcher will handle it.
    if (selectedFile.value && selectedFile.value === oldFile) {
      fetchResults()
      fetchInsights()
    }
  })

  on('tasks_updated', () => {
    fetchTaskNameMap()
  })

  async function refreshResults() {
    const current = selectedFile.value
    await fetchFiles()
    if (selectedFile.value && selectedFile.value === current) {
      await fetchResults()
      await fetchInsights()
      await fetchBlacklistRules()
    }
  }

  function exportSelectedResults() {
    if (!selectedFile.value) return
    resultsApi.downloadResultExport(selectedFile.value, { ...filters })
  }

  async function deleteSelectedFile(filename?: string) {
    const target = filename || selectedFile.value
    if (!target) return
    isLoading.value = true
    error.value = null
    try {
      await resultsApi.deleteResultFile(target)
      if (selectedFile.value === target) {
        const lastSelected = localStorage.getItem('lastSelectedResultFile')
        if (lastSelected === target) {
          localStorage.removeItem('lastSelectedResultFile')
        }
      }
      await fetchFiles()
    } catch (e) {
      if (e instanceof Error) error.value = e
      throw e
    } finally {
      isLoading.value = false
    }
  }

  async function toggleItemBlock(item: ResultItem) {
    if (!selectedFile.value) return
    const itemId = item.商品信息?.商品ID
    if (!itemId) return
    const newStatus = item._status === 'hidden' ? 'active' : 'hidden'
    try {
      await resultsApi.updateItemStatus(selectedFile.value, itemId, newStatus)
      await fetchResults()
    } catch (e) {
      if (e instanceof Error) error.value = e
    }
  }

  async function saveBlacklistRules(keywords: string[]) {
    if (!selectedFile.value) return
    isSavingBlacklist.value = true
    error.value = null
    try {
      const data = await resultsApi.updateResultBlacklistRules(selectedFile.value, keywords)
      blacklistKeywords.value = data.keywords || []
      await fetchResults()
      await fetchInsights()
    } catch (e) {
      if (e instanceof Error) error.value = e
      throw e
    } finally {
      isSavingBlacklist.value = false
    }
  }

  // Watchers
  watch(filters, (val) => {
    localStorage.setItem(STORAGE_KEY_FILTERS, JSON.stringify(val))
  }, { deep: true })
  watch([selectedFile, filters], fetchResults, { deep: true })
  watch(selectedFile, () => {
    fetchInsights()
    fetchBlacklistRules()
  })
  watch(selectedFile, (value) => {
    if (value) localStorage.setItem('lastSelectedResultFile', value)
  })
  watch(
    [() => route.query.file, files],
    ([routeFile, currentFiles]) => {
      if (typeof routeFile !== 'string') return
      if (currentFiles.includes(routeFile)) {
        selectedFile.value = routeFile
      }
    },
    { immediate: true }
  )

  const fileOptions = computed(() =>
    files.value.map((file) => {
      const keyword = getKeywordFromFilename(file)
      const taskName = taskNameByKeyword.value[keyword]
      return {
        value: file,
        taskName: taskName || t('common.unnamed'),
        label: t('results.filters.taskNameLabel', {
          task: taskName || t('common.unnamed'),
        }),
      }
    })
  )

  // Lifecycle
  onMounted(() => {
    fetchFiles()
    fetchTaskNameMap()
  })

  return {
    files,
    selectedFile,
    results,
    insights,
    totalItems,
    filters,
    isLoading,
    error,
    fetchFiles, // Expose to allow manual refresh
    refreshResults,
    exportSelectedResults,
    deleteSelectedFile,
    toggleItemBlock,
    blacklistKeywords,
    isSavingBlacklist,
    saveBlacklistRules,
    fileOptions,
    isFileOptionsReady,
  }
}
