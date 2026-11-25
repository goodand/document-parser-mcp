---------------------------- MODULE JobRouting ----------------------------
(* Concern: late background results vs. the visible document, and export
   publication vs. existing results.  Scope note: one window, jobs A/B,
   preview + export results delivered in any order; file contents abstracted
   to the owning job id.  Hancom/MCP internals out of scope. *)
EXTENDS Naturals, FiniteSets

CONSTANTS Jobs, Targets, None, GUARD, FRESH_TARGET

VARIABLES current,   \* job shown in the window
          view,      \* job whose preview/page count is on screen
          inflight,  \* undelivered preview results (job ids)
          exporting, \* job -> target being staged, or None
          published, \* target -> job that owns the committed result, or None
          source,    \* job -> content of its source file (never written)
          reserved   \* targets promised to a live export (publish._RESERVED)

vars == <<current, view, inflight, exporting, published, source, reserved>>

Init == /\ current = None /\ view = None /\ inflight = {}
        /\ exporting = [j \in Jobs |-> None]
        /\ published = [t \in Targets |-> None]
        /\ source = [j \in Jobs |-> j] /\ reserved = {}

\* src: Desktop.cs Open() -> JobRegistry.open (gpdesk/jobs.py:43)
Open(j) == /\ current' = j /\ view' = None /\ inflight' = inflight \cup {j}
           /\ UNCHANGED <<exporting, published, source, reserved>>

\* src: Desktop.cs ShowPage revision guard /  JobRegistry.accept (gpdesk/jobs.py:62)
Deliver(j) == /\ j \in inflight
              /\ inflight' = inflight \ {j}
              /\ view' = IF ~GUARD \/ j = current THEN j ELSE view
              /\ UNCHANGED <<current, exporting, published, source, reserved>>

\* src: gpdesk/publish.py Stager.__init__ (unique_target)
StartExport(j, t) ==
    /\ current = j /\ exporting[j] = None
    /\ FRESH_TARGET => (published[t] = None /\ t \notin reserved)
    /\ exporting' = [exporting EXCEPT ![j] = t]
    /\ reserved' = reserved \cup {t}
    /\ UNCHANGED <<current, view, inflight, published, source>>

\* src: gpdesk/publish.py Stager.commit (os.replace onto a fresh path)
Commit(j) == /\ exporting[j] # None
             /\ published' = [published EXCEPT ![exporting[j]] = j]
             /\ exporting' = [exporting EXCEPT ![j] = None]
             /\ reserved' = reserved \ {exporting[j]}
             /\ UNCHANGED <<current, view, inflight, source>>

\* src: gpdesk/publish.py Stager.abort (cancel, disk full, locked file)
Abort(j) == /\ exporting[j] # None
            /\ exporting' = [exporting EXCEPT ![j] = None]
            /\ reserved' = reserved \ {exporting[j]}
            /\ UNCHANGED <<current, view, inflight, published, source>>

Next == \E j \in Jobs : Open(j) \/ Deliver(j) \/ Commit(j) \/ Abort(j)
                         \/ \E t \in Targets : StartExport(j, t)

Spec == Init /\ [][Next]_vars

\* ---- protocol-level properties (frozen; see Properties section of README) ----
ViewIsCurrent == view \in {None, current}
SourceUntouched == \A j \in Jobs : source[j] = j
NoOverwrite == [][\A t \in Targets : published[t] # None => published'[t] = published[t]]_vars

\* sanity: must be violated (shows commits are reachable)
SanityNeverPublished == \A t \in Targets : published[t] = None
=============================================================================
