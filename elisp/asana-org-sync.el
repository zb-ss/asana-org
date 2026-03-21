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
(require 'seq)

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

;; Variables/constants from asana-org.el
(defvar asana-org-bridge-binary)
(defvar asana-org-root-directory)
(defvar asana-org-batch-size)
(defvar asana-org-preview-buffer-name)
(defvar asana-org-confirm-threshold)

;;;; Bridge Command Definitions

(defconst asana-org-sync-commands
  '(("sync-pull" . "Pull tasks from Asana")
    ("sync-preview" . "Preview pending changes")
    ("sync-apply" . "Apply mutations to Asana")
    ("move-task" . "Move task to project/section")
    ("comment-append" . "Append comment to task")
    ("cache-prune" . "Prune old cache entries"))
  "Alist of available bridge commands and descriptions.
Note: ai-summary, reconcile, rebuild-cache, validate, and status are not supported
by the current bridge CLI contract and have been removed.")

;;;; JSON Envelope Parsing

(defun asana-org-sync--parse-response (response)
  "Parse and validate bridge JSON RESPONSE.
Returns the data payload or raises an error.
Handles success, partial, and error envelopes per cli-contract.md.
Partial status means some mutations succeeded, some failed - not an error."
  (let ((status (alist-get 'status response))
        (command (alist-get 'command response)))
    (cond
     ;; Status is returned as string from bridge JSON
     ((string= status "error")
      (let* ((error-obj (alist-get 'error response))
             (code (alist-get 'code error-obj))
             (message (alist-get 'message error-obj))
             (details (alist-get 'details error-obj)))
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
        (message (alist-get 'message error-obj))
        (details (alist-get 'details error-obj)))
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
DRY-RUN shows what would happen without making changes.
SINCE filters changes since timestamp.
Stores pending changes in `asana-org-sync--pending-changes' for apply."
  (interactive)
  (asana-org-log-info "Sync preview: dry-run=%s" dry-run)
  (let* ((args (asana-org-sync--build-sync-preview-args dry-run since))
         (response (apply #'asana-org-call-json args))
         (data (asana-org-sync--parse-response response))
         (pending-changes (or (alist-get 'pending_changes data) '()))
         (version (alist-get 'version data))
         (command (alist-get 'command response)))
    (asana-org-log-info "Preview response: version=%s command=%s" version command)
    
    ;; Classify changes into blocked and non-blocked
    (setq asana-org-sync--pending-changes pending-changes)
    (setq asana-org-sync--blocked-changes
          (seq-filter (lambda (change)
                        (let ((conflict (alist-get 'conflict change)))
                          (and conflict
                               (alist-get 'blocking conflict))))
                      pending-changes))
    (setq asana-org-sync--nonblocked-changes
          (seq-filter (lambda (change)
                        (let ((conflict (alist-get 'conflict change)))
                          (or (not conflict)
                              (not (alist-get 'blocking conflict)))))
                      pending-changes))
    
    (asana-org-log-info "Preview: %d total, %d blocked, %d non-blocked"
                        (length pending-changes)
                        (length asana-org-sync--blocked-changes)
                        (length asana-org-sync--nonblocked-changes))
    
    ;; Render preview buffer
    (require 'asana-org-render)
    (asana-org-render-preview response)
    
    (when (called-interactively-p 'any)
      (message "Preview: %d changes, %d blocked"
               (length pending-changes)
               (length asana-org-sync--blocked-changes)))
    response))

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

(defun asana-org-sync-ai-summary (task-gids &optional include-notes)
  "Get AI summary for TASK-GIDS.
INCLUDE-NOTES includes task notes in the summary."
  (interactive)
  (ignore task-gids include-notes)
  (user-error "Command 'ai-summary' is not supported by the bridge CLI"))

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

;;;; Unsupported Commands (not in bridge CLI contract)

(defun asana-org-sync-reconcile ()
  "Reconcile local state with remote.
WARNING: This command is NOT supported by the current bridge CLI contract.
It will signal an error if called."
  (interactive)
  (user-error "Command 'reconcile' is not supported by the bridge CLI"))

(defun asana-org-sync-rebuild-cache ()
  "Rebuild cache from remote data.
WARNING: This command is NOT supported by the current bridge CLI contract.
It will signal an error if called."
  (interactive)
  (user-error "Command 'rebuild-cache' is not supported by the bridge CLI"))

(defun asana-org-sync-validate ()
  "Validate Asana Org configuration.
WARNING: This command is NOT supported by the current bridge CLI contract.
It will signal an error if called."
  (interactive)
  (user-error "Command 'validate' is not supported by the bridge CLI"))

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
  "Get sync status from bridge.
WARNING: This command is NOT supported by the current bridge CLI contract.
It will signal an error if called."
  (interactive)
  (user-error "Command 'status' is not supported by the bridge CLI"))

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

(provide 'asana-org-sync)

;;;; asana-org-sync.el ends here
