;;; asana-org-sync.el --- Bridge CLI process wrappers for Asana Org  -*- lexical-binding: t; -*-

;; Copyright (C) 2025  Asana Org contributors
;; Author: Asana Org Team
;; URL: https://github.com/zb-ss/asana-org
;; Version: 0.1.0
;; Package-Requires: ((emacs "28.1"))
;; Keywords: org, asana, sync, cli
;; SPDX-License-Identifier: GPL-3.0-or-later

;;; Commentary:
;; Process invocation wrappers for asana-org-bridge CLI commands.
;; All bridge communication flows through these functions.
;; Commands follow the JSON contract defined in docs/cli-contract.md

;;; Code:

(require 'json)
(require 'org)
(require 'seq)
(require 'org)

;; Forward declarations to avoid circular require
;; Functions from asana-org.el
(declare-function asana-org-call-json "asana-org")
(declare-function asana-org-call-json-with-stdin "asana-org")
(declare-function asana-org-log-info "asana-org")
(declare-function asana-org-log-warn "asana-org")
(declare-function asana-org-log-error "asana-org")
(declare-function asana-org-verify-bridge "asana-org")
(declare-function asana-org-get-bridge-path "asana-org")
(declare-function asana-org-generate-idempotency-key "asana-org")
(declare-function asana-org-render-preview "asana-org-render")
(declare-function asana-org-render-ai-summary "asana-org-render")
(declare-function asana-org-render--find-section-heading "asana-org-render")

;; Functions from asana-org.el (additional)
(declare-function asana-org-get-property "asana-org")

;; Variables/constants from asana-org.el
(defvar asana-org-bridge-binary)
(defvar asana-org-root-directory)
(defvar asana-org-batch-size)
(defvar asana-org-preview-buffer-name)
(defvar asana-org-prop-gid)
(defvar asana-org-confirm-threshold)
(defvar asana-org-prop-section-gid)

;;;; Bridge Command Definitions

(defconst asana-org-sync-commands
  '(("sync-pull" . "Pull tasks from Asana")
    ("detect-changes" . "Detect local org changes vs cached state")
    ("sync-preview" . "Preview pending changes")
    ("sync-apply" . "Apply mutations to Asana")
    ("move-task" . "Move task to project/section")
    ("comment-append" . "Append comment to task")
    ("relink" . "Relink task to new permalink URL")
    ("cache-prune" . "Prune old cache entries")
    ("status" . "Show sync health status")
    ("reconcile" . "Reconcile local snapshots against remote state")
    ("rebuild-cache" . "Rebuild snapshot cache from remote")
    ("validate" . "Validate org states against cached snapshots"))
  "Alist of available bridge commands and descriptions.")

;;;; JSON Envelope Parsing

(defun asana-org-sync--parse-response (response)
  "Parse and validate bridge JSON RESPONSE.
Returns the data payload or raises an error.
Handles success, partial, and error envelopes per cli-contract.md.
Partial status means some mutations succeeded, some failed - not an error."
  (let ((status (alist-get 'status response)))
    (cond
     ;; Status is returned as string from bridge JSON
     ((string= status "error")
      (let* ((error-obj (alist-get 'error response))
             (code (alist-get 'code error-obj))
             (message (alist-get 'message error-obj)))
        (asana-org-log-error "Bridge error [%s]: %s" code message)
        (signal 'asana-org-sync-failed
                (list (format "[%s] %s" code message)))))
     ((string= status "success")
      (alist-get 'data response))
     ((string= status "partial")
      ;; Partial success - some mutations applied, some failed
      ;; Log warning and return data (which contains results)
      (asana-org-log-warn "Partial success: some mutations failed")
      (alist-get 'data response))
     (t
      (asana-org-log-error "Unknown response status: %s" status)
      (signal 'asana-org-sync-failed (list "Invalid response from bridge"))))))

(defun asana-org-sync--format-error (error-obj)
  "Format ERROR-OBJ from bridge for user display.
Returns a human-readable error message."
  (let ((code (alist-get 'code error-obj))
        (message (alist-get 'message error-obj)))
    (pcase code
      ("INVALID_REQUEST"
       (format "Invalid request: %s" message))
      ("NOT_FOUND"
       (format "Not found: %s" message))
      ("CONFLICT"
       (format "Conflict detected: %s" message))
      ("RATE_LIMITED"
       (format "Rate limited: %s. Please wait before retrying." message))
      ("AUTH_ERROR"
       (format "Authentication failed: %s" message))
      ("INTERNAL_ERROR"
       (format "Server error: %s" message))
      (_
       (format "Error (%s): %s" code message)))))

;;;; Command Builders (sync prefix commands)

(defun asana-org-sync--build-sync-pull-args (&optional project-gid include-comments)
  "Build arguments for sync-pull command.
Optional PROJECT-GID filters by project (reserved for future use).
INCLUDE-COMMENTS includes stories/comments (reserved for future use)."
  ;; Build args - project and include-comments are accepted but currently
  ;; reserved for future expansion. CLI accepts them for forward compatibility.
  (let ((args (list "sync-pull" "--json")))
    (when project-gid
      (setq args (append args (list "--project" project-gid))))
    (when include-comments
      (setq args (append args (list "--include-comments"))))
    args))

(defun asana-org-sync--build-sync-preview-args (&optional _dry-run since)
  "Build arguments for sync-preview command.
_DRY-RUN is ignored since sync-preview is inherently a read-only preview.
SINCE filters changes since timestamp."
  ;; Note: sync-preview is inherently read-only/preview, so dry-run flag
  ;; doesn't apply the same way as sync-apply. The dry-run parameter
  ;; is accepted for API compatibility but not used.
  (let ((args (list "sync-preview" "--json")))
    (when since
      (setq args (append args (list "--since" since))))
    args))

(defun asana-org-sync--build-sync-apply-args (mutations &optional dry-run _batch-size)
  "Build arguments for sync-apply command.
MUTATIONS is list of mutation dictionaries to apply.
DRY-RUN simulates without making changes.
BATCH-SIZE is accepted for API compatibility and ignored."
  (ignore mutations)
  (let ((args (list "sync-apply")))
    (when dry-run
      (setq args (append args (list "--dry-run"))))
    (append args (list "--json" "-"))))

(defun asana-org-sync--build-move-task-args (task-gid from-list to-list)
  "Build arguments for move-task command.
TASK-GID is the task to move.
FROM-LIST is current list/section.
TO-LIST is target list/section."
  (list "move-task" task-gid "--from" from-list "--to" to-list "--json"))

(defun asana-org-sync--build-comment-args (task-gid text)
  "Build arguments for comment-append command.
TASK-GID is target task.
TEXT is comment text."
  (list "comment-append" task-gid "--body" text "--json"))

;;;; Pending Changes Storage (for apply workflow)

(defvar asana-org-sync--pending-changes nil
  "Storage for pending changes from preview.
Structure: ((:id . \"pc_001\") (:type . \"task_move\") ...)")
(defvar asana-org-sync--blocked-changes nil
  "List of blocked changes from preview.")
(defvar asana-org-sync--nonblocked-changes nil
  "List of non-blocked changes from preview.")

;;;; Direct Command Functions

(defun asana-org-sync-pull (&optional project-gid include-comments)
  "Pull tasks from Asana, optionally filtered by PROJECT-GID.
INCLUDE-COMMENTS if non-nil includes task stories (reserved for future use).
Returns the response envelope with tasks in data.tasks array."
  (interactive)
  (asana-org-log-info "Sync pull: project=%s" (or project-gid "all"))
  (let* ((args (asana-org-sync--build-sync-pull-args project-gid include-comments))
          (response (apply #'asana-org-call-json args))
          (data (asana-org-sync--parse-response response))
          ;; Tasks array is in data.tasks per cli-contract.md
          (tasks (alist-get 'tasks data))
          (summary (alist-get 'summary data)))
    (asana-org-log-info "Pulled %d tasks" (length tasks))
    (when (called-interactively-p 'any)
      (if summary
          (message "Pulled %d tasks from Asana (%d updated)"
                   (or (alist-get 'pulled summary) (length tasks))
                   (or (alist-get 'updated summary) 0))
        (message "Pulled %d tasks from Asana" (length tasks))))
    response))

(defun asana-org-sync-preview (&optional dry-run since)
  "Preview pending changes.
DRY-RUN and SINCE are accepted for API compatibility but currently
unused; detect-changes scans all org files unconditionally.
Runs detect-changes first to find org file modifications, then
stores pending changes in `asana-org-sync--pending-changes' for apply."
  (interactive)
  (ignore dry-run since)
  (asana-org-log-info "Sync preview")
  (let ((detect-response (asana-org-sync-detect-changes)))
    ;; detect-changes already stored results in pending-changes vars
    ;; and rendered the preview buffer, so just log and return
    (let* ((data (alist-get 'data detect-response))
           (pending-changes (or (alist-get 'pending_changes data) '())))
      (asana-org-log-info "Preview via detect-changes: %d total, %d blocked, %d non-blocked"
                          (length pending-changes)
                          (length asana-org-sync--blocked-changes)
                          (length asana-org-sync--nonblocked-changes))
      (when (called-interactively-p 'any)
        (message "Preview: %d changes, %d blocked"
                 (length pending-changes)
                 (length asana-org-sync--blocked-changes)))
      detect-response)))

(defun asana-org-sync--apply (&optional dry-run)
  "Apply pending changes to Asana.
DRY-RUN simulates without making changes.
Only applies non-blocked changes by default.
Requires preview to be run first.
Raises `asana-org-sync-failed' on bridge error or complete failure."
  (asana-org-log-info "Sync apply: dry-run=%s" dry-run)
  
  ;; Ensure we have pending changes (run preview if needed)
  (unless asana-org-sync--pending-changes
    (asana-org-log-warn "No pending changes stored, running preview first")
    (asana-org-sync-preview dry-run))
  
  (let* ((non-blocked asana-org-sync--nonblocked-changes)
         (blocked asana-org-sync--blocked-changes)
         (total-to-apply (length non-blocked)))
    
    ;; Check for blocked changes and warn user
    (when (> (length blocked) 0)
      (asana-org-log-warn "Skipping %d blocked changes" (length blocked))
      (message "Skipped %d blocked changes - resolve conflicts first"
               (length blocked)))
    
    ;; No changes to apply
    (when (= total-to-apply 0)
      (asana-org-log-info "No changes to apply")
      (message "No pending changes to apply")
      (signal 'asana-org-sync-failed (list "No pending changes to apply")))
    
    ;; Confirmation for large batches
    (when (> total-to-apply asana-org-confirm-threshold)
      (unless (y-or-n-p (format "Apply %d changes to Asana?" total-to-apply))
        (user-error "Apply cancelled")))
    
    ;; Build mutations payload
    ;; Use idempotency_key from preview (not id), as per cli-contract.md
    (let* ((mutations (mapcar (lambda (change)
                                (list
                                 (cons "idempotency_key" (or (alist-get 'idempotency_key change)
                                                             (alist-get 'id change)))
                                 (cons "type" (alist-get 'type change))
                                 (cons "payload" (alist-get 'proposed_state change))))
                              non-blocked))
            (request-payload (list
                             (cons "version" "1")
                             (cons "command" "sync-apply")
                             (cons "idempotency_key" (asana-org-generate-idempotency-key "req"))
                             (cons "mutations" mutations)))
           (json-payload (json-serialize request-payload))
            ;; Use stdin input mode: sync-apply --json -
           (args (list "sync-apply" "--json" "-"))
            (response (asana-org-call-json-with-stdin args json-payload))
            (status (alist-get 'status response))  ; Read status from top-level envelope
            (data (asana-org-sync--parse-response response))
            (results (alist-get 'results data)))
      
       ;; Process results
       ;; Handle status from top-level envelope: "success" | "partial" | "error"
       ;; Note: status is already extracted from top-level response
       (let* (;; Results are in data.results per cli-contract.md
              (results-data (or (alist-get 'results data) results))
              (applied-count (length (seq-filter (lambda (r)
                                                    (string= (alist-get 'status r) "applied"))
                                                  results-data)))
              (conflict-count (length (seq-filter (lambda (r)
                                                     (string= (alist-get 'status r) "conflict"))
                                                   results-data)))
              (error-count (length (seq-filter (lambda (r)
                                                  (string= (alist-get 'status r) "error"))
                                                results-data)))
              (failed-count (+ conflict-count error-count)))
        
        ;; Log based on overall status
        (cond
         ((string= status "partial")
          (asana-org-log-warn "Apply partial: %d applied, %d failed" applied-count failed-count))
         ((string= status "error")
          (asana-org-log-error "Apply failed: all mutations failed"))
         (t
          (asana-org-log-info "Apply: %d applied, %d failed" applied-count failed-count)))
        
        (when (called-interactively-p 'any)
          (cond
           ((string= status "partial")
            (message "Partial apply: %d succeeded, %d failed (check log)"
                     applied-count failed-count))
           ((string= status "error")
            (message "Apply failed: %d errors" failed-count))
           (t
            (message "Applied %d changes to Asana" applied-count))))
        
        ;; Clear pending changes after successful apply
        (setq asana-org-sync--pending-changes nil)
        (setq asana-org-sync--blocked-changes nil)
        (setq asana-org-sync--nonblocked-changes nil))
      
      response)))

(defun asana-org-sync-move (task-gid from-list to-list)
  "Move TASK-GID from FROM-LIST to TO-LIST."
  (interactive
   (list (read-string "Task GID: ")
         (read-string "From list: ")
         (read-string "To list: ")))
  (asana-org-log-info "Sync move: task=%s from=%s to=%s" task-gid from-list to-list)
  (let* ((args (asana-org-sync--build-move-task-args task-gid from-list to-list))
         (response (apply #'asana-org-call-json args))
         (data (asana-org-sync--parse-response response))
         (result (alist-get 'result data))
         (status (alist-get 'status result)))
    (if (string= status "applied")
        (progn
          (asana-org-log-info "Move successful")
          (when (called-interactively-p 'any)
            (message "Task moved to %s" to-list)))
      (signal 'asana-org-sync-failed (list "Move failed" result)))
    response))

(defun asana-org-sync-comment (task-gid text)
  "Append comment TEXT to TASK-GID."
  (interactive
   (list (read-string "Task GID: ")
         (read-string "Comment: ")))
  (asana-org-log-info "Sync comment: task=%s" task-gid)
  (let* ((args (asana-org-sync--build-comment-args task-gid text))
         (response (apply #'asana-org-call-json args))
         (data (asana-org-sync--parse-response response))
         (result (alist-get 'result data))
         (status (alist-get 'status result)))
    (if (string= status "applied")
        (progn
          (asana-org-log-info "Comment added")
          (when (called-interactively-p 'any)
            (message "Comment added to task")))
      (signal 'asana-org-sync-failed (list "Comment failed" result)))
    response))

(defun asana-org-sync-relink (task-gid new-permalink)
  "Relink TASK-GID to NEW-PERMALINK URL.
Updates the stored permalink for a task snapshot in the bridge database."
  (interactive
   (list (read-string "Task GID: ")
         (read-string "New permalink URL: ")))
  (asana-org-log-info "Sync relink: task=%s permalink=%s" task-gid new-permalink)
  (let* ((args (list "relink" task-gid "--permalink" new-permalink "--json"))
         (response (apply #'asana-org-call-json args))
         (data (asana-org-sync--parse-response response))
         (old-permalink (alist-get 'old_permalink data))
         (task-name (alist-get 'task_name data)))
    (asana-org-log-info "Relinked task %s (%s)" task-gid (or task-name "unknown"))
    (when (called-interactively-p 'any)
      (message "Relinked task %s: %s -> %s"
               task-gid
               (or old-permalink "(none)")
               new-permalink))
    response))

(defun asana-org-sync-ai-summary (task-gids &optional include-notes)
  "Get AI summary for TASK-GIDS via the bridge CLI.
When INCLUDE-NOTES is non-nil (default t), task notes are sent to the AI."
  (interactive
   (list (list (asana-org-get-property asana-org-prop-gid)) t))
  (asana-org-log-info "AI summary: %d tasks, include-notes=%s"
                      (length task-gids)
                      (if include-notes "yes" "no"))
  (let* ((args (append (list "ai-summary")
                       task-gids
                       (list "--json")
                       (unless include-notes (list "--no-include-notes"))))
         (response (apply #'asana-org-call-json args))
         (data (cdr (assq 'data response))))
    (asana-org-render-ai-summary data)
    response))

(defun asana-org-sync-cache-prune (&optional dry-run report)
  "Prune old cache entries via the bridge CLI.
DRY-RUN shows what would be pruned without deleting (default t).
REPORT includes detailed deletion counts."
  (interactive)
  (ignore report)
  (asana-org-log-info "Cache prune: dry-run=%s" (if dry-run "yes" "no"))
  (let* ((args (append (list "cache-prune" "--json")
                       (if dry-run
                           (list "--dry-run")
                         (list "--no-dry-run"))))
         (response (apply #'asana-org-call-json args))
         (data (asana-org-sync--parse-response response))
         (prune-report (alist-get 'report data))
         (snapshots (alist-get 'snapshots_deleted prune-report))
         (sync-runs (alist-get 'sync_runs_deleted prune-report))
         (mutations (alist-get 'mutations_deleted prune-report))
         (is-dry-run (eq (alist-get 'dry_run prune-report) t)))
    (when (called-interactively-p 'any)
      (message "%s: %d snapshots, %d sync runs, %d mutations"
               (if is-dry-run "Would prune" "Pruned")
               (or snapshots 0) (or sync-runs 0) (or mutations 0)))
    response))

;;;; Recovery Commands

(defun asana-org-sync-reconcile ()
  "Reconcile local snapshots against remote Asana state.
Compares cached task snapshots with current remote data and reports
any drift in key fields (completed, due_on, start_on, name)."
  (interactive)
  (asana-org-log-info "Running reconcile")
  (let* ((response (asana-org-call-json "reconcile" "--json"))
         (data (asana-org-sync--parse-response response))
         (drifted (alist-get 'drifted_tasks data))
         (missing (alist-get 'missing_remote data))
         (summary (alist-get 'summary data)))
    (asana-org-log-info "Reconcile: %d checked, %d drifted, %d missing"
                        (or (alist-get 'total_checked summary) 0)
                        (or (alist-get 'drifted summary) 0)
                        (or (alist-get 'missing summary) 0))
    (when drifted
      (dolist (d (if (vectorp drifted) (append drifted nil) drifted))
        (asana-org-log-warn "Drift: %s field %s snapshot=%s remote=%s"
                            (alist-get 'gid d)
                            (alist-get 'field d)
                            (alist-get 'snapshot_value d)
                            (alist-get 'remote_value d))))
    (when missing
      (dolist (gid (if (vectorp missing) (append missing nil) missing))
        (asana-org-log-warn "Missing from remote: %s" gid)))
    (when (called-interactively-p 'any)
      (message "Reconcile: %d checked, %d drifted, %d missing"
               (or (alist-get 'total_checked summary) 0)
               (or (alist-get 'drifted summary) 0)
               (or (alist-get 'missing summary) 0)))
    response))

(defun asana-org-sync-rebuild-cache ()
  "Rebuild the local snapshot cache from scratch.
Deletes all cached snapshots and re-fetches from remote.
Prompts for confirmation before proceeding."
  (interactive)
  (unless (y-or-n-p "Rebuild cache will delete all snapshots and re-fetch. Continue? ")
    (user-error "Rebuild cache cancelled"))
  (asana-org-log-info "Rebuilding cache")
  (let* ((response (asana-org-call-json "rebuild-cache" "--json" "--no-confirm"))
         (data (asana-org-sync--parse-response response))
         (deleted (or (alist-get 'snapshots_deleted data) 0))
         (created (or (alist-get 'snapshots_created data) 0)))
    (asana-org-log-info "Cache rebuilt: %d deleted, %d created" deleted created)
    (when (called-interactively-p 'any)
      (message "Cache rebuilt: %d deleted, %d created" deleted created))
    response))

(defun asana-org-sync-validate ()
  "Validate org task states against cached snapshots.
Extracts task states from org files and sends them to the bridge
validate command to check for mismatches and orphans."
  (interactive)
  (asana-org-log-info "Running validate")
  (let* ((task-states (asana-org-sync--extract-task-states))
         (request-payload (list (cons 'version "1")
                                (cons 'command "validate")
                                (cons 'tasks (vconcat task-states))))
         (json-payload (json-serialize request-payload))
         (args (list "validate" "--json" "-"))
         (response (asana-org-call-json-with-stdin args json-payload))
         (data (asana-org-sync--parse-response response))
         (mismatches (alist-get 'mismatches data))
         (summary (alist-get 'summary data)))
    (asana-org-log-info "Validate: %d total, %d valid, %d mismatched"
                        (or (alist-get 'total summary) 0)
                        (or (alist-get 'valid summary) 0)
                        (or (alist-get 'mismatched summary) 0))
    (when mismatches
      (dolist (m (if (vectorp mismatches) (append mismatches nil) mismatches))
        (asana-org-log-warn "Mismatch: %s field %s org=%s snapshot=%s"
                            (alist-get 'gid m)
                            (alist-get 'field m)
                            (alist-get 'org_value m)
                            (alist-get 'snapshot_value m))))
    (when (called-interactively-p 'any)
      (message "Validate: %d total, %d valid, %d mismatched"
               (or (alist-get 'total summary) 0)
               (or (alist-get 'valid summary) 0)
               (or (alist-get 'mismatched summary) 0)))
    response))

;;;; Batch Operations

(defun asana-org-sync-batch-apply (mutations &optional batch-size)
  "Apply MUTATIONS in batches of BATCH-SIZE.
MUTATIONS is a list of mutation objects following the sync-apply JSON contract.
Each mutation should have: type, idempotency_key, and payload.
Uses stdin input mode with proper JSON envelope per docs/cli-contract.md."
  (let* ((size (or batch-size asana-org-batch-size))
         (batches (seq-partition mutations size))
         (results nil))
    (dolist (batch batches)
      ;; Build proper JSON envelope per cli-contract.md
       (let* ((request-payload (list
                                (cons "version" "1")
                                (cons "command" "sync-apply")
                                (cons "idempotency_key" (asana-org-generate-idempotency-key "batch"))
                                (cons "mutations" batch)))
             (json-payload (json-serialize request-payload))
             (args (list "sync-apply" "--json" "-"))
             (response (asana-org-call-json-with-stdin args json-payload))
             (data (asana-org-sync--parse-response response))
             (results-list (or (alist-get 'results data) '()))
             (applied (seq-filter (lambda (r)
                                    (string= (alist-get 'status r) "applied"))
                                  results-list))
             (failed (seq-filter (lambda (r)
                                   (or (string= (alist-get 'status r) "conflict")
                                       (string= (alist-get 'status r) "error")))
                                 results-list)))
        (push (list :applied applied :failed failed) results)
        (asana-org-log-info "Batch applied: %d ok, %d failed"
                            (length applied) (length failed))))
    (nreverse results)))

;;;; Status Reporting

(defun asana-org-sync-status ()
  "Get sync status from bridge and display in log buffer."
  (interactive)
  (asana-org-log-info "Fetching sync status...")
  (let* ((response (asana-org-call-json "status" "--json"))
         (data (asana-org-sync--parse-response response))
         (sync-status (alist-get 'sync_status data))
         (last-pull (or (alist-get 'last_pull_at sync-status) "(never)"))
         (last-apply (or (alist-get 'last_apply_at sync-status) "(never)"))
         (snapshots (or (alist-get 'snapshot_count sync-status) 0))
         (unique (or (alist-get 'unique_tasks sync-status) 0))
         (pending (or (alist-get 'pending_mutations sync-status) 0))
         (failed (or (alist-get 'failed_mutations sync-status) 0))
         (db-size (alist-get 'db_size_bytes sync-status))
         (db-size-str (cond
                       ((null db-size) "unknown")
                       ((>= db-size (* 1024 1024))
                        (format "%.1f MB" (/ (float db-size) (* 1024 1024))))
                       ((>= db-size 1024)
                        (format "%.1f KB" (/ (float db-size) 1024)))
                       (t (format "%d B" db-size)))))
    (asana-org-log-info "--- Sync Status ---")
    (asana-org-log-info "Last pull:    %s" last-pull)
    (asana-org-log-info "Last apply:   %s" last-apply)
    (asana-org-log-info "Snapshots:    %d (%d unique tasks)" snapshots unique)
    (asana-org-log-info "Pending:      %d  Failed: %d" pending failed)
    (asana-org-log-info "DB size:      %s" db-size-str)
    (when (called-interactively-p 'any)
      (message "Sync status: %d snapshots, %d pending, %d failed — last pull %s"
               snapshots pending failed last-pull))
    response))

;;;; Section Resolution

(defun asana-org-sync--resolve-section-heading (section-gid)
  "Find the org heading with ASANA_SECTION_GID matching SECTION-GID.
Searches level-1 headings in the current buffer.
Return the point position of that heading, or nil if not found.
Delegates to `asana-org-render--find-section-heading'."
  (require 'asana-org-render)
  (asana-org-render--find-section-heading section-gid))

(defun asana-org-sync--section-name-for-gid (section-gid)
  "Return the heading text of the section with SECTION-GID, or nil."
  (save-excursion
    (let ((pos (asana-org-sync--resolve-section-heading section-gid)))
      (when pos
        (goto-char pos)
        (org-get-heading t t t t)))))

;;;; Clear Pending Changes

(defun asana-org-sync-clear-pending ()
  "Clear stored pending changes.
Useful if preview is stale and needs to be re-run."
  (interactive)
  (setq asana-org-sync--pending-changes nil)
  (setq asana-org-sync--blocked-changes nil)
  (setq asana-org-sync--nonblocked-changes nil)
  (asana-org-log-info "Cleared pending changes")
  (message "Cleared pending changes"))

;;;; Org State Extraction

(defun asana-org-sync--org-date-to-iso (timestamp)
  "Parse org TIMESTAMP to ISO date string.
Converts `<2026-03-15 Sat>' to `2026-03-15'.
Returns nil if TIMESTAMP is nil or empty."
  (when (and timestamp (stringp timestamp) (not (string-empty-p timestamp)))
    (if (string-match "\\([0-9]\\{4\\}-[0-9]\\{2\\}-[0-9]\\{2\\}\\)" timestamp)
        (match-string 1 timestamp)
      nil)))

(defun asana-org-sync--heading-to-task-state ()
  "Extract task state from org heading at point.
Returns an alist with keys: gid, completed, due_on, start_on, local_hash.
Returns nil if the heading has no ASANA_GID property."
  (let ((gid (org-entry-get (point) "ASANA_GID")))
    (when gid
      (let* ((todo-state (org-get-todo-state))
             (is-completed (and todo-state (string= todo-state "DONE")))
             (deadline-raw (org-entry-get (point) "DEADLINE"))
             (scheduled-raw (org-entry-get (point) "SCHEDULED"))
             (due-on (asana-org-sync--org-date-to-iso deadline-raw))
             (start-on (asana-org-sync--org-date-to-iso scheduled-raw))
             ;; Compute local hash from heading content
             (heading-text (org-get-heading t t t t))
             (body (org-get-entry))
             (hash-input (format "%s\n%s" (or heading-text "") (or body "")))
             (local-hash (secure-hash 'sha256 hash-input)))
        (list (cons 'gid gid)
              (cons 'completed (if is-completed t :json-false))
              (cons 'due_on (or due-on :json-null))
              (cons 'start_on (or start-on :json-null))
              (cons 'local_hash local-hash))))))

(defun asana-org-sync--extract-task-states (&optional file)
  "Extract task states from org FILE or all files in `asana-org-root-directory'.
Returns a list of alists, each with keys: gid, completed, due_on,
start_on, local_hash."
  (let ((files (if file
                   (list file)
                 (when (and (boundp 'asana-org-root-directory)
                            (file-directory-p asana-org-root-directory))
                   (directory-files asana-org-root-directory t "\\.org$"))))
        (result nil))
    (dolist (f files)
      (when (file-readable-p f)
        (with-temp-buffer
          (insert-file-contents f)
          (org-mode)
          (goto-char (point-min))
          (while (re-search-forward "^\\*+ " nil t)
            (beginning-of-line)
            (let ((state (asana-org-sync--heading-to-task-state)))
              (when state
                (push state result)))
            (forward-line 1)))))
    (nreverse result)))

;;;; Detect Changes Command

(defun asana-org-sync-detect-changes ()
  "Detect changes between local org files and cached Asana state.
Extracts task states from org files, sends them to the bridge
detect-changes command, and stores the result for preview/apply."
  (interactive)
  (asana-org-log-info "Detecting changes in org files")
  (let* ((task-states (asana-org-sync--extract-task-states))
         ;; Convert :json-false and :json-null for serialization
         (request-payload (list (cons 'version "1")
                                (cons 'command "detect-changes")
                                (cons 'tasks (vconcat task-states))))
         (json-payload (json-serialize request-payload))
         (args (list "detect-changes" "--json" "-"))
         (response (asana-org-call-json-with-stdin args json-payload))
         (data (asana-org-sync--parse-response response))
         (pending-changes (or (alist-get 'pending_changes data) '()))
         (summary (alist-get 'summary data))
         (warnings (alist-get 'warnings data)))

    ;; Store pending changes for preview/apply workflow
    (setq asana-org-sync--pending-changes pending-changes)
    (setq asana-org-sync--blocked-changes nil)
    (setq asana-org-sync--nonblocked-changes pending-changes)

    (asana-org-log-info "Detected %d changes (%d tasks scanned)"
                        (length pending-changes)
                        (length task-states))

    ;; Log warnings from bridge
    (when warnings
      (dolist (w (if (vectorp warnings) (append warnings nil) warnings))
        (asana-org-log-warn "detect-changes: %s" w)))

    ;; Render preview buffer with results
    (require 'asana-org-render)
    (asana-org-render-preview response)

    (when (called-interactively-p 'any)
      (if summary
          (message "Detected %d changes (status: %d, dates: %d)"
                   (or (alist-get 'total summary) (length pending-changes))
                   (or (alist-get 'status_changes summary) 0)
                   (or (alist-get 'date_changes summary) 0))
        (message "Detected %d changes" (length pending-changes))))
    response))

(provide 'asana-org-sync)

;;;; asana-org-sync.el ends here
