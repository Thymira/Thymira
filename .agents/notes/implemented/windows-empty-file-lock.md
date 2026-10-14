# Lock an empty file without writing a sentinel

## Problem

The concurrent approval/clarification regression exposed a preexisting race in `FileLock.__enter__`.
Two handles could observe an empty lock file. One would write the sentinel and acquire byte zero;
the other would flush its own sentinel into that now-locked range. Windows raised `PermissionError`
before the lock's contention handler, leaving the handle unclosed.

## Decision

Remove sentinel writes. Seek to byte zero and acquire the existing nonblocking native lock directly.
Windows supports locking a byte range beyond EOF, including an empty file; POSIX whole-file locking
also needs no sentinel. Keep the same filename, lock range, cleanup and `OwnerBusyError` contract.
Existing one-byte lock files remain compatible.

Source: Microsoft's [_locking documentation](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/locking?view=msvc-170).

## Alternatives considered

- Retrying the failed flush would hide a race outside the lock protocol.
- Initializing after acquiring would work but retains an unnecessary file write.
- A thread-only mutex cannot serialize independent backend processes.

## Consequences

Native Windows regression tests hold byte zero on both empty and populated files through another
handle, then require a clean contention error and successful reacquisition after release. The exact
approval-versus-clarification test also passes. No system permissions or stored Run data change.
