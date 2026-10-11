# Markdown style

Apply this guide after [development.md](development.md). markdownlint checks
it.

- Docs are agent context, so every token counts. State each rule once, where
  it belongs, and link to it elsewhere. Fix an overclaim by narrowing it, not
  by adding caveats.
- Specs and research are dated snapshots (`YYYY-MM-DD-<slug>.md`). Edit one
  only to fix a statement that would mislead current work, and say so in the
  commit; otherwise write a new dated doc.
- Wrap prose at 80 columns. Leave tables, long links, and code unwrapped.
- Tag every opening code fence with a language; use `text` when none applies.
- Use repository-relative links, and check that each target resolves.
- Give every heading in a file distinct text.
- A doc with images is a folder: `README.md` plus `assets/`, each image with
  alt text that names what it shows.

## In this repository

- `make lint` doesn't run markdownlint here. Check a file you changed with
  `npx --yes markdownlint-cli2 <file>`.
- Plans are dated snapshots too, in `docs/plans/`.
