// OpenCode 2.0.19: native Effect plugin, not the v1 @opencode-ai/plugin API.
import { Effect } from "effect";
import { Tool } from "@opencode/schema/tool";
import { fileURLToPath } from "node:url";
import { runHooks } from "../runner.mjs";

const config = {
  ...__YI_HOOK_CONFIG__,
  root: fileURLToPath(new URL("../source/", import.meta.url)),
};

const toolNames = {
  shell: "Bash", read: "Read", write: "Write", edit: "Edit", glob: "Glob", grep: "Grep",
};
const inputFields = {
  shell: { background: "run_in_background" },
  read: { path: "file_path" },
  write: { path: "file_path" },
  edit: { path: "file_path", oldString: "old_string", newString: "new_string", replaceAll: "replace_all" },
};

function enabled() {
  return (process.env.YI_ENABLE_MIGRATED_HOOKS ?? "").split(",").some((name) => name.trim() === config.plugin);
}

function sourceInput(tool, input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) return input;
  const result = { ...input };
  for (const [native, source] of Object.entries(inputFields[tool] ?? {})) {
    if (Object.hasOwn(result, native)) {
      result[source] = result[native];
      delete result[native];
    }
  }
  return result;
}

const updateFields = {
  shell: { command: "string", workdir: "string", timeout: "integer", run_in_background: "boolean" },
  read: { file_path: "string", offset: "integer", limit: "integer" },
  write: { file_path: "string", content: "string" },
  edit: { file_path: "string", old_string: "string", new_string: "string", replace_all: "boolean" },
  glob: { pattern: "string", path: "string", hidden: "boolean", limit: "integer" },
  grep: { pattern: "string", path: "string", include: "string", literal: "boolean", caseSensitive: "boolean", limit: "integer" },
};

function nativeInput(tool, original, updated) {
  // Only reviewed built-ins have a source-to-native updatedInput contract.
  // Original native fields remain untouched; the target still owns full validation.
  if (!Object.hasOwn(updateFields, tool)) return null;
  const fields = updateFields[tool];
  for (const [key, value] of Object.entries(updated)) {
    if (!Object.hasOwn(fields, key)) return null;
    if (fields[key] === "integer") {
      if (!Number.isInteger(value) || value < 0) return null;
    } else if (typeof value !== fields[key]) return null;
  }
  const result = { ...original, ...updated };
  for (const [native, source] of Object.entries(inputFields[tool] ?? {})) {
    if (Object.hasOwn(updated, source)) result[native] = updated[source];
    delete result[source];
  }
  return result;
}

function payload(ctx, event) {
  return {
    session_id: event.sessionID,
    cwd: ctx.location.directory,
    tool_name: Object.hasOwn(toolNames, event.tool) ? toolNames[event.tool] : event.tool,
    tool_use_id: event.id,
    tool_input: sourceInput(event.tool, event.input),
  };
}

function addContext(event, text) {
  if (!text) return;
  // Request-local developer context: do not mutate persisted user content or
  // opaque compaction parts. Native SessionContext.system uses SystemPart objects.
  event.system.push({ type: "text", text });
}

function appendResult(result, context) {
  if (!context) return;
  const content = result.content;
  // Native output-only results normally become JSON text. Preserve that content
  // before adding context rather than masking it by setting content to context.
  if (Array.isArray(content)) result.content = [...content, { type: "text", text: context }];
  else if (typeof content === "string") result.content = `${content}\n\n${context}`;
  else if (result.output !== undefined) result.content = `${JSON.stringify(result.output)}\n\n${context}`;
  else result.content = context;
  if (typeof result.output === "string") result.output += `\n\n${context}`;
}

function reportNotice(decision) {
  // Diagnostic stderr, not model context or a claim of native UI notification parity.
  if (decision.notice) console.warn(`[yi ${config.plugin} hooks] ${decision.notice}`);
}

