# Marketing

Generated into [`docs/assets/`](../../assets/) by `scripts/render_assets.py`
(`make assets`). Regenerate; never edit by hand.

- `social-preview.png`: the 1280×640 GitHub social card. Left: small lockup
  (mark at 1.6×, `benchspec` in 64px mono bold) and the two-line tagline in
  `brand-slate` on `brand-paper`. Right: a five-row take on the mark (right
  column orange, salmon, orange, gray, orange); it belongs to this card and is
  not a logo.
- `benchmark-terminal.png`: the README render of `benchspec run -- --count 3`
  and its benchmark matrix, on `term-body` with a `term-titlebar` bar and three
  dots (`term-prompt`, `brand-salmon`, `brand-gray`). Pass rates use the
  `term-green` / `term-yellow` / `term-red` bands. An animated GIF of the same
  render ships in the repo.
- Both still render in Menlo / DejaVu Sans Mono. On the next regeneration, use
  the bundled JetBrains Mono (400 for the tagline and terminal, 700 for the
  social wordmark) and the outlined wordmark.
