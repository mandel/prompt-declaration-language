# Diagnostic specs

One file per error ID, written by **diagnostic-designer** before any code is
touched: `<ERROR-ID>.md`.

A spec states the current output verbatim, the target output verbatim, the
structured record the target renders from, and — the part that stops specs being
fiction — where every field's data comes from, as `file:line`, at the point the
diagnostic is raised.

Format and rules are in
[`.claude/agents/diagnostic-designer.md`](../../../.claude/agents/diagnostic-designer.md).

## Citations are anchored, not current

A spec is a record of a decision, not a live map of the source. Its `file:line`
citations are pinned to the commit the spec was written against, named in a
blockquote under each title, and they are **not** updated as the code moves —
chasing them would mean re-verifying every spec on every refactor, and a silently
half-updated citation is worse than an openly old one.

Read a citation with `git show <anchor>:<path>`. Line numbers drift fast in this
repo — `call_python` moved 862 lines during Phase 3 alone — so when a citation and
a symbol name disagree, the symbol name is the one that was meant.
