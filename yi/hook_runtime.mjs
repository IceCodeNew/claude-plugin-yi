// Inert until the user opts in by plugin name through YI_ENABLE_MIGRATED_HOOKS.
import { spawn } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { dirname, isAbsolute, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const MAX_BYTES = 1024 * 1024;

function emptyResult() {
  return { deny: false, reason: '', context: '', updatedInput: null, stop: false, notice: '' };
}

/** Run migrated command hooks; input updates are separate from permission decisions. */
export async function runHooks(config, event, payload) {
  const enabled = (process.env.YI_ENABLE_MIGRATED_HOOKS || '').split(',').map(value => value.trim());
  if (!enabled.includes(config.plugin)) return emptyResult();
  if (!config || typeof config.plugin !== 'string' || !config.plugin || !isAbsolute(config.root || '')) {
    throw new Error('Hook configuration requires a plugin name and absolute root.');
  }
  const groups = config.events?.[event] || [];
  if (!Array.isArray(groups)) throw new Error('Hook event groups must be an array.');
  if (!payload || typeof payload !== 'object' || Array.isArray(payload) || !isAbsolute(payload.cwd || '')) {
    throw new Error('Hook payload requires an absolute cwd.');
  }
  const input = normalizeInput(payload, event);
  if (Buffer.byteLength(JSON.stringify(input)) > MAX_BYTES) throw new Error('Hook input exceeds the 1 MiB limit.');
  const commands = groups.filter(group => {
    if (!group || !Array.isArray(group.hooks)) throw new Error('Hook group requires a hooks array.');
    return matches(group.matcher, event, input);
  }).flatMap(group => group.hooks);
  for (const hook of commands) validateCommand(hook);
  const results = await Promise.all(commands.map(hook => execute(hook, config.root, input)));
  const aggregate = emptyResult();
  for (const result of results) {
    if (result.deny) {
      aggregate.deny = true;
      aggregate.reason = [aggregate.reason, result.reason].filter(Boolean).join('\n');
    }
    aggregate.context = [aggregate.context, result.context].filter(Boolean).join('\n');
    aggregate.notice = [aggregate.notice, result.notice].filter(Boolean).join('\n');
    aggregate.stop ||= result.stop;
    if (result.updatedInput !== null) aggregate.updatedInput = result.updatedInput;
  }
  return aggregate;
}

function matches(matcher, event, payload) {
  if (matcher === undefined || matcher === null || matcher === '' || matcher === '*') return true;
  if (typeof matcher !== 'string' || matcher.length > 512) throw new Error('Unsupported hook matcher.');
  // One non-group quantifier bounds backtracking; complex source regexes require review.
  const structure = matcher.replace(/\\.|\[(?:\\.|[^\]\\])*\]/g, 'x');
  if (/\\(?:[1-9]|k<)|\(\?|\)[*+?{]/.test(matcher)
    || /[{}]/.test(structure) || (structure.match(/[*+?]/g) || []).length > 1) {
    throw new Error('Unsupported hook matcher repetition.');
  }
  const value = ['PreToolUse', 'PostToolUse', 'PostToolUseFailure', 'PermissionRequest'].includes(event)
    ? payload.tool_name
    : event === 'SessionStart' ? payload.source
      : event === 'SessionEnd' ? payload.reason : payload.status;
  return new RegExp(matcher).test(typeof value === 'string' ? value.slice(0, 4096) : '');
}

function validateCommand(hook) {
  if (!hook || hook.type !== 'command' || typeof hook.command !== 'string' || !hook.command.trim()) {
    throw new Error('Only nonempty command hooks are supported.');
  }
  if (!['bash', 'sh', 'zsh', '/bin/bash', '/bin/sh', '/bin/zsh', '/usr/bin/bash', '/usr/bin/sh', '/usr/bin/zsh']
    .includes(hook.shell || 'bash')) throw new Error('Unsupported hook shell.');
  const timeout = hook.timeout ?? 60;
  if (typeof timeout !== 'number' || !Number.isFinite(timeout) || timeout <= 0 || timeout > 3600) {
    throw new Error('Hook timeout must be positive seconds, at most 3600.');
  }
}

function execute(hook, root, payload) {
  return new Promise((resolveResult, reject) => {
    const child = spawn(hook.shell || 'bash', ['-c', hook.command], {
      cwd: payload.cwd,
      env: { ...process.env, CLAUDE_PLUGIN_ROOT: root, PLUGIN_ROOT: root, CLAUDE_PROJECT_DIR: payload.cwd },
      stdio: ['pipe', 'pipe', 'pipe'],
      detached: process.platform !== 'win32',
    });
    const stdout = [];
    const stderr = [];
    let size = 0;
    let failure;
    const kill = () => {
      try {
        if (process.platform !== 'win32' && child.pid) process.kill(-child.pid, 'SIGKILL');
        else child.kill('SIGKILL');
      } catch (error) { if (error.code !== 'ESRCH') child.kill('SIGKILL'); }
    };
    const fail = message => { failure ||= new Error(message); kill(); };
    const timer = setTimeout(() => fail('Hook timed out.'), (hook.timeout ?? 60) * 1000);
    const capture = chunks => chunk => {
      size += chunk.length;
      if (size > MAX_BYTES) { fail('Hook output exceeds the 1 MiB limit.'); return; }
      chunks.push(chunk);
    };
    child.stdout.on('data', capture(stdout));
    child.stderr.on('data', capture(stderr));
    child.on('error', () => { clearTimeout(timer); reject(new Error('Hook shell could not start.')); });
    child.stdin.on('error', () => {}); // A hook may exit before consuming its input.
    child.stdin.end(JSON.stringify(payload));
    child.on('close', code => {
      clearTimeout(timer);
      if (failure) { reject(failure); return; }
      try {
        resolveResult(parseOutput(code, Buffer.concat(stdout).toString('utf8'),
          Buffer.concat(stderr).toString('utf8'), payload.hook_event_name));
      } catch (error) { reject(error); }
    });
  });
}

function parseOutput(code, stdout, stderr, event) {
  const result = emptyResult();
  const blocking = ['PreToolUse', 'PermissionRequest', 'UserPromptSubmit', 'Stop', 'SubagentStop'].includes(event);
  if (code === 2) {
    if (blocking) { result.deny = true; result.reason = stderr.trim() || 'Hook blocked this event.'; }
    else if (['PostToolUse', 'PostToolUseFailure'].includes(event)) result.context = stderr.trim();
    else result.notice = 'Hook exited with code 2; this event does not support blocking.';
    return result;
  }
  if (code !== 0) {
    result.notice = `Hook exited with code ${Number.isInteger(code) ? code : 'unknown'}.`;
    return result;
  }
  let output;
  try { output = JSON.parse(stdout); } catch { output = null; }
  if (!output || typeof output !== 'object' || Array.isArray(output)) {
    if (['SessionStart', 'UserPromptSubmit'].includes(event)) result.context = stdout.trim();
    return result;
  }
  const specific = output.hookSpecificOutput || {};
  const permission = specific.permissionDecision;
  if (permission === 'ask') throw new Error('Unsupported hook output: permissionDecision ask.');
  if (permission !== undefined && !['allow', 'deny'].includes(permission)) {
    throw new Error('Unsupported hook output: permissionDecision.');
  }
  const approval = event === 'PermissionRequest' ? specific.decision : undefined;
  if (approval !== undefined) {
    if (!approval || typeof approval !== 'object' || Array.isArray(approval)
      || !['allow', 'deny'].includes(approval.behavior)) {
      throw new Error('Unsupported hook output: PermissionRequest decision.');
    }
    if (approval.interrupt !== undefined || approval.updatedInput !== undefined || approval.updatedPermissions !== undefined) {
      throw new Error('Unsupported hook output: PermissionRequest decision controls.');
    }
  }
  if (permission === 'allow' || approval?.behavior === 'allow') {
    result.notice = 'Source hook approval overrides are not migrated.';
  }
  const denied = permission === 'deny' || approval?.behavior === 'deny' || output.decision === 'block';
  const reason = approval?.behavior === 'deny' ? approval.message
    : permission === 'deny' ? specific.permissionDecisionReason : output.reason;
  if (denied) {
    if (blocking) { result.deny = true; result.reason = typeof reason === 'string' ? reason : 'Hook blocked this event.'; }
    else if (['PostToolUse', 'PostToolUseFailure'].includes(event)) result.context = typeof reason === 'string' ? reason : '';
    else result.notice = 'Hook requested blocking; this event does not support blocking.';
  }
  if (specific.updatedInput !== undefined) {
    if (event !== 'PreToolUse' || !specific.updatedInput || typeof specific.updatedInput !== 'object'
      || Array.isArray(specific.updatedInput)) throw new Error('Unsupported hook output: updatedInput.');
    result.updatedInput = specific.updatedInput;
  }
  for (const [key, value] of [['systemMessage', output.systemMessage], ['additionalContext', specific.additionalContext]]) {
    if (value !== undefined && typeof value !== 'string') throw new Error(`Unsupported hook output: ${key}.`);
  }
  result.notice = [result.notice, output.systemMessage].filter(Boolean).join('\n');
  result.context = [result.context, specific.additionalContext].filter(Boolean).join('\n');
  result.stop = output.continue === false;
  return result;
}

function normalizeInput(payload, event) {
  const aliases = { shell: 'Bash', shell_command: 'Bash', exec_command: 'Bash', bash: 'Bash',
    read: 'Read', read_file: 'Read', write: 'Write', write_file: 'Write', edit: 'Edit', edit_file: 'Edit',
    glob: 'Glob', grep: 'Grep' };
  const name = payload.tool_name || payload.toolName || '';
  const input = payload.tool_input ?? payload.input ?? {};
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error('Hook tool input must be an object.');
  const canonical = { ...input };
  const tool = aliases[name] || name;
  if (tool === 'Bash' && canonical.command === undefined && typeof canonical.cmd === 'string') {
    canonical.command = canonical.cmd;
  }
  if (['Read', 'Write', 'Edit'].includes(tool) && canonical.file_path === undefined && typeof canonical.path === 'string') {
    canonical.file_path = canonical.path;
  }
  const result = { ...payload, hook_event_name: event, tool_name: tool, tool_input: canonical };
  if (typeof result.tool_response === 'string') {
    result.tool_response = { stdout: result.tool_response, stderr: null, exitCode: null };
  }
  return result;
}

async function readInput() {
  const chunks = [];
  let size = 0;
  for await (const chunk of process.stdin) {
    size += chunk.length;
    if (size > MAX_BYTES) throw new Error('Hook input exceeds the 1 MiB limit.');
    chunks.push(chunk);
  }
  const value = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Hook input must be an object.');
  }
  return value;
}

