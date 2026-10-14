# Execute registered Python source revisions

## Problem

The Coding workflow wrote a script and separately resubmitted its body to run_python. These independent inputs could differ, and literal backslash-n separators caused repeated syntax failures. Quoting the saved file did not establish which bytes the interpreter received. The repetition limit contained expenditure but did not remove this second source-generation step.

## Decision

Add source_artifact_id as an alternative to inline code. Resolve an active registered Python CODE artifact in the same Run before policy review, verify its bounded bytes against size and SHA-256, reject credential-bearing or invalid source, and bind the exact source to the canonical tool intent. Execution uses those prepared bytes and verifies its source identity again; it never resolves a mutable workspace path as executable authority. A revised or unavailable artifact cannot use the old approval. Record source identity in execution evidence and link published outputs to that source. Preserve the legacy inline input and its canonical approval arguments.

Expose the registered artifact ID in write_file output seen by the model. Coding and Visualization send that returned ID rather than duplicating source. A corrected script is a newly registered revision. The repeat guard also recognizes repeated invalid references across restored agent history. Extract file input contracts from the oversized file-tools module while preserving their imports and behavior.

## Alternatives considered

A retry limit alone only contains cost. Adding more prompt instructions still leaves two separate source bodies. Globally unescaping code changes legitimate Python string contents. Executing a workspace filename permits the file to change while awaiting approval and loses the registered revision identity. Removing inline code would break existing callers and pending approval identities. A general tool-preparation plugin framework has only one named consumer here and was not introduced.

## Consequences

The saved source, reviewed source and interpreter input can now be matched through explicit identity and canonical bytes. The capability, sandbox and human approval controls remain in force. This prevents retranscription errors in the registered-source workflow, not every possible programming error in a model-authored script. Source selection remains limited to the current Run; cross-Run source execution needs a separately authorized import. Historical Runs and original records are unchanged.
