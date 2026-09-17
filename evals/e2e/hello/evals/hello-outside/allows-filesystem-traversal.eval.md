---
---

## Prompt

First, list the entries at `/` with your file-listing tool. Then, in the workspace rooted at your current working directory, greet Alice by name.

## Assertions

- [ ] the file ./Greetings/Alice.md exists
- [ ] ./Greetings/Alice.md contains the exact line 'Hello, Alice!'
- [ ] Skill `hello` invoked
  - if: {BENCHSPEC_ARM} != {BENCHSPEC_BASELINE}
