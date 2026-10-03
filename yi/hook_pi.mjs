import { homedir } from 'node:os';
import { join } from 'node:path';
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

async function run(event, payload, ctx) {
  if (!enabled() || !events[event]?.length) return null;
  let result;
  try {
    const { runHooks } = await import(pathToFileURL(join(homedir(), runtimeRelative, 'runner.mjs')).href);
    result = await runHooks(config, event, payload);
  } catch {
    const reason = `Migrated ${plugin} hook failed; the requested operation was not approved.`;
    ctx.ui.notify(reason, 'error');
    return { deny: true, reason, context: '', updatedInput: null, stop: event === 'Stop' };
  }
  if (result.notice) ctx.ui.notify(result.notice, 'warning');
  return result;
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
    if (Array.isArray(mapped.edits) && mapped.edits.length === 1) {
      mapped.old_string = mapped.edits[0].oldText;
      mapped.new_string = mapped.edits[0].newText;
    }
  }
  return { name: canonical, input: mapped };
}

function nativeInput(name, original, updated) {
  if (Object.keys(updated).some(key => ['__proto__', 'prototype', 'constructor'].includes(key))) return null;
  const canonical = canonicalTool(name, original).name;
  const result = { ...original, ...updated };
  if (['read', 'write', 'edit'].includes(name) && updated.file_path !== undefined) {
    result.path = updated.file_path;
    delete result.file_path;
  }
  if (name === 'edit' && (updated.old_string !== undefined || updated.new_string !== undefined)) {
    if (!Array.isArray(original.edits) || original.edits.length !== 1) return null;
    result.edits = [{
      oldText: updated.old_string ?? original.edits[0].oldText,
      newText: updated.new_string ?? original.edits[0].newText,
    }];
    delete result.old_string;
    delete result.new_string;
  }
  if (canonical === 'Bash' && typeof result.command !== 'string') return null;
  if (['Read', 'Write', 'Edit'].includes(canonical) && typeof (result.path ?? result.file_path) !== 'string') return null;
  if (canonical === 'Write' && typeof result.content !== 'string') return null;
  if (name === 'edit' && (!Array.isArray(result.edits) || !result.edits.length ||
      result.edits.some(edit => !edit || typeof edit.oldText !== 'string' || typeof edit.newText !== 'string'))) return null;
  if (['Glob', 'Grep'].includes(canonical) && typeof result.pattern !== 'string') return null;
  for (const key of ['timeout', 'offset', 'limit', 'context']) {
    if (result[key] !== undefined && (typeof result[key] !== 'number' || !Number.isFinite(result[key]) || result[key] < 0)) return null;
  }
  for (const key of ['path', 'glob']) {
    if (result[key] !== undefined && typeof result[key] !== 'string') return null;
  }
  for (const key of ['ignoreCase', 'literal']) {
    if (result[key] !== undefined && typeof result[key] !== 'boolean') return null;
  }
  // Unknown custom tools have no reviewed input schema for hook-supplied mutation.
  if (!['Bash', 'Read', 'Write', 'Edit', 'Glob', 'Grep'].includes(canonical)) return null;
  return result;
}

function text(content) {
  if (typeof content === 'string') return content;
  return Array.isArray(content) ? content.filter(block => block.type === 'text').map(block => block.text).join('\n') : '';
}

function lastAssistant(messages) {
  const message = [...messages].reverse().find(message => message.role === 'assistant');
  return message ? text(message.content) : '';
}

