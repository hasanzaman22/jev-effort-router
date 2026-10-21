import type { Register } from 'claude-code'

// The effort the Python router picked, as scripts/route_switch.py prints it.
type Picked = {
  effort: 'low' | 'medium' | 'high' | 'xhigh'
  keep: boolean
  message: string
}

export const register: Register = on => {
  // The effort the session runs at now. It lasts until a prompt routes elsewhere,
  // so a follow-up like "yes, do that" keeps the effort of the work it continues.
  let current: Picked | undefined

  on('prompt.submit', async ($, e, next) => {
    // Slash commands and prompts the person did not type are not routed.
    if (e.command || e.isMeta || !e.text.trim()) return next(e)
    try {
      const { exitCode, stdout } = await $.process.run(
        ['python3', `${$.plugin.root}/scripts/route_switch.py`],
        { stdin: JSON.stringify({ prompt: e.text, cwd: await $.session.root() }), timeoutMs: 15000 },
      )
      const picked = exitCode === 0 ? (JSON.parse(stdout) as Picked | null) : null
      if (!picked) {
        // Routing is off (/jev-effort-router:disable, [jev:off]): leave the session's own effort.
        current = undefined
        $.ui.status(undefined)
      } else if (!picked.keep || !current) {
        current = picked
        $.ui.status(`jev effort: ${picked.effort}`)
        if (picked.message) $.ui.log(picked.message)
      }
    } catch {
      // A routing failure must never block the prompt.
    }
    return next(e)
  })

  // Every request of the main conversation runs at the routed effort, on the
  // session's own model. Subagents keep their own settings.
  on('turn.step', async function* ($, e, next) {
    if (!current || e.agentId || e.effort === undefined) return yield* next(e)
    return yield* next({ ...e, effort: current.effort })
  })
}
