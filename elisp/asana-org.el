;;; asana-org.el --- Asana ↔ Org-mode integration for Doom Emacs  -*- lexical-binding: t; -*-

;; Copyright (C) 2025  Asana Org contributors
;; Author: Asana Org Team
;; URL: https://github.com/zb-ss/asana-org
;; Version: 0.1.0
;; Package-Requires: ((emacs "28.1") (transient "0.4.0"))
;; Keywords: org, asana, sync, productivity
;; SPDX-License-Identifier: GPL-3.0-or-later

;;; Commentary:
;; This package provides integration between Asana and Org mode.
;; It pulls tasks from Asana into Org files and provides commands
;; for syncing changes back to Asana.
;;
;; Primary commands:
;;   - asana-org-sync-pull      - Pull My Tasks into Org files
;;   - asana-org-sync-preview   - Preview outbound changes
;;   - asana-org-sync-apply     - Apply approved changes to Asana
;;   - asana-org-move-task     - Move task to different project/section
;;   - asana-org-comment-append - Append comment to task
;;
;; Configuration:
;;   Customize `asana-org-bridge-binary' to point to the bridge executable.
;;   Set `asana-org-root-directory' to your Org files root.

;;; Code:

(require 'subr-x)
(require 'json)
(require 'transient)

;;;; Custom Variables

(defgroup asana-org nil
  "Asana ↔ Org-mode integration."
  :group 'productivity
  :prefix "asana-org-")

(defcustom asana-org-bridge-binary "asana-org-bridge"
  "Path to the asana-org-bridge executable."
  :type 'string
  :group 'asana-org
  :safe #'stringp)

(defcustom asana-org-root-directory (expand-file-name "~/org/asana")
  "Root directory for Asana Org files."
  :type 'directory
  :group 'asana-org
  :safe #'stringp)

(defcustom asana-org-project-name-mapping nil
  "Alist mapping Asana project GIDs to Org file paths.
Each element is (PROJECT_GID . ORG_FILE_PATH)."
  :type '(alist :key-type string :value-type string)
  :group 'asana-org)

(defcustom asana-org-confirm-threshold 5
  "Number of changes at which confirmation is required."
  :type 'integer
  :group 'asana-org
  :safe #'integerp)

(defcustom asana-org-batch-size 20
  "Maximum mutations per apply command."
  :type 'integer
  :group 'asana-org
  :safe #'integerp)

(defcustom asana-org-dry-run t
  "When non-nil, runs commands in dry-run mode for first-time use."
  :type 'boolean
  :group 'asana-org
  :safe #'booleanp)

(defcustom asana-org-cache-directory (expand-file-name ".asana-org-cache" user-emacs-directory)
  "Directory for local cache files."
  :type 'directory
  :group 'asana-org
  :safe #'stringp)

(defcustom asana-org-redact-logs t
  "When non-nil, redact sensitive data from logs."
  :type 'boolean
  :group 'asana-org
  :safe #'booleanp)

;;;; Constants

(defconst asana-org-prop-gid "ASANA_GID"
  "Property name for Asana task GID.")

(defconst asana-org-prop-permalink "ASANA_PERMALINK"
  "Property name for Asana task permalink URL.")

(defconst asana-org-prop-remote-modified-at "ASANA_REMOTE_MODIFIED_AT"
  "Property name for remote modification timestamp.")

(defconst asana-org-prop-local-hash "ASANA_LOCAL_HASH"
  "Property name for local content hash.")

(defconst asana-org-prop-project-gid "ASANA_PROJECT_GID"
  "Property name for Asana project GID.")

(defconst asana-org-prop-section-gid "ASANA_SECTION_GID"
  "Property name for Asana section GID.")

(defconst asana-org-comments-drawer "ASANA_COMMENTS"
  "Drawer name for task comments.")

(defconst asana-org-buffer-name "*Asana Org*"
  "Name of the main Asana Org buffer.")

(defconst asana-org-preview-buffer-name "*Asana Org Preview*"
  "Name of the preview buffer.")

(defconst asana-org-process-buffer-name "*Asana Org Process*"
  "Name of the process output buffer.")

;;;; Error Handling

(define-error 'asana-org-error "Asana Org error" 'error)
(define-error 'asana-org-missing-binary "Bridge binary not found" 'asana-org-error)
(define-error 'asana-org-sync-failed "Sync operation failed" 'asana-org-error)
(define-error 'asana-org-conflict-detected "Conflict detected" 'asana-org-error)
(define-error 'asana-org-invalid-mapping "Invalid task mapping" 'asana-org-error)

;;;; Logging

(defun asana-org-log (level message &rest args)
  "Log MESSAGE with LEVEL and ARGS to the Asana Org buffer."
  (let* ((timestamp (format-time-string "%Y-%m-%d %H:%M:%S"))
         (formatted-message (asana-org--redact-sensitive-text (apply #'format message args)))
         (log-entry (format "[%s] [%s] %s\n" timestamp level formatted-message)))
    (with-current-buffer (get-buffer-create asana-org-buffer-name)
      (goto-char (point-max))
      (insert log-entry)
      (goto-char (point-max)))))

(defun asana-org--redact-sensitive-text (text)
  "Return TEXT with potentially sensitive tokens redacted."
  (if (not (bound-and-true-p asana-org-redact-logs))
      text
    (let ((redacted text))
      (dolist (pattern '("\\(--body[[:space:]]+\\)[^[:space:]]+"
                         "\\(--json[[:space:]]+-\\)"
                         "\\(idempotency_key[^=]*=[[:space:]]*\\)[^, )\\n]+"
                         "\\(\\<\\(?:PAT\\|token\\|Bearer\\)\\>\\)[^ ,)\\n]+"))
        (setq redacted
              (replace-regexp-in-string pattern "\\1***REDACTED***" redacted t)))
      redacted)))

(defun asana-org--sanitize-command-args (args)
  "Return ARGS safe for log output without sensitive values."
  (let ((remaining args)
        (sanitized nil))
    (while remaining
      (let ((arg (car remaining))
            (next (cadr remaining)))
        (cond
         ((string= arg "--body")
          (push arg sanitized)
          (push "***REDACTED***" sanitized)
          (setq remaining (cddr remaining)))
         ((and next (string= arg "--json") (string= next "-"))
          (push arg sanitized)
          (push "<stdin>" sanitized)
          (setq remaining (cddr remaining)))
         (t
          (push arg sanitized)
          (setq remaining (cdr remaining))))))
    (nreverse sanitized)))

(defun asana-org-generate-idempotency-key (&optional prefix)
  "Generate a high-entropy idempotency key with optional PREFIX."
  (let* ((tag (or prefix "req"))
         (entropy
          (if (file-readable-p "/dev/urandom")
              (with-temp-buffer
                (set-buffer-multibyte nil)
                (insert-file-contents-literally "/dev/urandom" nil 0 32)
                (secure-hash 'sha256 (current-buffer)))
            (secure-hash 'sha256
                         (format "%s:%s:%s:%s"
                                 (current-time)
                                 (emacs-pid)
                                 (user-uid)
                                 (random t)))))
         (token (substring entropy 0 24)))
    (format "%s_%s" tag token)))

(defun asana-org-log-info (message &rest args)
  "Log INFO level MESSAGE with ARGS."
  (apply #'asana-org-log "INFO" message args))

(defun asana-org-log-warn (message &rest args)
  "Log WARN level MESSAGE with ARGS."
  (apply #'asana-org-log "WARN" message args))

(defun asana-org-log-error (message &rest args)
  "Log ERROR level MESSAGE with ARGS."
  (apply #'asana-org-log "ERROR" message args))

;;;; Bridge Binary Verification

(defun asana-org-verify-bridge ()
  "Verify the bridge binary exists and is executable.
Raises `asana-org-missing-binary' if not found."
  (unless (executable-find asana-org-bridge-binary)
    (signal 'asana-org-missing-binary
            (list (format "Bridge binary '%s' not found. Please install asana-org-bridge."
                          asana-org-bridge-binary))))
  t)

(defun asana-org-get-bridge-path ()
  "Return the full path to the bridge binary."
  (or (executable-find asana-org-bridge-binary)
      (signal 'asana-org-missing-binary
              (list (format "Bridge binary '%s' not in PATH" asana-org-bridge-binary)))))

;;;; Process Invocation

(defun asana-org--run-command (args &optional input)
  "Run bridge command with ARGS and optional INPUT.
Returns (EXIT-CODE . OUTPUT).
Uses synchronous call-process to avoid async output race conditions."
  (asana-org-verify-bridge)
  (asana-org-log-info "Running: asana-org-bridge %s"
                      (mapconcat #'shell-quote-argument
                                 (asana-org--sanitize-command-args args)
                                 " "))
  (let* ((default-directory (or asana-org-root-directory default-directory))
         (exit-code nil)
         (output nil))
    (condition-case err
        (with-temp-buffer
          (if input
              ;; With stdin: write input to temp file, use as stdin
              (let ((input-file (make-temp-file "asana-org-input")))
                (unwind-protect
                    (progn
                      (with-temp-file input-file
                        (insert input))
                      (setq exit-code
                            (apply #'call-process
                                   (asana-org-get-bridge-path)
                                   input-file
                                   '(t nil)  ; stdout to buffer, discard stderr
                                   nil
                                   args)))
                  (delete-file input-file)))
            ;; Without stdin: simple synchronous call
            (setq exit-code
                  (apply #'call-process
                         (asana-org-get-bridge-path)
                         nil
                         '(t nil)  ; stdout to buffer, discard stderr
                         nil
                         args)))
          (setq output (string-trim (buffer-string))))
      (error
       (asana-org-log-error "Process failed: %s" (error-message-string err))
       (signal 'asana-org-sync-failed (list (error-message-string err)))))
    (cons exit-code output)))

(defun asana-org-call-json (subcommand &rest args)
  "Call bridge SUBCOMMAND with ARGS, parsing JSON response.
Returns parsed JSON or raises an error on failure.
Handles error envelopes per docs/cli-contract.md."
  (let* ((result (asana-org--run-command (cons subcommand args)))
         (exit-code (car result))
         (output (cdr result)))
    ;; First, try to parse JSON response
    (condition-case nil
        (let* ((response (json-read-from-string output))
               ;; Check for error envelope in response (status is string from bridge JSON)
               (status (alist-get 'status response)))
          ;; If bridge returned error envelope, extract error details before failing
          (when (string= status "error")
            (let ((error-msg (asana-org-parse-error-response response)))
              (asana-org-log-error "Bridge error: %s" error-msg)
              (signal 'asana-org-sync-failed (list error-msg))))
          ;; Non-zero exit code without error envelope
          (unless (eq exit-code 0)
            (asana-org-log-error "Command failed with exit code %d: %s" exit-code output)
            (signal 'asana-org-sync-failed (list (format "Exit code %d: %s" exit-code output))))
          response)
      (error
       ;; JSON parse failed
       (asana-org-log-error "Failed to parse JSON: %s" output)
       (signal 'asana-org-sync-failed (list "Invalid JSON response from bridge"))))))

(defun asana-org-call-json-with-stdin (args stdin-data)
  "Call bridge command with ARGS, sending STDIN-DATA as input.
Returns parsed JSON or raises an error on failure.
Handles error envelopes per docs/cli-contract.md.
Clears the process buffer before each invocation to ensure JSON parse
only sees current output, not stale data from previous calls."
  (asana-org-verify-bridge)
  (asana-org-log-info "Running: asana-org-bridge %s <stdin>"
                      (mapconcat #'shell-quote-argument
                                 (asana-org--sanitize-command-args args)
                                 " "))
  (let* ((default-directory (or asana-org-root-directory default-directory))
         (exit-code nil)
         (output nil))
    (condition-case err
        (let ((input-file (make-temp-file "asana-org-input")))
          (unwind-protect
              (progn
                (when stdin-data
                  (with-temp-file input-file
                    (insert stdin-data)))
                (with-temp-buffer
                  (setq exit-code
                        (apply #'call-process
                               (asana-org-get-bridge-path)
                               (when stdin-data input-file)
                               '(t nil)  ; stdout to buffer, discard stderr
                               nil
                               args))
                  (setq output (string-trim (buffer-string)))))
            (when (file-exists-p input-file)
              (delete-file input-file))))
      (error
       (asana-org-log-error "Process failed: %s" (error-message-string err))
       (signal 'asana-org-sync-failed (list (error-message-string err)))))
    ;; First, try to parse JSON response
    (condition-case nil
        (let* ((response (json-read-from-string output))
               ;; Check for error envelope in response (status is string from bridge JSON)
               (status (alist-get 'status response)))
          ;; If bridge returned error envelope, extract error details before failing
          (when (string= status "error")
            (let ((error-msg (asana-org-parse-error-response response)))
              (asana-org-log-error "Bridge error: %s" error-msg)
              (signal 'asana-org-sync-failed (list error-msg))))
          ;; Non-zero exit code without error envelope
          (unless (eq exit-code 0)
            (asana-org-log-error "Command failed with exit code %d: %s" exit-code output)
            (signal 'asana-org-sync-failed (list (format "Exit code %d: %s" exit-code output))))
          response)
      (error
       ;; JSON parse failed
       (asana-org-log-error "Failed to parse JSON: %s" output)
       (signal 'asana-org-sync-failed (list "Invalid JSON response from bridge"))))))

(defun asana-org-parse-error-response (response)
  "Parse and format error from bridge RESPONSE.
Returns a user-friendly error message or nil if no error."
  (let ((status (alist-get 'status response))
        (error-obj (alist-get 'error response)))
    ;; Status is returned as string from bridge JSON
    (when (string= status "error")
      (let ((code (alist-get 'code error-obj))
            (message (alist-get 'message error-obj))
            (details (alist-get 'details error-obj)))
        (cond
         ((string= code "INVALID_REQUEST")
          (format "Invalid request: %s" message))
         ((string= code "NOT_FOUND")
          (format "Not found: %s" message))
         ((string= code "CONFLICT")
          (format "Conflict: %s" message))
         ((string= code "RATE_LIMITED")
          (format "Rate limited: %s" message))
         ((string= code "AUTH_ERROR")
          (format "Authentication error: %s" message))
         ((string= code "INTERNAL_ERROR")
          (format "Server error: %s" message))
         (t
          (format "Error [%s]: %s" code message)))))))

;;;; Org File Path Strategy

(defun asana-org-get-project-file (project-gid)
  "Return the Org file path for PROJECT-GID.
Uses `asana-org-project-name-mapping' or defaults to root directory."
  (or (cdr (assoc project-gid asana-org-project-name-mapping))
      (expand-file-name (format "%s.org" project-gid) asana-org-root-directory)))

(defun asana-org-ensure-root-directory ()
  "Ensure the root Org directory exists."
  (unless (file-directory-p asana-org-root-directory)
    (make-directory asana-org-root-directory t))
  asana-org-root-directory)

(defun asana-org-get-task-file (task-gid)
  "Return the Org file containing TASK-GID.
Searches project files for tasks with matching ASANA_GID property."
  (let* ((root (asana-org-ensure-root-directory))
         (org-files (directory-files root t "\\.org$")))
    (seq-some (lambda (file)
                (with-temp-buffer
                  (insert-file-contents file)
                  (goto-char (point-min))
                  (when (re-search-forward (concat "^:ASANA_GID: *" (regexp-quote task-gid) "$") nil t)
                    file)))
              org-files)))

;;;; Property Helpers

(defun asana-org-get-property (property-name &optional point)
  "Get PROPERTY-NAME from org entry at POINT (defaults to point)."
  (org-entry-get (or point (point)) property-name t))

(defun asana-org-set-property (property-name value &optional point)
  "Set PROPERTY-NAME to VALUE on org entry at POINT (defaults to point)."
  (org-entry-put (or point (point)) property-name value))

(defun asana-org-collect-task-properties (gid)
  "Collect all Asana-related properties for task with GID."
  (list (cons asana-org-prop-gid gid)
        (cons asana-org-prop-permalink (asana-org-get-property asana-org-prop-permalink))
        (cons asana-org-prop-remote-modified-at (asana-org-get-property asana-org-prop-remote-modified-at))
        (cons asana-org-prop-local-hash (asana-org-get-property asana-org-prop-local-hash))
        (cons asana-org-prop-project-gid (asana-org-get-property asana-org-prop-project-gid))
        (cons asana-org-prop-section-gid (asana-org-get-property asana-org-prop-section-gid))))

;;;; Main Interactive Commands

;;;###autoload
(defun asana-org-sync-pull (&optional project-gid)
  "Pull Asana My Tasks and refresh Org mirror.
If PROJECT-GID is provided, only pull that project.
By default pulls only incomplete tasks."
  (interactive)
  (asana-org-log-info "Starting pull sync (project: %s)" (or project-gid "all"))
  (asana-org-ensure-root-directory)

  (let* ((args (list "sync-pull" "--json" "--incomplete-only")))
    (when project-gid
      (setq args (append args (list "--project" project-gid))))
    (let* ((response (apply #'asana-org-call-json args))
           (data (alist-get 'data response))
           (tasks (alist-get 'tasks data))
           (pulled-count (length tasks)))
      (asana-org-log-info "Pulled %d tasks" pulled-count)
      (message "Asana Org: Pulled %d tasks" pulled-count)
      (when (> pulled-count 0)
        (asana-org-render-tasks tasks))
      response)))

;;;###autoload
(defun asana-org-sync-preview ()
  "Compute and display outbound diff for current changes.
This runs 'sync-preview' to get pending changes from the bridge.
Changes are displayed in a preview buffer with:
- Blocked conflicts (top, cannot apply)
- Warnings
- Proposed mutations grouped by type"
  (interactive)
  (asana-org-log-info "Computing preview diff")
  (let* ((response (asana-org-call-json "sync-preview" "--json"))
         (status (alist-get 'status response)))
    ;; Check for error response (status is string from bridge JSON)
    (when (string= status "error")
      (let ((error-msg (asana-org-parse-error-response response)))
        (asana-org-log-error "Preview failed: %s" error-msg)
        (signal 'asana-org-sync-failed (list error-msg))))
    
    (asana-org-render-preview response)
    
    ;; Log summary
    (let* ((data (alist-get 'data response))
           (pending-changes (or (alist-get 'pending_changes data) '()))
           (blocked (seq-filter (lambda (c)
                                  (let ((conflict (alist-get 'conflict c)))
                                    (and conflict
                                         (alist-get 'blocking conflict))))
                                pending-changes)))
      (asana-org-log-info "Preview: %d total changes, %d blocked"
                          (length pending-changes) (length blocked))
      (message "Asana Org: %d changes, %d blocked"
               (length pending-changes) (length blocked)))
    ;; Return response from within let* scope
    response))

;;;###autoload
(defun asana-org-sync-apply ()
  "Apply approved changes to Asana.
This runs 'sync-apply' to execute non-blocked mutations.
Requires preview to be run first to store pending changes.
Skips blocked changes - resolve conflicts with pull first."
  (interactive)
  (require 'asana-org-sync)
  (asana-org-log-info "Starting apply")
  
  ;; Delegate to sync module's apply function
  (let ((response (asana-org-sync--apply)))
    (if (not response)
        (progn
          (asana-org-log-error "Apply failed: no response from bridge")
          (signal 'asana-org-sync-failed (list "Apply failed: no response from bridge")))
      (let* ((status (alist-get 'status response)))
        ;; Check for error envelope from bridge (status is string from JSON)
        (when (string= status "error")
          (let ((error-msg (asana-org-parse-error-response response)))
            (asana-org-log-error "Apply failed: %s" error-msg)
            (signal 'asana-org-sync-failed (list error-msg))))
        
        (let* ((data (alist-get 'data response))
               (results (alist-get 'results data))
               (applied (seq-filter (lambda (r)
                                      (string= (alist-get 'status r) "applied"))
                                    results))
               (failed (seq-filter (lambda (r)
                                     (or (string= (alist-get 'status r) "conflict")
                                         (string= (alist-get 'status r) "error")))
                                   results)))
          (asana-org-log-info "Applied: %d succeeded, %d failed" (length applied) (length failed))
          (message "Asana Org: Applied %d changes" (length applied))
          (when failed
            (asana-org-log-warn "Failed operations: %d" (length failed))
            (message "Failed: %d changes - check log for details" (length failed)))
          
          ;; Render results
          (require 'asana-org-render)
          (asana-org-render-apply-result response)
          
          response)))))

;;;###autoload
(defun asana-org-move-task (task-gid target-project-gid &optional target-section-gid)
  "Move TASK-GID to TARGET-PROJECT-GID.
If TARGET-SECTION-GID is provided, move to that section.

Uses bridge 'move-task' command per cli-contract.md.
Requests task_gid, from_list, and to_list from user."
  (interactive
   (list (or (asana-org-get-property asana-org-prop-gid)
             (read-string "Task GID: "))
         (read-string "Target project GID: ")
         (read-string "Target section GID (optional): ")))
  (unless task-gid
    (signal 'asana-org-invalid-mapping (list "No task GID at point")))
  
  (asana-org-log-info "Moving task %s to project %s (section: %s)"
                      task-gid target-project-gid (or target-section-gid "none"))
  
  ;; Build move-task command with JSON output
  (let* ((args (list "move-task" task-gid "--to" (or target-section-gid target-project-gid) "--json"))
         (response (apply #'asana-org-call-json args))
         (status (alist-get 'status response)))
    
    ;; Handle error response (status is string from bridge JSON)
    (when (string= status "error")
      (let ((error-msg (asana-org-parse-error-response response)))
        (asana-org-log-error "Move failed: %s" error-msg)
        (signal 'asana-org-sync-failed (list error-msg))))
    
    (let* ((data (alist-get 'data response))
           (result (alist-get 'result data))
           (result-status (alist-get 'status result)))
      (if (string= result-status "applied")
          (progn
            (asana-org-log-info "Task moved successfully")
            (message "Task moved to project %s" target-project-gid))
        (asana-org-log-error "Move failed: %s" result)
        (signal 'asana-org-sync-failed (list "Move operation failed"))))
    ;; Return response from within let* scope
    response))

;;;###autoload
(defun asana-org-comment-append (task-gid comment)
  "Append COMMENT to TASK-GID.

Uses bridge 'comment-append' command per cli-contract.md."
  (interactive
   (list (or (asana-org-get-property asana-org-prop-gid)
             (read-string "Task GID: "))
         (read-string "Comment: ")))
  (unless task-gid
    (signal 'asana-org-invalid-mapping (list "No task GID at point")))
  
  (asana-org-log-info "Appending comment to task %s" task-gid)
  
  ;; Build comment-append command with JSON output
  (let* ((args (list "comment-append" task-gid "--body" comment "--json"))
         (response (apply #'asana-org-call-json args))
         (status (alist-get 'status response)))
    
    ;; Handle error response (status is string from bridge JSON)
    (when (string= status "error")
      (let ((error-msg (asana-org-parse-error-response response)))
        (asana-org-log-error "Comment failed: %s" error-msg)
        (signal 'asana-org-sync-failed (list error-msg))))
    
    (let* ((data (alist-get 'data response))
           (result (alist-get 'result data))
           (result-status (alist-get 'status result)))
      (if (string= result-status "applied")
          (progn
            (asana-org-log-info "Comment added successfully")
            (message "Comment added to task"))
        (asana-org-log-error "Comment failed: %s" result)
        (signal 'asana-org-sync-failed (list "Comment operation failed"))))
    ;; Return response from within let* scope
    response))

;;;###autoload
(defun asana-org-ai-summary (&optional task-gids)
  "Get AI summary for TASK-GIDS (or current task if none specified).
Uses Asana MCP for summaries."
  (interactive)
  (ignore task-gids)
  (user-error "Command 'ai-summary' is not supported by the bridge CLI"))

;;;; Rendering Helpers (delegated to asana-org-render)

(defun asana-org-render-tasks (tasks)
  "Render TASKS to Org files.
Delegates to `asana-org-render-tasks' in asana-org-render.el."
  (require 'asana-org-render)
  (asana-org-render-tasks tasks))

(defun asana-org-render-preview (preview-data)
  "Render PREVIEW-DATA to preview buffer.
Delegates to `asana-org-render-preview' in asana-org-render.el."
  (require 'asana-org-render)
  (asana-org-render-preview preview-data))

(defun asana-org-render-apply-result (apply-data)
  "Render APPLY results to buffer.
Delegates to `asana-org-render-apply-result' in asana-org-render.el."
  (require 'asana-org-render)
  (asana-org-render-apply-result apply-data))

(defun asana-org-render-ai-summary (summary-data)
  "Render AI SUMMARY-DATA to buffer.
Delegates to `asana-org-render-ai-summary' in asana-org-render.el."
  (require 'asana-org-render)
  (asana-org-render-ai-summary summary-data))

;;;; Minor Mode Definition

(define-minor-mode asana-org-mode
  "Asana Org mode for task synchronization."
  :lighter " AsanaOrg"
  :keymap (let ((map (make-sparse-keymap)))
            (define-key map (kbd "C-c a p") #'asana-org-sync-pull)
            (define-key map (kbd "C-c a v") #'asana-org-sync-preview)
            (define-key map (kbd "C-c a a") #'asana-org-sync-apply)
            (define-key map (kbd "C-c a m") #'asana-org-move-task)
            (define-key map (kbd "C-c a c") #'asana-org-comment-append)
            map)
  (asana-org-log-info "Asana Org mode activated"))

;;;; Load Submodules

(require 'asana-org-transient)
(require 'asana-org-render)
(require 'asana-org-sync)

;;;; Package Footer

(provide 'asana-org)

;;; asana-org.el ends here
