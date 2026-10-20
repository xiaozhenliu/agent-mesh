// reason-network-bridge: a DeepSeek Harness (dsh) host plugin that makes dsh sessions reachable on Reason Network
// the way pi's extension does. For each top-level dsh session it
//   1. mounts a per-session Reason Network MCP server (REASON_NETWORK_SESSION = the dsh session id), so the session
//      gets its own address instead of every dsh session sharing one;
//   2. tells Reason Network when the session is busy or idle (`reason-network hook dsh status`);
//   3. delivers teammates' messages: while idle -> poll every few seconds (and right after going idle) and start a
//      turn with followup(); at a turn's stopping boundary (`agent/turn-stopping`) the session still counts as busy
//      (agent/status is still `running`), so messages marked to interrupt go in with steer(): the model runs another
//      step on them, which in practice reads like a new turn opening with them. Mid-turn tool steps are not hooked:
//      a message arriving during a long task waits (in Reason Network, not lost) until that boundary or idle.
//   4. steps aside while the session is archived: dsh's archived-session gate rejects every step of an archived
//      session (the turn ends `blocked` and the claimed input is discarded), and `hook dsh poll` marks a message read
//      the moment it hands it over, so polling an archived session swallows mail. While a session is in
//      `workspaceRegistry.archivedSessionIds` the bridge does not poll it, unmounts its MCP (Reason Network ignores
//      `session-end` while the session's MCP process is alive) and ends its registration, so the address is free for
//      the next top-level session in the same folder, which then receives the unread backlog. Unarchiving remounts
//      the MCP, re-registers and resumes polling.
// Only messages whose sender's person is in `allowPeople` are delivered; anything else is dropped from the
// conversation and written to the log (with a short excerpt) so it is not silently lost.
// No package imports: dsh modules are resolved from the running installation at load time.

import { execFile } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import fs from 'node:fs'
import { createRequire } from 'node:module'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

export const name = 'reason-network-bridge'
export const inject = ['agents', 'workspaceRegistry']

const DEFAULTS = {
  serverName: 'reason-network',
  command: path.join(os.homedir(), '.local/bin/reason-network'),
  allowPeople: [],
  pollMs: 5000,
  hookTimeoutMs: 10000,
  toolCallTimeoutMs: 660000,
  mountTimeoutMs: 15000,
  logFile: path.join(os.homedir(), '.dsh/logs/reason-network-bridge.log'),
  logMaxBytes: 1024 * 1024,
}

// ---------- pure helpers (unit-tested) ----------

/** Merge user config over defaults and validate it. */
export function resolveConfig(input = {}) {
  const c = { ...DEFAULTS, ...(input ?? {}) }
  if (typeof c.command !== 'string' || !path.isAbsolute(c.command)) throw new Error(`${name}: command must be an absolute path`)
  if (!/^[A-Za-z0-9_-]{1,32}$/.test(c.serverName)) throw new Error(`${name}: invalid serverName`)
  if (!Array.isArray(c.allowPeople) || c.allowPeople.length === 0 || !c.allowPeople.every(p => typeof p === 'string' && p)) {
    throw new Error(`${name}: allowPeople must list at least one person (fail closed)`)
  }
  for (const k of ['pollMs', 'hookTimeoutMs', 'toolCallTimeoutMs', 'mountTimeoutMs', 'logMaxBytes']) {
    if (!Number.isSafeInteger(c[k]) || c[k] <= 0) throw new Error(`${name}: ${k} must be a positive integer`)
  }
  c.pollMs = Math.max(c.pollMs, 1000)
  return c
}

const unescapeAttr = v => v.replace(/&quot;/g, '"').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&')

/**
 * Split Reason Network's rendered delivery (`render::for_agent`) into its frames. Bodies are defanged by Reason
 * Network (no `<reason…` tag can appear inside one), so a frame regex is safe.
 */
export function parseDelivery(text) {
  const frames = []
  const re = /<(reason-message|reason-approval)\s([^>]*)>\n?([\s\S]*?)\n?<\/\1>/g
  let m
  while ((m = re.exec(text)) !== null) {
    const attrs = {}
    for (const a of m[2].matchAll(/([a-z_]+)="([^"]*)"/g)) attrs[a[1]] = unescapeAttr(a[2])
    frames.push({ kind: m[1], from: attrs.from ?? '', id: attrs.id ?? attrs.about ?? '', raw: m[0], body: m[3] })
  }
  return frames
}

