---
history: ./session.jsonl
---

## Prompt

Greet the colleague from our earlier session by name.

## Assertions

- [ ] ./Greetings/Carol.md contains the exact line 'Hello, Carol!'
- [ ] Skill `hello` invoked
  - if: {BENCHSPEC_ARM} != {BENCHSPEC_BASELINE}
