# Contributing

## The one rule that matters

**A change is verified by running it, never by deploying it.** A file copied
into place proves nothing. Before you open a pull request, the thing you
changed has been executed on a real machine.

This is not pedantry. The five real defects this project has had all shared
one shape: the service survived and kept answering, it simply answered things
that were not so. None of them would have been caught by reading the diff.

## Running the checks

```bash
./verify.sh          # what CI runs, identically: shape, ruff, pytest
```

`tests/` holds unit tests for the logic that can be checked without hardware:
map parsing, the load estimator, release-slot derivation, reading a server
log. They run anywhere, in under a second.

`fault-injection/` is a different thing entirely and **CI never runs it**.
Those scripts kill the service mid-load, corrupt its state file, delete model
weights from under a running container and fill the disk. They need a real
machine with real containers, and they will disrupt whatever is on it:

```bash
python3 fault-injection/break-turnstile.py    # 14 scenarios, the allocator
python3 fault-injection/break-chain.py        # 10 scenarios, everything around it
```

Both discover their own targets from `/slots`, so they adapt to your map.

## If you touch the probe

The probe is the foundation. Everything above it is confidently wrong if it is
wrong. Two traps are easy to reintroduce:

- **Read the START of a container's log**, not its tail, when identifying the
  model. The startup lines vanish from a `--tail` once a server has been up a
  few hours, and a healthy model then reports as diverging.
- **Read from the swap's own start instant** when following a load, using a
  Unix timestamp. `docker logs` returns the whole history, so the previous
  run's "listening on" is already there; and a bare date is interpreted in
  local time.

## Adding a guard

Every guard in this repository exists because something went wrong first, and
the comment above it says what. Keep that. A threshold with no story behind it
is a number somebody will change back.

If you add one, add the fault-injection scenario that provokes it in the same
pull request. A guard nobody has seen fire is a guard nobody knows works.

## Before you open a pull request

- The change has been executed, not only written.
- `README.md` still describes what the project does.
- `CHANGELOG.md` has an entry.
- `./verify.sh` prints `PASSED`.
- Comments, commit messages and documentation are in English.

## What CI checks, and what it does not

CI verifies shape, lint and the unit tests. It does **not** verify that a test
looks at the right thing — a test written alongside its code inherits that
code's blind spots. Green means the checks passed. It does not mean the change
is right.