/** The person part of `person/session`. */
export const personOf = from => (from.includes('/') ? from.slice(0, from.indexOf('/')) : from)

/** Keep only frames from allowed people. */
export function filterFrames(frames, allowPeople) {
  const allow = new Set(allowPeople)
  const kept = [], dropped = []
  for (const f of frames) (allow.has(personOf(f.from)) && f.from.includes('/') ? kept : dropped).push(f)
  return { kept, dropped }
}

/** What the model sees: the kept frames plus the reply hint Reason Network would add, for the last kept sender. */
export function renderForModel(kept, serverName = 'reason-network') {
  if (kept.length === 0) return ''
  const last = kept[kept.length - 1]
  const person = personOf(last.from)
  const who = last.from.endsWith('/terminal') ? `From ${person}, typed in a terminal.` : `From ${person}'s agent.`
  const frames = kept.map(f => f.raw).join('\n')
  return `${frames}\n${who} Reply if useful with mcp__${serverName}__send_message to "${last.from}".`
}

/** A dsh user-role message, shaped like dsh-llm's createUserMessage (id + role + frozen). */
export function makeUserMessage(text) {
  const content = Object.freeze([Object.freeze({ type: 'text', text })])
  return Object.freeze({ content, source: Object.freeze({ kind: 'reason-network' }), role: 'user', id: randomUUID() })
}

/** Top-level sessions only: subagent children are reached through their parent. */
export function isTopLevel(agent, agents) {
  const h = agent?.session?.header ?? {}
  if (h.origin === 'subagent' || (h.delegationDepth ?? 0) > 0) return false
  if (agents && typeof agents.roots === 'function') return agents.roots().includes(agent)
  return true
}

/** The ids an agent's session may be archived under (agent id and session header id; normally the same string). */
export function sessionIdsOf(agent) {
  const ids = new Set()
  if (agent?.id !== undefined && agent.id !== null) ids.add(String(agent.id))
  const h = agent?.session?.header?.id
  if (typeof h === 'string' && h) ids.add(h)
  return [...ids]
}

/** Whether the workspace registry lists the agent's session as archived. Missing registry/state reads as not archived. */
export function isArchived(agent, workspaceRegistry) {
  let archived
  try { archived = workspaceRegistry?.archivedSessionIds } catch { return false }
  if (!Array.isArray(archived) || archived.length === 0) return false
  const set = new Set(archived.map(String))
  return sessionIdsOf(agent).some(id => set.has(id))
}

/** Load a dsh package from the running installation (bare import first, then relative to the host's entry). */
export function installAnchors(extra = []) {
  const res = typeof process.resourcesPath === 'string' ? [path.join(process.resourcesPath, 'app.asar', 'dsh', 'package.json')] : []
  return [...extra, ...process.argv.slice(1), ...res].filter(x => typeof x === 'string' && path.isAbsolute(x))
}

export async function loadInstalled(spec, anchors = installAnchors()) {
  try {
    return await import(spec)
  } catch (first) {
    for (const a of anchors) {
      try {
        const file = createRequire(a).resolve(spec)
        return await import(pathToFileURL(file).href)
      } catch {}
    }
    throw first
  }
}

// ---------- logging ----------

function makeLogger(c, ctx) {
  const write = (level, msg) => {
    const line = `${new Date().toISOString()} ${level} ${msg}\n`
    try {
      fs.mkdirSync(path.dirname(c.logFile), { recursive: true, mode: 0o700 })
      try {
        if (fs.statSync(c.logFile).size > c.logMaxBytes) fs.renameSync(c.logFile, `${c.logFile}.1`)
      } catch {}
      fs.appendFileSync(c.logFile, line, { mode: 0o600 })
    } catch {}
    try { ctx?.logger?.[level === 'ERROR' ? 'warn' : 'info']?.(`${name}: ${msg}`) } catch {}
  }
  return { info: m => write('INFO', m), warn: m => write('WARN', m), error: m => write('ERROR', m) }
}

const excerpt = s => s.replace(/\s+/g, ' ').trim().slice(0, 200)

// ---------- plugin ----------

