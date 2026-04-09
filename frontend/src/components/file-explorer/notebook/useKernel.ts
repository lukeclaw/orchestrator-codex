import { useState, useRef, useCallback, useEffect } from 'react'
import type { KernelStatus, KernelSpec, CellOutput } from './notebookTypes'

interface KernelMessage {
  type: string
  status?: string
  cell_id?: string
  name?: string
  text?: string
  data?: Record<string, string>
  metadata?: Record<string, unknown>
  ename?: string
  evalue?: string
  traceback?: string[]
  execution_count?: number
  message?: string
  available?: boolean
  specs?: KernelSpec[]
  install_hint?: string
}

interface UseKernelOptions {
  sessionId: string
  notebookPath: string | null
  workDir: string
  defaultKernel: string
  onOutput: (cellId: string, output: CellOutput) => void
  onExecuteComplete: (cellId: string, executionCount: number) => void
  onStatusChange: (status: KernelStatus) => void
  onClearOutputs: (cellId: string) => void
}

export function useKernel({
  sessionId,
  notebookPath,
  workDir,
  defaultKernel,
  onOutput,
  onExecuteComplete,
  onStatusChange,
  onClearOutputs,
}: UseKernelOptions) {
  const [kernelStatus, setKernelStatus] = useState<KernelStatus>('none')
  const [availableKernels, setAvailableKernels] = useState<KernelSpec[]>([])
  const [isAvailable, setIsAvailable] = useState<boolean | null>(null) // null = not yet checked
  const [installHint, setInstallHint] = useState<string | null>(null)
  const [isRemote, setIsRemote] = useState(false)
  const [remoteHost, setRemoteHost] = useState<string | null>(null)

  const wsRef = useRef<WebSocket | null>(null)
  const reconnectRef = useRef(false)

  // Stable refs for callbacks to avoid re-creating WS on every render
  const onOutputRef = useRef(onOutput)
  onOutputRef.current = onOutput
  const onExecuteCompleteRef = useRef(onExecuteComplete)
  onExecuteCompleteRef.current = onExecuteComplete
  const onStatusChangeRef = useRef(onStatusChange)
  onStatusChangeRef.current = onStatusChange
  const onClearOutputsRef = useRef(onClearOutputs)
  onClearOutputsRef.current = onClearOutputs
  const autoStartRef = useRef(false)

  // Check kernel availability on mount
  useEffect(() => {
    if (!sessionId) return
    fetch(`/api/sessions/${sessionId}/kernel/specs`)
      .then(r => r.json())
      .then((data: { available: boolean; specs: KernelSpec[]; install_hint: string | null; is_remote?: boolean; host?: string }) => {
        setIsAvailable(data.available)
        setAvailableKernels(data.specs)
        setInstallHint(data.install_hint)
        setIsRemote(data.is_remote ?? false)
        setRemoteHost(data.host ?? null)
        if (!data.available) {
          setKernelStatus('unavailable')
          onStatusChangeRef.current('unavailable')
        } else {
          // Auto-start kernel when available
          autoStartRef.current = true
        }
      })
      .catch(() => {
        setIsAvailable(false)
        setKernelStatus('unavailable')
      })
  }, [sessionId])

  const connectWs = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return
    if (!sessionId) return

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/kernel/${sessionId}`)
    wsRef.current = ws

    ws.onmessage = (event) => {
      const msg: KernelMessage = JSON.parse(event.data)

      switch (msg.type) {
        case 'status': {
          const status = (msg.status ?? 'none') as KernelStatus
          setKernelStatus(status)
          onStatusChangeRef.current(status)
          break
        }
        case 'stream':
          if (msg.cell_id) {
            onOutputRef.current(msg.cell_id, {
              outputType: 'stream',
              stream: (msg.name as 'stdout' | 'stderr') ?? 'stdout',
              text: msg.text ?? '',
              raw: {},
            })
          }
          break
        case 'display':
          if (msg.cell_id) {
            const hasExecCount = msg.execution_count != null
            onOutputRef.current(msg.cell_id, {
              outputType: hasExecCount ? 'execute_result' : 'display_data',
              data: msg.data ?? {},
              outputMetadata: msg.metadata,
              executionCount: msg.execution_count,
              raw: {},
            })
          }
          break
        case 'error':
          if (msg.cell_id) {
            onOutputRef.current(msg.cell_id, {
              outputType: 'error',
              ename: msg.ename ?? 'Error',
              evalue: msg.evalue ?? '',
              traceback: msg.traceback ?? [],
              raw: {},
            })
          }
          break
        case 'execute_complete':
          if (msg.cell_id) {
            onExecuteCompleteRef.current(msg.cell_id, msg.execution_count ?? 0)
          }
          break
        case 'specs':
          setIsAvailable(msg.available ?? false)
          setAvailableKernels(msg.specs ?? [])
          setInstallHint(msg.install_hint ?? null)
          if (!msg.available) {
            setKernelStatus('unavailable')
            onStatusChangeRef.current('unavailable')
          }
          break
      }
    }

    ws.onclose = () => {
      wsRef.current = null
    }
  }, [sessionId])

  const startKernel = useCallback((kernelName?: string) => {
    connectWs()
    // Wait for WS to open, then send start message
    const ws = wsRef.current
    if (!ws) return

    const sendStart = () => {
      ws.send(JSON.stringify({
        type: 'start',
        kernel_name: kernelName ?? defaultKernel,
        notebook_path: notebookPath ?? '',
        work_dir: workDir,
      }))
      setKernelStatus('starting')
      onStatusChangeRef.current('starting')
    }

    if (ws.readyState === WebSocket.OPEN) {
      sendStart()
    } else {
      ws.addEventListener('open', sendStart, { once: true })
    }
  }, [connectWs, defaultKernel, notebookPath, workDir])

  // Auto-start kernel once specs confirm it's available
  useEffect(() => {
    if (autoStartRef.current && kernelStatus === 'none' && isAvailable) {
      autoStartRef.current = false
      startKernel()
    }
  }, [isAvailable, kernelStatus, startKernel])

  const executeCell = useCallback((cellId: string, code: string) => {
    // Auto-start kernel if not running
    if (kernelStatus === 'none' || kernelStatus === 'dead') {
      startKernel()
      // Queue the execution — will be sent once kernel is idle
      const checkAndExecute = () => {
        const ws = wsRef.current
        if (!ws || ws.readyState !== WebSocket.OPEN) {
          setTimeout(checkAndExecute, 100)
          return
        }
        // Wait for idle status, then execute
        const handler = (event: MessageEvent) => {
          const msg = JSON.parse(event.data)
          if (msg.type === 'status' && msg.status === 'idle') {
            ws.removeEventListener('message', handler)
            onClearOutputsRef.current(cellId)
            ws.send(JSON.stringify({ type: 'execute', cell_id: cellId, code }))
          }
        }
        ws.addEventListener('message', handler)
      }
      checkAndExecute()
      return
    }

    if (kernelStatus === 'unavailable') return

    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    onClearOutputsRef.current(cellId)
    ws.send(JSON.stringify({ type: 'execute', cell_id: cellId, code }))
  }, [kernelStatus, startKernel])

  const interrupt = useCallback(() => {
    wsRef.current?.send(JSON.stringify({ type: 'interrupt' }))
  }, [])

  const restart = useCallback(() => {
    wsRef.current?.send(JSON.stringify({ type: 'restart' }))
  }, [])

  const shutdown = useCallback(() => {
    wsRef.current?.send(JSON.stringify({ type: 'shutdown' }))
  }, [])

  // ── Auto-install ────────────────────────────────────────────────────

  const [installing, setInstalling] = useState(false)
  const [installProgress, setInstallProgress] = useState<string | null>(null)

  const installKernel = useCallback(() => {
    if (installing) return
    setInstalling(true)
    setInstallProgress('Starting installation...')

    fetch(`/api/sessions/${sessionId}/kernel/install`, { method: 'POST' })
      .then(async (resp) => {
        if (!resp.body) throw new Error('No response body')
        const reader = resp.body.getReader()
        const decoder = new TextDecoder()
        let lastLine = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          const text = decoder.decode(value, { stream: true })
          // Parse SSE lines: "data: ...\n\n"
          for (const line of text.split('\n')) {
            if (line.startsWith('data: ')) {
              lastLine = line.slice(6)
              setInstallProgress(lastLine)
            }
          }
        }

        if (lastLine === '[done]') {
          // Refresh specs
          setInstalling(false)
          setInstallProgress(null)
          setIsAvailable(true)
          setKernelStatus('none')
          onStatusChangeRef.current('none')
          // Re-fetch specs
          fetch(`/api/sessions/${sessionId}/kernel/specs`)
            .then(r => r.json())
            .then((data: { available: boolean; specs: KernelSpec[]; install_hint: string | null }) => {
              setIsAvailable(data.available)
              setAvailableKernels(data.specs)
              setInstallHint(data.install_hint)
            })
            .catch(() => {})
        } else {
          setInstalling(false)
          setInstallProgress('Installation failed')
          setTimeout(() => setInstallProgress(null), 5000)
        }
      })
      .catch((err) => {
        setInstalling(false)
        setInstallProgress(`Error: ${err.message}`)
        setTimeout(() => setInstallProgress(null), 5000)
      })
  }, [sessionId, installing])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      wsRef.current?.close()
      wsRef.current = null
    }
  }, [])

  return {
    kernelStatus,
    availableKernels,
    isAvailable,
    installHint,
    isRemote,
    remoteHost,
    installing,
    installProgress,
    startKernel,
    executeCell,
    interrupt,
    restart,
    shutdown,
    installKernel,
  }
}
