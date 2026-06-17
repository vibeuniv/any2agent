# Demo recording for any2agent — renders docs/demo.gif
#
# Prereq:  pip install -e .        (so the `any2agent` command is on PATH)
#          brew install vhs        (https://github.com/charmbracelet/vhs)
# Render:  vhs demo.tape           (run from the repo root)
#
# Shows: connect a FastAPI app -> auto-detect JWT auth -> scan routes ->
#        verify (coverage/accuracy) -> PASS.  ~18s.
# (Generated <project>.toolspec.json / .toml land in the cwd and are gitignored.)

Output docs/demo.gif

Set Shell "bash"
Set FontSize 16
Set Width 1180
Set Height 720
Set Padding 22
Set Theme "Catppuccin Mocha"
Set TypingSpeed 45ms

Type "# Point any2agent at a project — it figures out the rest" Sleep 900ms Enter
Type "any2agent connect --path examples/notes-api --base-url http://localhost:8000 --no-input" Sleep 700ms Enter
Sleep 5s

Type "# ↑ detected JWT auth, scanned all 5 routes, verified the tool set — write/delete flagged." Sleep 1200ms Enter
Sleep 2s
Type "# Then:  any2agent serve  → a chat agent over your API." Sleep 1500ms Enter
Sleep 2500ms