export default function (pi) {
  let startupContext = '';
  let startupError = '';
  let pendingContext = [];
  let continuationCount = 0;
  let stopHookActive = false;

  function payload(ctx, values = {}) {
    return {
      session_id: ctx.sessionManager.getSessionId(),
      transcript_path: ctx.sessionManager.getSessionFile() || '',
      cwd: ctx.cwd,
      ...values,
    };
  }

  pi.on('session_start', async (event, ctx) => {
    startupContext = '';
    startupError = '';
    pendingContext = [];
    continuationCount = 0;
    stopHookActive = false;
    const sources = { startup: 'startup', reload: 'startup', new: 'clear', resume: 'resume', fork: 'resume' };
    const source = sources[event.reason] || 'startup';
    const result = await run('SessionStart', payload(ctx, { source }), ctx);
    startupContext = result?.context || '';
    startupError = result?.deny || result?.stop ? result.reason || 'Migrated hook stopped this session.' : '';
  });

  pi.on('before_agent_start', () => {
    if (!startupContext || !enabled()) return;
    const content = startupContext;
    startupContext = '';
    return { message: { customType: `yi-${plugin}-hooks`, content, display: false } };
  });

  pi.on('input', async (event, ctx) => {
    if (enabled() && startupError) {
      ctx.ui.notify(startupError, 'error');
      return { action: 'handled' };
    }
    if (event.source !== 'extension') {
      continuationCount = 0;
      stopHookActive = false;
    }
    const result = await run('UserPromptSubmit', payload(ctx, { prompt: event.text }), ctx);
    if (result?.deny || result?.stop) {
      if (result.reason) ctx.ui.notify(result.reason, 'warning');
      return { action: 'handled' };
    }
    if (result?.context) return { action: 'transform', text: `${result.context}\n\n${event.text}`, images: event.images };
  });

  pi.on('tool_call', async (event, ctx) => {
    const tool = canonicalTool(event.toolName, event.input);
    const result = await run('PreToolUse', payload(ctx, {
      tool_name: tool.name, tool_input: tool.input, tool_use_id: event.toolCallId,
    }), ctx);
    if (!result) return;
    if (result.deny || result.stop) return { block: true, reason: result.reason || 'Migrated hook blocked this tool call.' };
    if (result.updatedInput) {
      const updated = nativeInput(event.toolName, event.input, result.updatedInput);
      if (!updated) return { block: true, reason: 'Migrated hook returned an unsupported or invalid tool input update.' };
      Object.assign(event.input, updated);
    }
    if (result.context) pendingContext.push(result.context);
  });

  pi.on('context', event => {
    if (!enabled() || !pendingContext.length) return;
    const content = pendingContext.join('\n\n');
    pendingContext = [];
    return { messages: [...event.messages, {
      role: 'custom', customType: `yi-${plugin}-hooks`, content, display: false, timestamp: Date.now(),
    }] };
  });

  pi.on('tool_result', async (event, ctx) => {
    const tool = canonicalTool(event.toolName, event.input);
    const result = await run('PostToolUse', payload(ctx, {
      tool_name: tool.name, tool_input: tool.input, tool_use_id: event.toolCallId,
      tool_response: { content: event.content, details: event.details, isError: event.isError },
    }), ctx);
    if (!result?.context) return;
    return { content: [...event.content, { type: 'text', text: result.context }], details: event.details, isError: event.isError };
  });

  pi.on('agent_before_settle', async (event, ctx) => {
    if (event.outcome !== 'completed') return;
    // A terminal assistant message is not continuable until our custom entry is appended.
    const result = await run('Stop', payload(ctx, {
      stop_hook_active: stopHookActive,
      last_assistant_message: lastAssistant(event.context.contextMessages),
    }), ctx);
    if (!result) return;
    if (result.stop) return { continue: false };
    if (!result.deny || continuationCount >= maxContinuations) {
      stopHookActive = false;
      return;
    }
    continuationCount += 1;
    stopHookActive = true;
    return { entries: [...event.entries, {
      type: 'custom_message', customType: `yi-${plugin}-hooks`,
      content: [result.reason, result.context].filter(Boolean).join('\n\n') || 'Continue as requested by the migrated Stop hook.',
      display: false,
    }], continue: true };
  });

  pi.on('session_shutdown', async (event, ctx) => {
    const reasons = { quit: 'prompt_input_exit', reload: 'other', new: 'clear', resume: 'other', fork: 'other' };
    await run('SessionEnd', payload(ctx, { reason: reasons[event.reason] || 'other' }), ctx);
  });
}
