import { homedir } from 'node:os';
import { isAbsolute, join } from 'node:path';
import { pathToFileURL } from 'node:url';

const plugin = __PLUGIN_JSON__;
const runtimeRelative = __RUNTIME_RELATIVE_JSON__;
const events = __EVENTS_JSON__;
const runtimeRoot = join(homedir(), runtimeRelative);
const config = { plugin, root: join(runtimeRoot, 'source'), events };
const maxContinuations = 5;

function enabled() {
  return (process.env.YI_ENABLE_MIGRATED_HOOKS || '').split(',').some(value => value.trim() === plugin);
}

export function canonicalTool(name, input) {
  const aliases = {
    Bash: 'Bash', bash: 'Bash', shell: 'Bash', shell_command: 'Bash', exec_command: 'Bash',
    Read: 'Read', read: 'Read', read_file: 'Read',
    Write: 'Write', write: 'Write', create_file: 'Write',
    Edit: 'Edit', edit: 'Edit', edit_file: 'Edit',
    Glob: 'Glob', glob: 'Glob', find: 'Glob',
    Grep: 'Grep', grep: 'Grep',
  };
  const canonical = Object.hasOwn(aliases, name) ? aliases[name] : name;
  const mapped = { ...input };
  if (canonical === 'Bash' && typeof mapped.cmd === 'string' && mapped.command === undefined) {
    mapped.command = mapped.cmd;
    delete mapped.cmd;
  }
  if (['Read', 'Write', 'Edit'].includes(canonical) && mapped.path !== undefined) {
    mapped.file_path = mapped.path;
    delete mapped.path;
  }
  if (canonical === 'Edit') {
    if (typeof mapped.oldText === 'string') { mapped.old_string = mapped.oldText; delete mapped.oldText; }
    if (typeof mapped.newText === 'string') { mapped.new_string = mapped.newText; delete mapped.newText; }
  }
  return { name: canonical, input: mapped };
}

function nativeInput(name, original, updated) {
  if (Object.keys(updated).some(key => ['__proto__', 'prototype', 'constructor'].includes(key))) return null;
  const canonical = canonicalTool(name, original).name;
  if (!['Bash', 'Read', 'Write', 'Edit', 'Glob', 'Grep'].includes(canonical)) return null;
  const result = { ...original, ...updated };
  if (original.path !== undefined && ['Read', 'Write', 'Edit'].includes(canonical) && updated.file_path !== undefined) {
    result.path = updated.file_path;
    delete result.file_path;
  }
  if (canonical === 'Bash') {
    if (original.cmd !== undefined && updated.command !== undefined) {
      result.cmd = updated.command;
      delete result.command;
    }
    if (typeof (result.cmd ?? result.command) !== 'string') return null;
  }
  if (canonical === 'Edit') {
    if (original.oldText !== undefined && updated.old_string !== undefined) {
      result.oldText = updated.old_string;
      delete result.old_string;
    }
    if (original.newText !== undefined && updated.new_string !== undefined) {
      result.newText = updated.new_string;
      delete result.new_string;
    }
  }
  // Amp exposes opaque tool inputs; mutate only fields whose native shape is present.
  for (const [key, value] of Object.entries(result)) {
    if (!(key in original)) return null;
    if (typeof value !== typeof original[key] || Array.isArray(value) !== Array.isArray(original[key])) return null;
    if (typeof value === 'number' && !Number.isFinite(value)) return null;
    if (value !== null && typeof value === 'object') return null;
  }
  return result;
}

function lastAssistant(messages) {
  const message = [...messages].reverse().find(message => message.role === 'assistant');
  return message ? message.content.filter(block => block.type === 'text').map(block => block.text).join('\n') : '';
}

function appendContext(output, context) {
  if (typeof output === 'string') return `${output}\n\n${context}`;
  if (output === undefined) return context;
  // Keep structured native results rather than discarding their original value.
  return { output, additionalContext: context };
}