async function main() {
  const args = process.argv.slice(2);
  if (![4, 6].includes(args.length) || args[0] !== '--config' || args[2] !== '--event'
    || (args.length === 6 && (args[4] !== '--target' || args[5] !== 'codex'))) {
    throw new Error('Expected --config filepath --event HookEvent [--target codex].');
  }
  const target = args[5];
  const config = JSON.parse(await readFile(args[1], 'utf8'));
  config.root = resolve(dirname(resolve(args[1])), config.root || 'source');
  const event = args[3];
  const payload = await readInput();
  const result = await runHooks(config, event, payload);
  const output = {};
  const specific = {};
  if (result.deny) {
    if (event === 'PreToolUse') {
      specific.permissionDecision = 'deny';
      specific.permissionDecisionReason = result.reason;
    } else if (event === 'PermissionRequest') {
      specific.decision = { behavior: 'deny', message: result.reason };
    } else { output.decision = 'block'; output.reason = result.reason; }
  }
  if (result.updatedInput !== null) {
    specific.updatedInput = result.updatedInput;
    // Codex requires this to apply a rewrite, not to bypass its own approval policy.
    if (target === 'codex' && !result.deny) specific.permissionDecision = 'allow';
  }
  if (result.context) specific.additionalContext = result.context;
  if (Object.keys(specific).length) output.hookSpecificOutput = { hookEventName: event, ...specific };
  if (result.stop) output.continue = false;
  if (result.notice) output.systemMessage = result.notice;
  process.stdout.write(JSON.stringify(output) + '\n');
  // Codex parses decision JSON only on success; exit 2 instead consumes stderr.
  if (result.deny && target !== 'codex') process.exitCode = 2;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch(error => {
    // Only our protocol diagnostics are safe; never echo input or filesystem errors.
    const message = error.message.startsWith('Unsupported hook output:') ? error.message
      : 'Migrated hook runtime failed. Review the hook configuration.';
    process.stderr.write(message + '\n');
    process.exitCode = 1;
  });
}
