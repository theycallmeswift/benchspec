# PR 44 review resolution

## Goal

Correct two user-facing contract errors without changing runtime behavior.

## Design

- Describe `/workspace` as the clean eval workdir while stating that the repository is
  readable, but not writable, at `/project`. Apply the correction consistently in the
  README, sandbox guide, and eval-authoring guide.
- Describe comparison as the recommended multi-arm configuration, not an invariant.
  Sets with a configured baseline report deltas; sets without one report absolute rates.
- Preserve the existing `/project` mount, optional-baseline schema, and reporting behavior.

## Verification

- Run `make test`.
- Run `make lint`.
- Confirm the corrected claims agree with the sandbox mount and reporting implementations.

