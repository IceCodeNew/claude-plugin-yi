---
name: usage
description: This skill shows local usage counts when the user asks for "yi statistics", "most used skills", or "查看调用统计".
argument-hint: "[--group plugin|component]"
disable-model-invocation: true
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/yi.py" usage *)
---

# Usage statistics

Run the local helper from this plugin's root:

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/yi.py" usage --json
```

Use `--group plugin` when plugin totals are requested. Explain that unqualified component names remain in component statistics but are excluded from plugin totals. Display returned items in their existing order: highest count first, then name. Preserve complete names. Report an empty database as no recorded invocations, not as a collection failure.

Explain that counts include manual and automatic observable invocations. Do not infer counts from this conversation or installed plugin lists. Do not read historical conversations automatically. Offer explicit historical import through the migration flow if requested.
