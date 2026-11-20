/**
 * ScriptFlow — a Host-only Cordis plugin.
 *
 * One Douyin link in, two files out: a watermark-free mp4 and a plain-text
 * docx. The media work happens in `engine/run.py` (a thin driver around the
 * locally installed shortvideo engine, ffmpeg, sherpa-onnx and python-docx),
 * so this package itself has no npm dependencies and can be restarted freely.
 *
 * Tools:
 *   sf_run       run the whole chain (sync, or background with a job id)
 *   sf_job       list / status / cancel background runs
 *   sf_settings  read or change the plugin config (DeepSeek key, engine, paths)
 */
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

export const name = 'scriptflow'
export const inject = ['tools']

const PKG_DIR = path.dirname(fileURLToPath(import.meta.url))
const RUN_PY = path.join(PKG_DIR, 'engine', 'run.py')
const DEFAULT_HOME = 'E:/Tools/scriptflow-home'
const DEFAULT_ENGINE_DIR = 'E:/Tools/dsh-shortvideo-export/engine'
const DEFAULT_PYTHON = 'E:/Tools/sv-engine/Scripts/python.exe'

const RUN_TIMEOUT_MS = 20 * 60 * 1000

// ---------------------------------------------------------------- tool sugar

const RESULT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    ok: { type: 'boolean' },
    summary: { type: 'string' },
    data: {},
  },
}

const renderSummary = (_args, value) => [{ type: 'text', text: value.summary }]

function defineTool(options) {
  return {
    name: options.name,
    description: options.description,
    parameters: {
      type: 'object',
      properties: options.properties || {},
      ...(options.required && options.required.length ? { required: options.required } : {}),
      additionalProperties: false,
    },
    output: { schema: RESULT_SCHEMA, render: renderSummary },
    ...(options.timeoutMs ? { timeoutMs: options.timeoutMs } : {}),
    execute: async (args, exec) => options.execute(args || {}, exec),
  }
}

// ---------------------------------------------------------------- config

function homeDir(config) {
  const fromConfig = config && typeof config.home === 'string' ? config.home.trim() : ''
  if (fromConfig) return fromConfig
  const fromEnv = String(process.env.SF_HOME || '').trim()
  return fromEnv || DEFAULT_HOME
}

function configPath(config) {
  return path.join(homeDir(config), 'config.json')
}

function readConfigFile(config) {
  try {
    return JSON.parse(fs.readFileSync(configPath(config), 'utf8')) || {}
  } catch {
    return {}
  }
}

function effective(config) {
  const home = homeDir(config)
  const file = readConfigFile(config)
  const python = String(file.python || (config && config.python) || process.env.SF_PYTHON || DEFAULT_PYTHON)
  return {
    home,
    python,
    engine: String(file.engine || (config && config.engine) || 'sensevoice'),
    outputDir: String(file.outputDir || path.join(home, 'output')),
    engineDir: String(file.engineDir || DEFAULT_ENGINE_DIR),
    dataHome: String(file.dataHome || 'E:/Tools/shortvideo-export-home'),
    deepseekKey: String(file.deepseekKey || process.env.SF_DEEPSEEK_KEY || ''),
    deepseekBase: String(file.deepseekBase || 'https://api.deepseek.com'),
    deepseekModel: String(file.deepseekModel || 'deepseek-chat'),
  }
}

function mask(key) {
  const k = String(key || '')
  if (!k) return ''
  return k.length <= 10 ? k.slice(0, 3) + '…' : k.slice(0, 6) + '…' + k.slice(-4)
}

function writeConfigFile(config, patch) {
  const home = homeDir(config)
  fs.mkdirSync(home, { recursive: true })
  const current = readConfigFile(config)
  const next = { ...current, ...patch }
  for (const key of Object.keys(next)) {
    if (next[key] === null || next[key] === '') delete next[key]
  }
  fs.writeFileSync(configPath(config), JSON.stringify(next, null, 2), 'utf8')
  return next
}

// ---------------------------------------------------------------- process

