# Context menu Agent architecture

`right-click > Agent` should capture a small, explicit context envelope: app,
window, URL/origin when present, selected text, target role/name/bounds, DOM and
accessibility availability, screenshot crop, and a monotonically increasing
surface revision. The prompt and envelope start a mission in observe-only mode.

`surface_router.route_surface` makes the first deterministic choice:

- browser for normal web controls with DOM access;
- hybrid for canvas/media and native apps with accessibility metadata;
- vision when semantic structure is unavailable.

Every route has a recorded fallback. Browser actions use fresh snapshot IDs and
semantic target signatures. Vision actions use fresh screenshots and calibrated
screen coordinates. An action invalidates both representations; the next step
must observe again. Cross-origin navigation clears origin-bound identity, and an
account switch invalidates personalized plans. Submits, sends, purchases,
uploads, downloads, authentication, and destructive actions remain separately
capability-gated regardless of route or model confidence.

The UI should show the chosen mode, confidence, current origin/app, planned next
action, approval boundary, and compact evidence receipts. “Done” is available
only when the mission success predicate is supported by a post-action
observation—not merely by a dispatched click.
