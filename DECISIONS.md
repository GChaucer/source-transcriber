# Technical Decisions

## UI Framework: CustomTkinter (migrated from stock tkinter)

**Chosen:** CustomTkinter 5.x  
**Alternatives considered:** stock tkinter, PySide6/Qt  
**Why:**

Stock tkinter was rejected because:
- Cannot clip child widgets to rounded corners (no CSS box-model equivalent)
- OS-native widget chrome fights custom dark themes at the rendering level
- No smooth hover states, no hardware compositing, no shadow effects
- ScrolledText and Button widgets look dated regardless of color choices

PySide6/Qt was considered but deferred because:
- 60–80 MB dependency, more complex packaging for a local utility
- Qt QSS stylesheets achieve higher fidelity, but the extra code volume doesn't justify it at this scope
- Could be adopted in v2 if this app grows into something more ambitious

CustomTkinter was chosen because:
- Draws all widgets via Canvas → true rounded corners on every surface
- Real dark-mode theming with tinted hover/active states
- `CTkSegmentedButton`, `CTkTextbox` etc. look genuinely modern out of the box
- Same Python/pack mental model as tkinter — migration was fast
- ~5 MB install, no extra system deps

**Trade-off noted:** `CTkTextbox._textbox` is a private attribute used to access the underlying `tk.Text` widget for tag-based styling (timestamps vs body text). This is fragile against CTk major version bumps but widely used in the CTk community and acceptable for a local utility.

---

## Transcript rendering: tag-split (timestamps dim / body bright)

**Chosen:** Two-tag insertion via `._textbox`  
**Why:** The visual hierarchy of dim timestamps + bright spoken content is the most important in-app reading experience improvement. Plain single-color text reads like a log file. `spacing3=20` on the body tag creates document-like paragraph breaks without hard dividers.

---

## Chunk selector: CTkSegmentedButton

**Chosen:** CTkSegmentedButton ["5s", "8s", "15s", "30s"]  
**Alternatives:** OptionMenu dropdown  
**Why:** Shows all options at once, no click-to-reveal, more discoverable. More appropriate for a 4-option control in a permanent toolbar.

---

## Title inference: glossary-term extraction first

**Chosen:** Scan transcript for glossary matches (longest-match first), fall back to first-phrase slugging  
**Why:** Interview sessions often open with filler ("hi thanks for joining") that makes bad filenames. Glossary terms like "oauth-sso" or "rtb-attribution" are specific, meaningful, and far more useful as filename identifiers.

---

## Save dir: deterministic app data

**Why:** Output must never depend on the shell launch directory. Source runs stay project-local for development, while packaged `.app` runs write to the user's Application Support folder because app bundles should not mutate themselves.

**Implementation note:** Source mode writes to `./recordings/`. Packaged mode writes to `~/Library/Application Support/InterviewTranscriber/recordings/`.

---

## Autosave: after every chunk

**Why:** A crash mid-interview should not lose the session. Writing ~5 KB after each chunk is negligible overhead.

---

## Output: YAML frontmatter, `.md`, `.txt`, and sidecars

**Why:** `.md` is paste-ready for Markdown and Obsidian-style workflows. `.txt` is a universal fallback. YAML frontmatter keeps session metadata machine-readable without adding a database. Audio sidecars (`*_mic.wav`, `*_system.wav`, `*_mixed.wav`) provide a simple audit trail for Mic + System capture.

## Model selector: quality ladder

**Chosen:** `small`, `medium`, and `large-v3`

**Why:** `small` is the practical local default. `medium` and `large-v3` are available for users who want to trade slower local inference and larger first-time downloads for potentially better transcript quality.

## System audio: BlackHole first

**Chosen:** Capture System and Mic + System through BlackHole or an equivalent virtual input device.

**Why:** Native ScreenCaptureKit capture would require a larger permissions and packaging surface. BlackHole keeps v0.1 simple, local, and understandable. Native macOS loopback capture is documented as future architecture, not current behavior.
