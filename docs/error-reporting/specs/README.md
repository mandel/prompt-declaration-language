# Diagnostic specs

One file per error ID, written by **diagnostic-designer** before any code is
touched: `<ERROR-ID>.md`.

A spec states the current output verbatim, the target output verbatim, the
structured record the target renders from, and — the part that stops specs being
fiction — where every field's data comes from, as `file:line`, at the point the
diagnostic is raised.

Empty until Phase 3 begins. Format and rules are in
[`.claude/agents/diagnostic-designer.md`](../../../.claude/agents/diagnostic-designer.md).
