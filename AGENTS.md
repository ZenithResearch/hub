# Agent Rules

These rules apply to this repository and all agent sessions within it.

---

## Changelog

Maintain `CHANGELOG.md` in [Keep a Changelog](https://keepachangelog.com) format.

**After each commit**, add an entry under `## [Unreleased]` using the format:

```
- <what changed> — <why it was changed / what problem it solves>
```

Categories: `### Added` · `### Changed` · `### Fixed` · `### Removed`

The *why* is required. The diff shows what changed — the changelog records the
reasoning that won't survive in the code.

Skip entries for: whitespace-only commits, immediately reverted commits,
lock file bumps with no behavioral intent change.

Never promote `[Unreleased]` to a version block without an explicit instruction.

If `CHANGELOG.md` does not exist yet, create it:

```markdown
# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)

## [Unreleased]
```
