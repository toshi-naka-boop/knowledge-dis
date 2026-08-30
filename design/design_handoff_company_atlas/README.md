# Handoff: Knowledge Discovery — Company Atlas UI

## Overview
"Company Atlas" is the approved signature visual language for Knowledge Discovery — a product where personal agents detect stalled work, autonomously sweep the organization for relevant people, and prepare (but never send) human introductions. The organization is rendered as a parchment nautical chart: departments are islands, people are ink dots, agent activity is dashed survey routes, and a human connection is a bridge crossing the sea between islands.

Design philosophy encoded in the visuals:
- **Humans are large. Agents are small.** People get serif names and the darkest ink; agent activity is thin dashed lines and footnotes.
- **AI finds the route. Humans build the bridge.** Discovered-but-unapproved connections are dashed; only mutual human consent renders a solid line.
- **The knowledge already exists. The bridge doesn't.**

## About the Design Files
The files in this bundle are **design references created in HTML** — prototypes showing the intended look, not production code to copy directly. Recreate these designs in the target codebase's existing environment (the current app is plain HTML/CSS/JS under `src/knowledge_discovery/web/` — requester.html, candidate.html, audit.html, ui.css). The islands/coastlines are SVG paths; regenerate or copy them as static SVG assets.

## Fidelity
**High-fidelity.** Colors, typography, spacing, and copy are final. Recreate pixel-perfectly at 1920×1080 (the canvas scales; keep proportions).

## Screens / Views