export default function (amp) {
  const threads = new Map();

  async function notify(ctx, message) {
    try { await ctx.ui.notify(message); }
    catch { ctx.logger.log(message); }
  }

  function state(event) {
    const id = event.thread.id;
    if (!threads.has(id)) threads.set(id, {
      startupContext: '', startupError: '', toolContext: new Map(), rejectedTools: new Set(),
      continuationCount: 0, stopHookActive: false,
    });
    return threads.get(id);
  }

  async function run(eventName, event, ctx, values = {}) {
    if (!enabled() || !events[eventName]?.length) return null;
    const workspace = ctx.system.workspaceRoot;
    let cwd;
    try {
      if (workspace && workspace.toString().startsWith('file:')) cwd = amp.helpers.filePathFromURI(workspace);
    } catch {
      // Native URI conversion can reject non-local workspace roots.
    }
    if (typeof cwd !== 'string' || !isAbsolute(cwd)) {
      const reason = `Migrated ${plugin} hook cannot run: native workspace directory is unavailable.`;
      await notify(ctx, reason);
      if (eventName === 'UserPromptSubmit') await ctx.thread.cancel();
      return { deny: true, reason, context: '', updatedInput: null, stop: eventName === 'Stop' };
    }
    let result;
    try {
      const { runHooks } = await import(pathToFileURL(join(homedir(), runtimeRelative, 'runner.mjs')).href);
      result = await runHooks(config, eventName, { session_id: event.thread.id, cwd, ...values });
    } catch {
      const reason = `Migrated ${plugin} hook failed; the requested operation was not approved.`;
      await notify(ctx, reason);
      return { deny: true, reason, context: '', updatedInput: null, stop: eventName === 'Stop' };
    }
    if (result.notice) await notify(ctx, result.notice);
    return result;
  }

  amp.on('session.start', async (event, ctx) => {
    const resumed = threads.has(event.thread.id);
    const current = state(event);
    current.startupContext = '';
    current.toolContext.clear();
    current.rejectedTools.clear();
    current.continuationCount = 0;
    current.stopHookActive = false;
    const result = await run('SessionStart', event, ctx, { source: resumed ? 'resume' : 'startup' });
    current.startupContext = result?.context || '';
    current.startupError = result?.deny || result?.stop ? result.reason || 'Migrated hook stopped this session.' : '';
  });

  amp.on('agent.start', async (event, ctx) => {
    const current = state(event);
    if (enabled() && current.startupError) {
      await notify(ctx, current.startupError);
      await ctx.thread.cancel();
      return;
    }
    // Amp exposes no provenance flag: every native start includes hook-generated follow-ups.
    const result = await run('UserPromptSubmit', event, ctx, { prompt: event.message });
    if (result?.deny || result?.stop) {
      if (result.reason) await notify(ctx, result.reason);
      await ctx.thread.cancel();
      return;
    }
    const content = [current.startupContext, result?.context].filter(Boolean).join('\n\n');
    current.startupContext = '';
    if (enabled() && content) return { message: { content, display: false } };
  });

  amp.on('tool.call', async (event, ctx) => {
    const current = state(event);
    const tool = canonicalTool(event.tool, event.input);
    const shell = amp.helpers.shellCommandFromToolCall(event);
    if (shell) { tool.name = 'Bash'; tool.input = { ...tool.input, command: shell.command }; }
    const result = await run('PreToolUse', event, ctx, {
      tool_name: tool.name, tool_input: tool.input, tool_use_id: event.toolUseID,
    });
    if (!result) return;
    function reject(message) {
      current.toolContext.delete(event.toolUseID);
      current.rejectedTools.add(event.toolUseID);
      return { action: 'reject-and-continue', message };
    }
    if (result.deny || result.stop) return reject(
      [result.reason || 'Migrated hook blocked this tool call.', result.context].filter(Boolean).join('\n\n'),
    );
    if (result.updatedInput) {
      const input = nativeInput(event.tool, event.input, result.updatedInput);
      if (!input) return reject('Migrated hook returned an unsupported or invalid tool input update.');
      if (result.context) current.toolContext.set(event.toolUseID, result.context);
      return { action: 'modify', input };
    }
    if (result.context) current.toolContext.set(event.toolUseID, result.context);
  });

  amp.on('tool.result', async (event, ctx) => {
    const current = state(event);
    // Amp emits a native result even when our pre-hook prevented execution.
    if (current.rejectedTools.delete(event.toolUseID)) {
      current.toolContext.delete(event.toolUseID);
      return;
    }
    const preContext = current.toolContext.get(event.toolUseID);
    current.toolContext.delete(event.toolUseID);
    const tool = canonicalTool(event.tool, event.input);
    const shell = amp.helpers.shellCommandFromToolCall(event);
    if (shell) { tool.name = 'Bash'; tool.input = { ...tool.input, command: shell.command }; }
    const result = await run('PostToolUse', event, ctx, {
      tool_name: tool.name, tool_input: tool.input, tool_use_id: event.toolUseID,
      tool_response: { output: event.output, status: event.status, error: event.error },
    });
    const context = enabled() ? [preContext, result?.context].filter(Boolean).join('\n\n') : '';
    if (!context) return;
    const output = appendContext(event.output, context);
    return event.status === 'done' ? { status: event.status, output } : { status: event.status, output, error: event.error };
  });

  amp.on('agent.end', async (event, ctx) => {
    const current = state(event);
    // Retain correlation only through this run; aborted runs may omit tool results.
    current.rejectedTools.clear();
    current.toolContext.clear();
    if (event.status !== 'done') return;
    const result = await run('Stop', event, ctx, {
      stop_hook_active: current.stopHookActive,
      last_assistant_message: lastAssistant(event.messages),
    });
    if (!result || result.stop || !result.deny) {
      current.stopHookActive = false;
      current.continuationCount = 0;
      return;
    }
    if (current.continuationCount >= maxContinuations) {
      current.stopHookActive = false;
      current.continuationCount = 0;
      return;
    }
    const userMessage = [result.reason, result.context].filter(Boolean).join('\n\n') || 'Continue as requested by the migrated Stop hook.';
    current.continuationCount += 1;
    current.stopHookActive = true;
    return { action: 'continue', userMessage, maxContinuations };
  });
}