/** @type {import("@opencode/plugin/effect").Plugin.Plugin} */
export default {
  id: `yi-${config.plugin}-hooks`,
  effect(ctx) {
    return Effect.gen(function* () {
      // Loading/registering this artifact does not grant permission to run source.
      if (!enabled()) return;
      const started = new Map();
      const pending = new Map();

      if (config.events.SessionStart || config.events.PreToolUse) {
        yield* ctx.session.hook("context", (event) => Effect.tryPromise({
          try: async () => {
            if (!enabled()) return;
            if (config.events.SessionStart && !started.has(event.sessionID)) {
              // Cache immediately so concurrent first requests cannot rerun startup.
              // First observed request is not native creation/resume detection.
              started.set(event.sessionID, runHooks(config, "SessionStart", {
                session_id: event.sessionID,
                cwd: ctx.location.directory,
                source: "startup",
              }).then((decision) => {
                reportNotice(decision);
                if (decision.deny || decision.stop || decision.updatedInput !== null) {
                  console.warn(`[yi ${config.plugin} hooks] SessionStart requested unsupported control; ` +
                    "OpenCode cannot honor it. Adapt the source hook before enabling it.");
                  return "";
                }
                return decision.context;
              }, () => {
                // Startup is an observer, not a native enforcement barrier. Cache
                // recovery too, so one failed command cannot poison every request.
                console.warn(`[yi ${config.plugin} hooks] SessionStart hook failed; ` +
                  "startup context is unavailable. Review the source hook.");
                return "";
              }));
            }
            const startup = started.get(event.sessionID);
            if (startup) {
              // Native context is request-local. Keep startup context visible on
              // later primary requests without re-executing the source command.
              addContext(event, await startup);
            }
            const context = pending.get(event.sessionID);
            pending.delete(event.sessionID);
            addContext(event, context);
          },
          catch: (error) => error,
        }).pipe(Effect.orDie));
      }

      if (config.events.PreToolUse) {
        yield* ctx.tool.hook("execute.before", (event) => {
          if (!enabled()) return Effect.void;
          return Effect.tryPromise({
            try: () => runHooks(config, "PreToolUse", payload(ctx, event)),
            catch: () => new Tool.Error({ message: "yi PreToolUse hook execution failed; tool was not run." }),
          }).pipe(Effect.flatMap((decision) => {
            reportNotice(decision);
            if (decision.stop) {
              return Effect.fail(new Tool.Error({
                message: "yi PreToolUse: continue:false cannot halt an OpenCode session; this tool call was rejected. " +
                  (decision.reason || "Adapt the source hook before enabling it."),
              }));
            }
            if (decision.deny) {
              return Effect.fail(new Tool.Error({ message: decision.reason || "Blocked by migrated hook." }));
            }
            const updated = decision.updatedInput === null
              ? event.input : nativeInput(event.tool, event.input, decision.updatedInput);
            if (updated === null) {
              return Effect.fail(new Tool.Error({
                message: "yi PreToolUse: updatedInput has an unreviewed tool or invalid native field; tool was not run.",
              }));
            }
            return Effect.sync(() => {
              // execute.before precedes native validation; mutate only reviewed updates.
              if (decision.updatedInput !== null) event.input = updated;
              if (decision.context) {
                const previous = pending.get(event.sessionID);
                pending.set(event.sessionID, previous ? `${previous}\n\n${decision.context}` : decision.context);
              }
            });
          }));
        });
      }

      if (config.events.PostToolUse) {
        yield* ctx.tool.hook("execute.after", (event) => {
          if (!enabled() || event.status !== "completed") return Effect.void;
          return Effect.tryPromise({
            try: async () => {
              const decision = await runHooks(config, "PostToolUse", {
                ...payload(ctx, event),
                // This preserves native result data, not an invented Claude response schema.
                tool_response: event.result,
              });
              reportNotice(decision);
              if (decision.stop || decision.updatedInput !== null) {
                throw new Error("yi PostToolUse: native event cannot honor this hook decision; adapt the source hook.");
              }
              // A post-tool block is feedback after the side effect, never an undo or pre-tool veto.
              const feedback = [decision.context, decision.deny ? decision.reason : ""].filter(Boolean).join("\n\n");
              appendResult(event.result, feedback);
            },
            catch: (error) => error,
          }).pipe(Effect.orDie);
        });
      }
    });
  },
};
