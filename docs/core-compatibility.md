# Core compatibility contract

The adapter requires [AuroraView Core PR #497](https://github.com/try-auroraview/auroraview/pull/497).

- Public source commit: 7bfb45376c4428c6d3f36fc9361eca436e379d97
- Base commit: 11b3a29ad95a46cb22aaa604614de16da16bfc22
- Reviewed source tree: b3aee846455954892c7e3e6be98e6bd9936e8a5f
- Original reviewed candidate: a8a9b5fe6c6d20025e600ca5153c6e7babf6dbc2

No published Core version is currently declared compatible with this candidate.
Core's source version is based on 0.5.11; the released 0.5.11 wheel does not
contain this draft PR. Packaging intentionally has no automatic Core dependency.
Select and validate an explicit build of the proposed contract before attempting
native use. This document is a source dependency pin, not an installable release.

Core owns native WebView construction, JS assets, RPC envelopes, result/error
delivery, cross-thread command proxies and generic window lifecycle. Blender
only supplies scheduling, host handlers and view/session ownership.

Required behavior includes:

- set_call_dispatcher(schedule_callback) defers bound host callbacks to the
  Blender main-thread queue without changing Core's RPC envelope
- Bindings and dispatcher options survive Core's asynchronous native construction
- request_close() requests closure and wait(0) reports completion without
  declaring success while native delivery or host cleanup remains pending
- Close invalidates stale queued callbacks; reopen creates a fresh WebView
- Owner-created command proxies and EventEmitter preserve safe cross-thread use

The adapter checks required public methods before constructing an incompatible
view. A method-presence check cannot replace actual native acceptance tests.
Long-running handlers must not block Blender's main thread. A JavaScript timeout
does not undo host work already running.

The Windows floating-window path remains unverified. Linux/macOS background
creation is explicitly rejected. The independent experimental Linux GTK runtime
candidate is not included, enabled, or treated as a released dependency here.
