# Core compatibility contract

The adapter requires [AuroraView Core PR #497](https://github.com/try-auroraview/auroraview/pull/497).

- Public merged source commit: 0381061602ff2a326e3f541da24ad7756dea50a4
- Base commit: eab9508013e6d68e0e1521648ff98e378e26658a
- Initial event contract review: 436e2471 (adds asynchronous notification dispatch)
- The prior ce33cc2 candidate supplies the original lifecycle/RPC changes but
  lacks host event dispatch and is no longer sufficient for optional WebViews

No published Core version is currently declared compatible with this candidate.
Core's source version is based on 0.5.11; the released 0.5.11 wheel does not
contain this host contract. Packaging intentionally has no automatic Core dependency.
Select and validate an explicit build of the merged contract before attempting
native use. This document is a source dependency pin, not an installable release.

Native tool panels do not use Core and are independent of this dependency.

Core owns native WebView construction, JS assets, RPC envelopes, result/error
delivery, cross-thread command proxies and generic window lifecycle. Blender
only supplies scheduling, host handlers and view/session ownership.

Required behavior includes:

- set_call_dispatcher(schedule_callback) defers bound host callbacks to the
  Blender main-thread queue without changing Core's RPC envelope
- set_event_dispatcher(schedule_callback) schedules event notifications on that
  queue and rejects synchronous closing-veto registrations. Close, disconnect,
  dispatcher replacement and expired host sessions discard pending delivery
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
