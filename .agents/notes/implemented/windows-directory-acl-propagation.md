# Avoid directory ACL propagation over concurrent state writes

## Problem

Run `run_f81ed581733d4fcdb36cd446cf0e5365` failed while securing `.run-n6hz7ad9.tmp` before its
projection bytes were written. Its ACL contained inherited entries instead of the exact protected
entries required by private storage. An isolated Windows race reproduced the same failure when
the parent directory and new child file received concurrent DACL updates. A deterministic test
also showed that securing the parent changed an existing child's ACL.

## Decision

For directory DACL writes, open the exact existing directory with `MAXIMUM_ALLOWED`, shared access
and the reparse-point flag, then call `SetSecurityInfo` through the handle. Microsoft documents
that this access-mask choice suppresses inheritance propagation to existing children. Keep OICI
entries for future children, close the handle in all outcomes, and retain the exact protected
DACL readback check and trustee allowlist. Files keep their existing setter. Every storage writer
continues to secure its own target before publishing bytes.

Sources: [SetSecurityInfo](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-setsecurityinfo),
[SetNamedSecurityInfoW](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-setnamedsecurityinfow).

## Alternatives considered

- Accepting inherited or broad ACLs would hide an actual permission-state race.
- Retrying or sleeping could mask the failure without preventing interference with child writes.
- A thread-only lock cannot coordinate independent local runtime processes.
- Global cross-process locking adds a new coordination lifecycle when the native API already
  supports writing one object without propagation.
- `SetFileSecurityW` also avoids propagation but is obsolete and changes the AI descriptor control
  bit; the supported handle-based API preserves the existing exact verification contract.

## Consequences

Securing a directory no longer modifies existing children as a side effect. Children must continue
to use their own storage permission primitive, as existing writers already do. No permissions are
relaxed, no stored Run is replayed, and no elevation or system configuration change is needed.
Tests exercise native Windows ACL readback and concurrent parent/child writes in isolated paths.
The security/persistence change receives an independent Sol xhigh review.
