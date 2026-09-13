---
---

## Prompt

You are working in a workspace rooted at your current working directory. Greet Alice by name.

## Assertions

- [ ] ./Greetings/Alice.md contains the exact line 'Hello, Alice!'
- [ ] Skill `hello` invoked
  - if: {BENCHSPEC_ARM} != {BENCHSPEC_BASELINE}
- [ ] The greeting feels warm and personable, not curt or robotic