export async function apply(ctx, input) {
  const c = resolveConfig(input)
  const log = makeLogger(c, ctx)
  const runHook = c.hookRunner ?? ((args, cwd) => new Promise(resolve =>
    execFile(c.command, ['hook', 'dsh', ...args], { cwd, timeout: c.hookTimeoutMs, env: process.env }, (err, out) =>
      resolve(err ? '' : String(out).trim()))))
  const loadMcp = c.mcpLoader ?? (() => loadInstalled('@deepseek-ai/dsh-mcp-client', installAnchors(c.installAnchors ?? [])))

  let McpClient
  try {
    McpClient = await loadMcp()
    if (typeof McpClient?.apply !== 'function') throw new Error('module has no apply()')
  } catch (e) {
    log.error(`cannot load @deepseek-ai/dsh-mcp-client: ${e?.message ?? e}; bridge inactive`)
    throw e
  }

  /** @type {Map<string, {agent:any, cwd:string, busy:boolean, delivering:boolean, parked:boolean, reconciling:boolean, fiber:any}>} */
  const live = new Map()
  const mounted = new WeakSet()
  const cwdOf = agent => {
    const h = agent?.session?.header?.cwd
    return typeof h === 'string' && path.isAbsolute(h) ? h : process.cwd()
  }
  // One hook at a time per session, in call order: a `status` racing past a later `session-end` would re-register
  // a session that was just ended (seen in Reason Network's daemon log); the hooks are quick, so queueing is cheap.
  const hook = (s, args) => {
    // After dispose only the final `session-end` may run: a queued `poll`/`status` would re-register the session.
    const run = () => (s.gone && args[0] !== 'session-end' ? Promise.resolve('') : runHook([...args, '--session', String(s.agent.id)], s.cwd))
    const next = (s.chain ?? Promise.resolve()).then(run, run)
    s.chain = next.then(() => {}, () => {})
    return next
  }
  const archived = s => isArchived(s.agent, ctx.workspaceRegistry)

  /** Mount the session's own Reason Network MCP server; keeps the fiber so it can be unmounted while archived. */
  async function mount(s) {
    if (s.fiber) return
    try {
      const fiber = s.agent.ctx.plugin(McpClient, {
        transport: 'stdio',
        serverName: c.serverName,
        command: c.command,
        args: ['mcp'],
        env: { REASON_NETWORK_AGENT: 'dsh', REASON_NETWORK_SESSION: String(s.agent.id) },
        cwd: s.cwd,
        toolCallTimeoutMs: c.toolCallTimeoutMs,
      })
      s.fiber = fiber
      let timer
      const late = new Promise(r => { timer = setTimeout(() => r('timeout'), c.mountTimeoutMs); timer.unref?.() })
      const how = await Promise.race([Promise.resolve(fiber).then(() => 'ok'), late]).finally(() => clearTimeout(timer))
      if (how === 'timeout') log.warn(`mcp mount still pending after ${c.mountTimeoutMs}ms session=${s.agent.id}`)
    } catch (e) {
      log.error(`mcp mount failed session=${s.agent.id}: ${e?.message ?? e}`)
    }
  }

  /** Unmount the session's MCP server (stops its `reason-network mcp` process). */
  async function unmount(s) {
    const fiber = s.fiber
    s.fiber = undefined
    if (!fiber) return
    try {
      await (typeof fiber.dispose === 'function' ? fiber.dispose() : fiber)
    } catch (e) {
      log.warn(`mcp unmount failed session=${s.agent.id}: ${e?.message ?? e}`)
    }
  }

  /**
   * Bring the session's parked state in line with the workspace registry. Returns whether it is parked (archived).
   * Parking: MCP first (Reason Network ignores `session-end` while the MCP process is alive), then `session-end`.
   * Resuming: remount, then re-register with the current state.
   */
  async function reconcile(s) {
    const want = archived(s)
    if (want === s.parked || s.reconciling) return s.parked
    s.reconciling = true
    try {
      if (want) {
        s.parked = true
        await unmount(s)
        await hook(s, ['session-end'])
        log.info(`parked (archived) session=${s.agent.id}: mcp unmounted, registration ended; mail stays unread for the next session in ${s.cwd}`)
      } else {
        await mount(s)
        await hook(s, ['status', '--state', s.busy ? 'busy' : 'idle'])
        s.parked = false
        log.info(`resumed (unarchived) session=${s.agent.id}: mcp mounted, registered ${s.busy ? 'busy' : 'idle'}`)
      }
    } catch (e) {
      log.error(`reconcile failed session=${s.agent.id}: ${e?.message ?? e}`)
    } finally {
      s.reconciling = false
    }
    return s.parked
  }

  async function deliver(s, event) {
    if (s.delivering || !s.ready || s.gone) return
    s.delivering = true
    try {
      if (await reconcile(s)) return
      const text = await hook(s, [event])
      if (!text) return
      const frames = parseDelivery(text)
      const { kept, dropped } = filterFrames(frames, c.allowPeople)
      for (const f of dropped) log.warn(`dropped ${f.kind} ${f.id} from "${f.from}" (not in allowPeople) session=${s.agent.id}: ${excerpt(f.body)}`)
      if (frames.length === 0) { log.warn(`unrecognized hook output ignored session=${s.agent.id}: ${excerpt(text)}`); return }
      if (kept.length === 0) return
      const msg = makeUserMessage(renderForModel(kept, c.serverName))
      const how = s.busy ? 'steer' : 'followup'
      s.agent[how](msg)
      log.info(`${how} ${kept.map(f => `${f.id}<-${f.from}`).join(', ')} session=${s.agent.id}`)
    } catch (e) {
      log.error(`deliver(${event}) failed session=${s.agent.id}: ${e?.message ?? e}`)
    } finally {
      s.delivering = false
    }
  }

  async function attach(agent) {
    if (!agent || mounted.has(agent) || !isTopLevel(agent, ctx.agents)) return
    mounted.add(agent)
    // `ready` gates delivery until the MCP is mounted and the session registered (or parked).
    const s = { agent, cwd: cwdOf(agent), busy: agent.status === 'running', delivering: false, parked: false, reconciling: false, fiber: undefined, ready: false }
    live.set(String(agent.id), s)
    if (archived(s)) {
      // Already archived (e.g. a hot reload over an archived live session): no MCP, no registration, no polling.
      s.parked = true
      s.ready = true
      log.info(`attached (archived, parked) session=${agent.id} cwd=${s.cwd}`)
      return
    }
    await mount(s)
    log.info(`attached session=${agent.id} cwd=${s.cwd}`)
    await hook(s, ['status', '--state', s.busy ? 'busy' : 'idle'])
    s.ready = true
  }

  ctx.on('agent/created', async ({ agent }) => { await attach(agent) })
  ctx.on('agent/status', ({ agent, status }) => {
    const s = live.get(String(agent?.id))
    if (!s) return
    s.busy = status === 'running'
    void reconcile(s).then(parked => {
      if (parked) return
      return hook(s, ['status', '--state', s.busy ? 'busy' : 'idle']).then(() => { if (!s.busy) return deliver(s, 'poll') })
    })
  })
  // Still `running` here, so deliver() picks steer(); the extra step it causes is how the turn picks the message up.
  ctx.on('agent/turn-stopping', async ({ agent }) => {
    const s = live.get(String(agent?.id))
    if (s) await deliver(s, 'post-tool-use')
  })
  ctx.on('agent/disposed', ({ agent }) => {
    const s = live.get(String(agent?.id))
    if (!s) return
    live.delete(String(agent.id))
    s.gone = true
    // MCP first (its process keeps the registration alive in Reason Network), then end the registration.
    void unmount(s).then(() => (s.parked ? undefined : hook(s, ['session-end'])))
    log.info(`detached session=${agent.id}${s.parked ? ' (was parked)' : ''}`)
  })

  ctx.effect(() => {
    const timer = setInterval(() => {
      // Parked sessions are visited too, so unarchiving is noticed; deliver() only polls once reconcile() says unparked.
      for (const s of live.values()) if (s.parked || (!s.busy && s.agent.status !== 'running')) void deliver(s, 'poll')
    }, c.pollMs)
    timer.unref?.()
    return () => clearInterval(timer)
  }, `${name}.poll`)

  // Sessions that already exist when the plugin loads (e.g. a hot reload).
  for (const agent of ctx.agents.list?.() ?? []) await attach(agent)
  log.info(`loaded: allowPeople=${c.allowPeople.join(',')} pollMs=${c.pollMs} command=${c.command}`)
}