### 1. Bridge Trace — Signature Screen (`Company Atlas v3 - Parchment Chart.dc.html`)
The moment after the autonomous sweep: Marcus discovered, route proposed, no bridge yet. This is the Devpost screenshot.
- **Layout**: column — top bar 52px / SVG chart 1920×964 / trace drawer 64px. Introduction card floats top-right (absolute, right 56px, top 84px, width 344px).
- **Chart**: parchment sea #E4D5AC with wave-line pattern (stroke #B49E78), double-line outer frame + checkered border (#58412C / #E9DCB6), compass rose (geometric, bottom-left), cartouche "AN ATLAS OF KNOWLEDGE" (top-left).
- **Islands (5)**: CLINICAL, FINANCE, STRATEGY, OPERATIONS, REAL ESTATE. Fractal hand-drawn coastlines (see Assets). States: dormant (fill #EFE3C0, stroke #8A7354, label #A08A62), swept-then-released (fill #F2E7C6, stroke #7A6446), awakened (fill #F6EDCF, stroke #4A3722 2px + 7px 13%-opacity echo, label #3B2C1D).
- **People**: anonymous dots r=3–3.2 (#AD9770 dormant / #5C4A32 awakened). Jordan: r=13 filled #26221A + 26px serif name. Marcus: r=12 ring stroke #A13A20 3px (outlined = not yet contacted) + 26px serif name.
- **Released candidates**: Elena Park (Finance), Tom Nguyen (Technology-area) at 60% opacity with caption "released · reason".
- **Need signal**: concentric circles from Jordan (stroke #58412C, opacity 0.35→0.06), drawn *beneath* island fills so they read as sea swell.
- **Proposed crossing**: dashed #A13A20, stroke-width 2.2, dasharray "10 9" over open sea, fading to "2 13" at Marcus's coast; small coastline tick marks at both shores; italic serif caption "proposed crossing — no bridge exists between these islands".
- **Introduction card**: bg #F8F1DC, border 1px #A8946C, heading "INTRODUCTION PREPARED — NOT SENT", serif italic evidence sentence, button "Ask Marcus for 15 min" (#1F3A5F, no radius), caption "Nothing is sent until you decide."
- **Trace drawer**: one-line serif trace with superscript footnote numbers; "Full trace ▴" link right.

### 2. My Agent — Jordan (`Company Atlas - Screen Suite.dc.html` section 2a)
- **Layout**: top bar / two panes: left rail 640px (bg #EDE1BE, border-right #C3AF85) + chart detail (rest).
- **Left rail**: "YOUR AGENT NOTICED ¹" card (stall description, red pulse dot + "Sweeping the organization…"), Autonomy Policy card (4 ✓ items + hatched Human Boundary row "Contacting a person — always ask me first"), footnote trace link.
- **Chart detail**: zoomed corner of the same atlas — Operations coast bleeding off left/bottom edge, Finance fragment top-right, signal rings, 3 dashed sweep routes heading off-chart, caption "YOUR CORNER OF THE ATLAS", link "view full atlas ›".

### 3. Connection Request — Marcus (section 2b)
- **Layout**: top bar / chart strip 330px / centered letter.
- **Chart strip**: Marcus's own coast entering from the right ("You" node, outlined red), incoming dashed route from the west, captions "SOMEONE FOUND A ROUTE TO YOUR ISLAND" and "a proposed crossing, from Operations — waiting on you".
- **Letter**: 780px, double-border (1.6px + 0.7px #58412C), centered: "AN INTRODUCTION · PREPARED BY JORDAN'S AGENT" / 32px serif "Jordan Lee needs your perspective." / italic context / meta row (JORDAN LEE · OPERATIONS · 15 MINUTES · THIS WEEK) / buttons "Accept the introduction" (#1F3A5F) + "Decline quietly" (ghost) / "Declining is invisible to Jordan." / footnote on what was shared vs kept private.

### 4. Connection Created (section 2c)
- Full atlas frame again, but quiet: only Operations + Real Estate islands remain (85% opacity), all dots/routes/rings gone. One **solid** #A13A20 line (3px) Jordan→Marcus with shore ticks. Caption "a path that didn't exist yesterday". Rotated (-2.5°) double-border stamp "INTRODUCED · 09:52" in #A13A20. Cartouche reads "one new crossing recorded / Operations ↔ Real Estate · the first in 3 years". Top text "the search is over — 415 people were never disturbed". Bottom drawer = LEDGER.

## Interactions & Behavior (for the demo build)
- Motion sequence on Bridge Trace: need pulse → signal rings expand (one at a time, ~1.8s ease-out) → islands awaken (label color darkens 0.6s) → sweep routes draw then fade for released candidates → dashed crossing draws (stroke-dashoffset) → on Ask: dash advances; on Accept: dash → solid, everything else fades over ~2s.
- No idle/looping animation. No particles.
- "Ask Marcus for 15 min" → Marcus's Connection Request view. Accept → Connection Created.

## State Management
Route states: `discovered` (dashed, partial), `asked` (dash advancing), `connected` (solid). Island states: `dormant` / `swept-released` / `awakened`. Person states: anonymous dot / named-released (60%) / named-active.

## Design Tokens
Colors:
- Sea/base bg: #E4D5AC · wave lines #B49E78
- Chrome bg: #F1E8CF · chrome border #C3AF85
- Island dormant #EFE3C0 / swept #F2E7C6 / awakened #F6EDCF
- Coastline dormant #8A7354 / swept #7A6446 / awakened #4A3722
- Primary text/ink: #26221A (also #3B2C1D headers)
- Muted text: #6E5A40, #8A7354
- People dots: #AD9770 (dormant), #5C4A32 (awakened)
- Connection / crossing / stamp: #A13A20 (text-on-parchment variant #7A3A22)
- Action blue: #1F3A5F
- Privacy/policy boundary: #5C6B57 hatch (repeating-linear-gradient 45°, 2px/5px) on #EBEAD3, border #A8AC8C
- Frame/cartography ink: #58412C · checker alt #E9DCB6
Typography:
- Serif (human names, island labels, letter copy, trace): "Source Serif 4" — names 26px/600, letter headline 32px/600, evidence italic
- Sans (UI, captions, buttons): "Archivo" — buttons 13.5px/600, captions 10.5–12px, island labels letterspaced 6–7px
- Rule: serif = humans and the chart's voice; sans = product chrome. Agent evidence is always footnotes (¹ ²).
Other: **no border-radius anywhere**; shadows only on floating cards (0 3px 14px rgba(59,44,29,.16–.18)); buttons are flat rectangles.

## Assets
- Island coastline SVG paths: embedded in the HTML files. They were generated by midpoint-displacement fractalization (3 iterations, amplitude ~0.16–0.18 of segment length, seeded PRNG) of coarse control polygons, then smoothed with quadratic curves. Copy the paths verbatim or regenerate with the same algorithm.
- Wave pattern, checker border, compass rose, cartouche: inline SVG in the files.
- Fonts: Google Fonts — Archivo (400–700), Source Serif 4 (400/600 + italics).

## Files
- `Company Atlas v3 - Parchment Chart.dc.html` — Signature Screen (Bridge Trace)
- `Company Atlas - Screen Suite.dc.html` — 2a My Agent / 2b Connection Request / 2c Connection Created (three 1920×1080 frames in one canvas doc)
- `Visual Directions.dc.html` — original 3-direction rationale (context only)
Note: `.dc.html` files contain a `<x-dc>` template + a support.js runtime specific to the design tool. Treat everything inside `<x-dc>` as the markup reference; ignore `support.js`.
