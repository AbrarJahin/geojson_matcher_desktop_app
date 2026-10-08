# Repository research handoff

**HARD RULE (research):** Research work in this repo is **documentation-only**, limited to `specs/research/**` and `AGENTS.md`. **Never change application/source code, scripts, tests, dependencies, build/CI/release files, tags or artifacts for research tasks.** Do experiments outside the tracked repo; request a separate, explicitly approved software task for any implementation changes. Never mix code changes into research-only pushes. `tests.yml` must skip automatic CI for pushes/PRs containing *only* those documentation paths; code changes still run CI. A manual release after a docs-only commit still needs a manually triggered successful Tests run for that exact `master` SHA.

Road Matcher production code, tests, release artifacts and tags must stay unchanged during research documentation work unless explicitly requested. Default branch: `master`.

**Read only the selected research spec first:** `specs/research/01-risk-limiting-conflation.md` (Paper 1, current focus), `02-topology-routing-impact.md` (Paper 2), or `03-research-software.md` (Paper 3). These specs separate verified implementation, user-reported evidence, proposals and unknowns; do not infer data or results. Follow their code pointers only when needed. The older root README describes an obsolete 2%–30% review policy; current code uses two categories.

**Paper 1 next:** Stage 0 frozen Marion–Hamilton reproducibility audit; request missing private input/decision files instead of inventing results. Do not upload user-supplied research datasets to public GitHub without permission. Ask clarification questions singly.