function runPython(settings, args, { detached = false, timeoutMs = RUN_TIMEOUT_MS } = {}) {
  const env = { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' }
  delete env.PYTHONHOME
  const child = spawn(settings.python, ['-X', 'utf8', RUN_PY, ...args], {
    cwd: PKG_DIR,
    env,
    detached,
    windowsHide: true,
    stdio: detached ? ['ignore', 'ignore', 'pipe'] : ['ignore', 'pipe', 'pipe'],
  })
  if (detached) {
    child.unref()
    return { child, done: Promise.resolve({ code: 0, stdout: '', stderr: '' }) }
  }
  let stdout = ''
  let stderr = ''
  child.stdout.on('data', (chunk) => { stdout += chunk.toString('utf8') })
  child.stderr.on('data', (chunk) => { stderr += chunk.toString('utf8').slice(-20000) })
  const done = new Promise((resolve) => {
    const timer = setTimeout(() => {
      try { child.kill() } catch {}
      resolve({ code: -1, stdout, stderr: stderr + '\n[timeout]' })
    }, timeoutMs)
    child.on('error', (error) => {
      clearTimeout(timer)
      resolve({ code: -2, stdout, stderr: String(error && error.message || error) })
    })
    child.on('close', (code) => {
      clearTimeout(timer)
      resolve({ code: code ?? -3, stdout, stderr })
    })
  })
  return { child, done }
}

function parseResult(stdout) {
  const marker = '===RESULT==='
  const idx = String(stdout || '').lastIndexOf(marker)
  if (idx < 0) return null
  try {
    return JSON.parse(stdout.slice(idx + marker.length).trim())
  } catch {
    return null
  }
}

function humanSize(bytes) {
  const n = Number(bytes) || 0
  if (n >= 1024 * 1024 * 1024) return (n / 1024 / 1024 / 1024).toFixed(2) + ' GB'
  if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB'
  return Math.round(n / 1024) + ' KB'
}

function summarise(result) {
  if (result && result.batch) {
    const items = result.items || []
    const head = `批量完成：共 ${result.count} 条，成功 ${result.done} 条，失败 ${result.failed} 条`
    const lines = [head, '']
    items.forEach((item, i) => {
      if (item.ok === false) {
        lines.push(`${i + 1}. 失败 · ${item.url}`)
        lines.push(`   ${String(item.error || '').split('\n')[0].slice(0, 180)}`)
        return
      }
      const meta = item.meta || {}
      const proof = item.proof || {}
      lines.push(`${i + 1}. ${item.author || '未知作者'} · ${item.title || '未命名'} · ${meta.duration || '?'} 秒 · ${item.wordCount || 0} 字`)
      lines.push(`   校对 ${proof.by === 'llm' ? 'DeepSeek' : '本地规则'} · 改动 ${proof.changes || 0} 处`)
      lines.push(`   mp4：${item.videoPath || '（未找到）'}`)
      lines.push(`   Word：${item.docxPath || '（未生成）'}`)
    })
    const failed = items.filter((it) => it.ok === false)
    if (failed.length) {
      lines.push('')
      lines.push('失败的可以单独重跑：sf_run url=<那条链接>')
    }
    return lines.join('\n')
  }
  const meta = result.meta || {}
  const proof = result.proof || {}
  const lines = [
    `已完成：${meta.author || '未知作者'} · ${meta.duration || '?'} 秒 · ${meta.width || '?'}×${meta.height || '?'} · ${humanSize(meta.size)}`,
    `文案：${result.wordCount || 0} 字 / ${(result.paragraphs || []).length} 段 · 校对 ${
      proof.by === 'llm' ? 'DeepSeek（' + (proof.model || 'deepseek-chat') + '）' : '本地规则'} · 改动 ${proof.changes || 0} 处`,
    `视频：${result.videoPath || '（未找到）'}`,
    `文案：${result.docxPath || '（未生成）'}`,
    '',
    result.text || '',
  ]
  return lines.join('\n')
}

// ---------------------------------------------------------------- apply

export function apply(ctx, config = {}) {
  ctx.logger?.info?.(`[scriptflow] run.py=${RUN_PY} home=${homeDir(config)}`)

  ctx.tools.register(defineTool({
    name: 'sf_run',
    description:
      '抖音链接 → 无水印 mp4 + 纯文字 Word。链路：下载 → 抽音轨 → 语音识别（默认本地 SenseVoice，0 元）→ '
      + '本地规则清洗 → DeepSeek 校对 → 写 docx，跑完自动删中间音轨。'
      + 'url 里可以一行一条链接，一次跑多条（批量按顺序跑完，每条各出一份 mp4 + Word）；条数上限用 limit。'
      + '默认同步返回结果（60 秒片本机十几秒到半分钟）；传 background:true 则立即返回任务号，用 sf_job 轮询。',
    properties: {
      url: {
        type: 'string',
        description: '抖音作品链接、分享口令（含 v.douyin.com 短链）或纯数字作品 ID；可多行，一行一条，批量处理',
      },
      engine: {
        type: 'string',
        enum: ['sensevoice', 'faster-whisper', 'dashscope', 'volc', 'official'],
        description: '识别引擎；默认用配置里的 engine（sensevoice 本地免费）',
      },
      limit: { type: 'integer', description: '最多处理前几条（批量时防止一次跑太多），不传=全部' },
      background: { type: 'boolean', description: 'true 表示后台跑，返回 jobId，用 sf_job 查' },
    },
    required: ['url'],
    timeoutMs: RUN_TIMEOUT_MS,
    execute: async (args) => {
      const settings = effective(config)
      const urls = String(args.url || '')
        .split(/[\r\n]+/)
        .map((line) => line.trim())
        .filter(Boolean)
      if (!urls.length) return { ok: false, summary: '缺少 url：给一条抖音链接或分享口令（多行=批量）', data: null }

      const limit = Number(args.limit) || 0
      const targets = limit > 0 ? urls.slice(0, limit) : urls
      const batch = targets.length > 1
      const jobId = 'sf' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6)
      const jobDir = path.join(settings.home, 'jobs', jobId)
      fs.mkdirSync(jobDir, { recursive: true })

      const argv = ['--home', settings.home, '--engine', String(args.engine || settings.engine), '--job', jobId]
      if (batch) {
        const file = path.join(jobDir, 'urls.txt')
        fs.writeFileSync(file, targets.join('\n'), 'utf8')
        argv.push('--urls-file', file)
      } else {
        argv.push('--url', targets[0])
      }

      if (args.background) {
        const { child } = runPython(settings, argv, { detached: true })
        return {
          ok: true,
          summary: `已放到后台：${jobId}（pid ${child.pid}，${targets.length} 条）。用 sf_job action=status id=${jobId} 查进度。`,
          data: { jobId, pid: child.pid, count: targets.length, statusPath: path.join(jobDir, 'status.json') },
        }
      }
      const { done } = runPython(settings, argv, { timeoutMs: batch ? Math.max(RUN_TIMEOUT_MS, targets.length * RUN_TIMEOUT_MS) : RUN_TIMEOUT_MS })
      const { code, stdout, stderr } = await done
      const result = parseResult(stdout)
      if (!result || !result.ok) {
        const reason = (result && result.error) || stderr.trim().split('\n').slice(-6).join('\n') || `python exit ${code}`
        const partial = result && result.items
          ? '\n' + summarise({ ...result, batch: true })
          : ''
        return { ok: false, summary: '失败：' + reason + partial, data: { stderr: stderr.slice(-4000), result } }
      }
      return { ok: true, summary: summarise(result), data: result }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'sf_job',
    description: '查看或取消 sf_run background 跑出来的任务：action=list 列最近任务，action=status+id 看一个，action=cancel+id 取消。',
    properties: {
      action: { type: 'string', enum: ['list', 'status', 'cancel'], description: '操作' },
      id: { type: 'string', description: 'status / cancel 用的任务号' },
      limit: { type: 'integer', description: 'list 最多列几个，默认 10' },
    },
    required: ['action'],
    execute: async (args) => {
      const settings = effective(config)
      const jobsDir = path.join(settings.home, 'jobs')
      const readStatus = (id) => {
        try {
          return JSON.parse(fs.readFileSync(path.join(jobsDir, id, 'status.json'), 'utf8'))
        } catch {
          return null
        }
      }
      if (args.action === 'list') {
        let rows = []
        try {
          rows = fs.readdirSync(jobsDir).filter((n) => fs.existsSync(path.join(jobsDir, n, 'status.json')))
        } catch {}
        const limit = Number(args.limit) || 10
        const items = rows
          .map((id) => ({ id, ...readStatus(id) }))
          .filter((x) => x && x.id)
          .sort((a, b) => String(b.startedAt || '').localeCompare(String(a.startedAt || '')))
          .slice(0, limit)
        if (!items.length) return { ok: true, summary: '还没有后台任务。', data: { jobs: [] } }
        const lines = items.map((j) => `- ${j.id} · ${j.status} · ${j.stage || ''} ${j.percent ? Math.round(j.percent) + '%' : ''} ${j.result && j.result.title ? '· ' + j.result.title.slice(0, 40) : ''}`)
        return { ok: true, summary: `${items.length} 个任务：\n` + lines.join('\n'), data: { jobs: items } }
      }
      const id = String(args.id || '').trim()
      if (!id) return { ok: false, summary: '缺少 id', data: null }
      if (args.action === 'cancel') {
        const status = readStatus(id) || {}
        if (!status.pid) return { ok: false, summary: `任务 ${id} 没有 pid，可能已经结束`, data: null }
        try { process.kill(status.pid) } catch (error) {
          return { ok: false, summary: `取消失败：${error.message}`, data: null }
        }
        return { ok: true, summary: `已请求结束 ${id}（pid ${status.pid}）`, data: { id } }
      }
      const status = readStatus(id)
      if (!status) return { ok: false, summary: `找不到任务 ${id}`, data: null }
      const result = status.result
      const summary = status.status === 'done' && result
        ? summarise(result)
        : `任务 ${id}：${status.status} · ${status.stage || ''} ${status.percent ? Math.round(status.percent) + '%' : ''}` +
          (status.error ? `\n错误：${status.error}` : '')
      return { ok: status.status !== 'error', summary, data: status }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'sf_settings',
    description:
      '读或改插件配置。action=get 看当前生效路径与 Key（打码）；action=set 改一项，'
      + '支持 deepseekKey / deepseekBase / deepseekModel / engine / outputDir / python / engineDir / dataHome。',
    properties: {
      action: { type: 'string', enum: ['get', 'set'], description: '操作' },
      key: { type: 'string', description: 'set 用：配置项名' },
      value: { type: 'string', description: 'set 用：值' },
    },
    required: ['action'],
    execute: async (args) => {
      const settings = effective(config)
      if (args.action === 'get') {
        const lines = [
          `home：${settings.home}`,
          `引擎数据：${settings.dataHome}`,
          `引擎目录：${settings.engineDir}${fs.existsSync(settings.engineDir) ? '' : '（不存在）'}`,
          `Python：${settings.python}${fs.existsSync(settings.python) ? '' : '（不存在）'}`,
          `识别引擎：${settings.engine}`,
          `输出目录：${settings.outputDir}`,
          `DeepSeek：${settings.deepseekKey ? mask(settings.deepseekKey) + ' · ' + settings.deepseekModel + ' · ' + settings.deepseekBase : '未配置（校对退回本地规则）'}`,
          `配置文件：${configPath(config)}`,
        ]
        return { ok: true, summary: lines.join('\n'), data: { ...settings, deepseekKey: mask(settings.deepseekKey) } }
      }
      const key = String(args.key || '').trim()
      const allowed = ['deepseekKey', 'deepseekBase', 'deepseekModel', 'engine', 'outputDir', 'python', 'engineDir', 'dataHome']
      if (!allowed.includes(key)) {
        return { ok: false, summary: `不支持的配置项：${key}。可用：${allowed.join(' / ')}`, data: null }
      }
      const value = args.value === undefined || args.value === null ? '' : String(args.value).trim()
      const next = writeConfigFile(config, { [key]: value })
      const shown = key === 'deepseekKey' ? mask(value) : value
      return {
        ok: true,
        summary: `已更新 ${key} = ${shown || '（已清空）'}；配置文件 ${configPath(config)}`,
        data: { config: { ...next, deepseekKey: mask(next.deepseekKey || '') } },
      }
    },
  }))

  ctx.logger?.info?.('[scriptflow] 3 tools registered')
}
