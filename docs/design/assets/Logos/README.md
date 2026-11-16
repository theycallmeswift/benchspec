# Logos

Fixed fills (no `currentColor`): pick the file by ground. Colours, construction,
clear space and don'ts: [README › Logo](../../README.md#logo).

- `benchspec-mark.svg`: the 64×64 mark for light grounds (`brand-paper`, white,
  the light theme).
- `benchspec-mark-dark.svg`: the same geometry for dark grounds (navy,
  `term-body`, the dark theme).
- `benchspec-wordmark-light.svg`: mark plus `benchspec`, 250×64, for light
  grounds.
- `benchspec-wordmark-dark.svg`: the same for dark grounds.
- In UI chrome, draw the mark inline bound to `wm-*` instead of using a file.

In Markdown:

```html
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="benchspec-mark-dark.svg">
  <img alt="benchspec" src="benchspec-mark.svg" width="48" height="48">
</picture>
```
