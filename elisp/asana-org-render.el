;;; asana-org-render.el --- Org rendering helpers for Asana tasks  -*- lexical-binding: t; -*-

;; Copyright (C) 2025  Asana Org contributors
;; Author: Asana Org Team
;; URL: https://github.com/zb-ss/asana-org
;; Version: 0.1.0
;; Package-Requires: ((emacs "28.1"))
;; Keywords: org, asana, rendering
;; SPDX-License-Identifier: GPL-3.0-or-later

;;; Commentary:
;; Functions for rendering Asana tasks as Org entries and
;; displaying preview/diff buffers.
;; Follows JSON contract defined in docs/cli-contract.md

;;; Code:

(require 'org)
(require 'org-element)
(require 'seq)
(require 'cl-lib)

;; Forward declarations to avoid circular require
;; Functions from asana-org.el
(declare-function asana-org-get-project-file "asana-org")
(declare-function asana-org-log-info "asana-org")
(declare-function asana-org-log-warn "asana-org")
(declare-function asana-org-get-task-file "asana-org")
(declare-function asana-org-ensure-root-directory "asana-org")

;; Variables/constants from asana-org.el
(defvar asana-org-prop-gid)
(defvar asana-org-prop-permalink)
(defvar asana-org-prop-remote-modified-at)
(defvar asana-org-prop-project-gid)
(defvar asana-org-prop-section-gid)
(defvar asana-org-prop-local-hash)
(defvar asana-org-comments-drawer)
(defvar asana-org-root-directory)
(defvar asana-org-confirm-threshold)
(defvar asana-org-preview-buffer-name)

;;;; Rendering Constants

(defconst asana-org-render-todo-keywords
  '(("TODO" . "In Progress")
    ("DONE" . "Completed"))
  "Mapping of Asana completion status to Org TODO keywords.")

(defconst asana-org-render-priority-map
  '(("high" . ?A)
    ("medium" . ?B)
    ("low" . ?C)
    ("none" . nil))
  "Mapping of Asana priority to Org priority.")

;;;; Task Rendering

(defun asana-org-render--seq-first (seq)
  "Return the first element of SEQ, which may be a list or vector.
Returns nil if SEQ is nil or empty."
  (cond
   ((null seq) nil)
   ((vectorp seq) (and (> (length seq) 0) (aref seq 0)))
   ((listp seq) (car seq))
   (t nil)))

(defun asana-org-render--memberships-to-list (memberships)
  "Coerce MEMBERSHIPS to a list, handling vectors from `json-read'."
  (cond
   ((null memberships) nil)
   ((vectorp memberships) (append memberships nil))
   ((listp memberships) memberships)
   (t nil)))

(defun asana-org-render--task-to-org (task)
  "Convert Asana TASK data to Org heading text and properties.
Returns (heading-text . properties-alist)."
  (let* ((gid (alist-get 'gid task))
         (permalink (alist-get 'permalink_url task))
         (completed-raw (alist-get 'completed task))
         (completed (and completed-raw (not (eq completed-raw :json-false))))
         (start-on (alist-get 'start_on task))
         (due-on (alist-get 'due_on task))
         (due-at (alist-get 'due_at task))
         (notes (alist-get 'notes task))
         (modified-at (alist-get 'modified_at task))
         (memberships (alist-get 'memberships task))
         ;; memberships is a vector or list from json-read
         (first-membership (asana-org-render--seq-first memberships))
         (project-obj (and first-membership (alist-get 'project first-membership)))
         (section-obj (and first-membership (alist-get 'section first-membership)))
         (project-gid (and project-obj (alist-get 'gid project-obj)))
         (section-gid (and section-obj (alist-get 'gid section-obj))))
    (cons
     (if completed "DONE" "TODO")
     (list (cons asana-org-prop-gid gid)
           (cons asana-org-prop-permalink permalink)
           (cons asana-org-prop-remote-modified-at modified-at)
           (cons asana-org-prop-project-gid project-gid)
           (cons asana-org-prop-section-gid section-gid)
           (cons "SCHEDULED" start-on)
           (cons "DEADLINE" (or due-at due-on))
           (cons "DESCRIPTION" notes)
           (cons asana-org-prop-local-hash (asana-org-render--compute-hash task))))))

(defun asana-org-render--normalize-json-booleans (obj)
  "Normalize JSON booleans/nulls in OBJ recursively.
Convert :json-false and :json-null to nil so that
`json-serialize' can process the result."
  (cond
   ((eq obj :json-false) nil)
   ((eq obj :json-null) nil)
   ((and (consp obj) (not (listp (cdr obj))))
    ;; dotted pair
    (cons (car obj) (asana-org-render--normalize-json-booleans (cdr obj))))
   ((listp obj)
    (mapcar #'asana-org-render--normalize-json-booleans obj))
   ((vectorp obj)
    (vconcat (mapcar #'asana-org-render--normalize-json-booleans (append obj nil))))
   (t obj)))

(defun asana-org-render--compute-hash (task)
  "Compute hash for TASK content to detect changes."
  (secure-hash 'sha256 (json-serialize (asana-org-render--normalize-json-booleans task))))

(defun asana-org-render--format-timestamp (timestamp)
  "Format TIMESTAMP for Org date entry."
  (when timestamp
    (if (string-match-p "T" timestamp)
        (format "<%s>" (substring timestamp 0 19))
      (format "<%s>" timestamp))))

(defun asana-org-render--format-scheduled (start-on)
  "Format START_ON for Org SCHEDULED."
  (when start-on
    (format "SCHEDULED: <%s>" start-on)))

(defun asana-org-render--format-deadline (due-on due-at)
  "Format DUE-ON/DUE-AT for Org DEADLINE."
  (when (or due-on due-at)
    (format "DEADLINE: %s"
            (asana-org-render--format-timestamp (or due-at due-on)))))

(defun asana-org-render--format-properties (properties)
  "Format PROPERTIES alist as Org property drawer."
  (when properties
    (concat ":PROPERTIES:\n"
            (mapconcat (lambda (prop)
                         (format ":%s: %s" (car prop) (cdr prop)))
                       properties "\n")
            "\n:END:\n")))

(defun asana-org-render--format-comments (comments)
  "Format COMMENTS list as Org drawer."
  (when comments
    (concat ":" asana-org-comments-drawer ":\n"
            (mapconcat (lambda (comment)
                         (let* ((author-obj (alist-get 'created_by comment))
                                (author (cond
                                         ((stringp author-obj) author-obj)
                                         ((listp author-obj) (or (alist-get 'name author-obj) "unknown"))
                                         (t "unknown")))
                                (text (alist-get 'text comment))
                                (created (alist-get 'created_at comment)))
                           (format "- [%s] %s: %s"
                                   (substring created 0 10)
                                   author
                                   text)))
                       comments "\n")
            ":END:\n")))

(defun asana-org-render-task-entry (task)
  "Render Asana TASK as Org entry string at level 1."
  (asana-org-render-task-entry-at-level task 1))

(defun asana-org-render-task-entry-at-level (task level)
  "Render Asana TASK as Org entry string at heading LEVEL."
  (let* ((parsed (asana-org-render--task-to-org task))
         (todo (car parsed))
         (props (cdr parsed))
         (name (alist-get 'name task))
         (start-on (alist-get 'start_on task))
         (due-on (alist-get 'due_on task))
         (due-at (alist-get 'due_at task))
         (notes (alist-get 'notes task))
         (stories (alist-get 'stories task))
         (stars (make-string level ?*)))
    (concat
     (format "%s %s %s\n" stars todo name)
     (asana-org-render--format-properties props)
     (when start-on (format "%s\n" (asana-org-render--format-scheduled start-on)))
     (when (or due-on due-at) (format "%s\n" (asana-org-render--format-deadline due-on due-at)))
     (when notes
       (concat "\n" notes "\n"))
     (when stories
       (asana-org-render--format-comments stories)))))

(defun asana-org-render-tasks (tasks)
  "Render TASKS list to Org files per project.
Creates or updates Org files in `asana-org-root-directory'."
  (let* ((by-project (seq-group-by
                      (lambda (task)
                        (let* ((memberships (alist-get 'memberships task))
                               (first-membership (asana-org-render--seq-first memberships))
                               (project-obj (and first-membership (alist-get 'project first-membership)))
                               (project-gid (and project-obj (alist-get 'gid project-obj))))
                          (or project-gid "my-tasks")))
                      tasks))
         (updated-files nil))
    (pcase-dolist (`(,project-gid . ,project-tasks) by-project)
      (let* ((file (asana-org-get-project-file project-gid))
             (_existing-tasks (when (file-exists-p file)
                                (asana-org-render--parse-existing-tasks file)))
             (rendered (mapconcat #'asana-org-render-task-entry project-tasks "\n"))
             (content (concat "#+TITLE: Asana Project " project-gid "\n"
                              "#+FILETAGS: :asana:\n\n"
                              rendered "\n")))
        ;; Use with-temp-file to safely write content to file
        (with-temp-file file
          (insert content))
        (push file updated-files)
        (asana-org-log-info "Wrote %d tasks to %s" (length project-tasks) file)))
    updated-files))

(defun asana-org-render--extract-section-gid (task project-gid)
  "Extract section GID from TASK for the matching PROJECT-GID membership.
Falls back to first membership if no match found."
  (let* ((memberships (asana-org-render--memberships-to-list
                       (alist-get 'memberships task)))
         (match (seq-find (lambda (mem)
                            (let* ((proj (alist-get 'project mem))
                                   (pgid (and proj (alist-get 'gid proj))))
                              (and pgid (string= pgid project-gid))))
                          memberships))
         (mem (or match (car memberships)))
         (section-obj (and mem (alist-get 'section mem))))
    (and section-obj (alist-get 'gid section-obj))))

(defun asana-org-render--collect-all-sections (sections-map)
  "Collect all sections from SECTIONS-MAP into a flat ordered list.
Deduplicates by section GID, preserving first occurrence order.
SECTIONS-MAP is an alist mapping project-gid to section vectors/lists."
  (let ((seen (make-hash-table :test 'equal))
        (result nil))
    (dolist (entry sections-map)
      (let* ((sections-raw (cdr entry))
             (sections (if (vectorp sections-raw)
                           (append sections-raw nil)
                         sections-raw)))
        (dolist (section sections)
          (let ((gid (alist-get 'gid section)))
            (unless (gethash gid seen)
              (puthash gid t seen)
              (push section result))))))
    (nreverse result)))

(defun asana-org-render-tasks-with-sections (tasks sections-map)
  "Render all TASKS into a single My Tasks file grouped by section.
SECTIONS-MAP is an alist mapping project-gid to an ordered vector/list
of section objects, each with `gid' and `name' fields.
Sections become level-1 headings, tasks become level-2 headings.
All tasks are merged into one file regardless of project.
Tasks with a `my_tasks_section_gid' field use that for grouping."
  (let* ((file (expand-file-name "my-tasks.org" asana-org-root-directory))
         ;; Collect all unique sections in order across all projects
         (all-sections (asana-org-render--collect-all-sections sections-map))
         ;; Build a map of section-gid -> tasks
         ;; Use my_tasks_section_gid if present (injected by bridge),
         ;; otherwise fall back to membership section lookup
         (by-section (seq-group-by
                      (lambda (task)
                        (or (alist-get 'my_tasks_section_gid task)
                            (let* ((memberships (asana-org-render--memberships-to-list
                                                 (alist-get 'memberships task))))
                              (or (cl-some (lambda (mem)
                                             (let ((section-obj (alist-get 'section mem)))
                                               (and section-obj
                                                    (alist-get 'gid section-obj))))
                                           memberships)
                                  "unsectioned"))))
                      tasks))
         (content-parts nil))
    ;; File header
    (push "#+TITLE: My Tasks\n" content-parts)
    (push "#+CATEGORY: asana\n" content-parts)
    (push "#+STARTUP: overview\n\n" content-parts)

    (if all-sections
        (progn
          ;; Render each section in order
          (dolist (section all-sections)
            (let* ((section-gid (alist-get 'gid section))
                   (section-name (alist-get 'name section))
                   (section-tasks (cdr (assoc section-gid by-section))))
              ;; Section heading (level 1)
              (push (format "* %s\n" section-name) content-parts)
              ;; Tasks in this section (level 2)
              (dolist (task section-tasks)
                (push (asana-org-render-task-entry-at-level task 2) content-parts))))
          ;; Handle tasks not in any known section
          (let ((unsectioned (cdr (assoc "unsectioned" by-section))))
            (when unsectioned
              (push "* Unsectioned\n" content-parts)
              (dolist (task unsectioned)
                (push (asana-org-render-task-entry-at-level task 2) content-parts)))))
      ;; Fallback: no sections data, render flat
      (dolist (task tasks)
        (push (asana-org-render-task-entry task) content-parts)))

    ;; Write file and keep any visiting buffer in sync
    (let ((content (apply #'concat (nreverse content-parts))))
      (asana-org-ensure-root-directory)
      (let ((buf (find-file-noselect file)))
        (with-current-buffer buf
          (erase-buffer)
          (insert content)
          (save-buffer)))
      (asana-org-log-info "Wrote %d tasks (%d sections) to %s"
                          (length tasks)
                          (length all-sections)
                          file))
    (list file)))

(defun asana-org-render--parse-existing-tasks (file)
  "Parse existing tasks from ORG-FILE.
Returns list of task GIDs found."
  (with-temp-buffer
    (insert-file-contents file)
    (goto-char (point-min))
    (let ((gids nil))
      (while (re-search-forward (concat "^:ASANA_GID: \\(" asana-org-prop-gid "\\|[0-9]+\\)") nil t)
        (push (match-string 1) gids))
      gids)))

;;;; Preview Rendering (JSON Contract v1)

(defun asana-org-render-preview (preview-response)
  "Render PREVIEW-RESPONSE to preview buffer.
PREVIEW-RESPONSE follows sync preview output schema from cli-contract.md.

Sections (in order):
1. Blocked conflicts (top - cannot apply)
2. Warnings
3. Proposed mutations grouped by type (task_move, comment_add)"
  (let* ((data (alist-get 'data preview-response))
         (pending-changes (or (alist-get 'pending_changes data) '()))
         (version (alist-get 'version preview-response))
         (command (alist-get 'command preview-response))
         (buffer (get-buffer-create asana-org-preview-buffer-name)))
    
    ;; Classify changes
    (let ((blocked-changes (seq-filter
                           (lambda (change)
                             (let ((conflict (alist-get 'conflict change)))
                               (and conflict
                                    (alist-get 'blocking conflict))))
                           pending-changes))
          (non-blocked-changes (seq-filter
                               (lambda (change)
                                 (let ((conflict (alist-get 'conflict change)))
                                   (or (not conflict)
                                       (not (alist-get 'blocking conflict)))))
                               pending-changes))
          ;; Extract warnings (could be in data.warnings or embedded in changes)
          (warnings (or (alist-get 'warnings data) '())))
      
      (with-current-buffer buffer
        (erase-buffer)
        (insert "#+TITLE: Asana Org - Change Preview\n")
        (insert "#+OPTIONS: toc:nil num:nil\n")
        (insert (format "#+VERSION: %s\n" version))
        (insert (format "#+COMMAND: %s\n" command))
        (insert "\n")
        
        ;; Summary header
        (insert "* Summary\n")
        (insert (format "Total changes: %d\n" (length pending-changes)))
        (insert (format "Blocked: %d\n" (length blocked-changes)))
        (insert (format "Ready to apply: %d\n\n" (length non-blocked-changes)))
        
        ;; Section 1: Blocked Conflicts (TOP - most important)
        (when blocked-changes
          (insert "* ⚠ BLOCKED CONFLICTS (Cannot Apply)\n")
          (insert "These changes have conflicts that must be resolved:\n\n")
          (dolist (change blocked-changes)
            (asana-org-render-preview-conflict change))
          (insert "\n"))
        
        ;; Section 2: Warnings
        (when warnings
          (insert "* ⚡ Warnings\n")
          (dolist (warning warnings)
            (insert (format "- %s\n" warning)))
          (insert "\n"))
        
        ;; Section 3: Proposed Mutations (grouped by type)
        ;; Note: type from JSON is a string, not a symbol
        (when non-blocked-changes
          (let* ((task-moves (seq-filter
                              (lambda (c)
                                (string= (alist-get 'type c) "task_move"))
                              non-blocked-changes))
                 (comment-adds (seq-filter
                                (lambda (c)
                                  (string= (alist-get 'type c) "comment_add"))
                                non-blocked-changes))
                 (other-changes (seq-filter
                                 (lambda (c)
                                   (let ((type (alist-get 'type c)))
                                     (not (or (string= type "task_move")
                                              (string= type "comment_add")))))
                                 non-blocked-changes)))
            
            ;; Task Moves
            (when task-moves
              (insert "* → Task Moves\n")
              (insert (format "%d task(s) to move:\n\n" (length task-moves)))
              (dolist (change task-moves)
                (asana-org-render-preview-mutation change))
              (insert "\n"))
            
            ;; Comment Additions
            (when comment-adds
              (insert "* 💬 Comments to Add\n")
              (insert (format "%d comment(s) to add:\n\n" (length comment-adds)))
              (dolist (change comment-adds)
                (asana-org-render-preview-mutation change))
              (insert "\n"))
            
            ;; Other changes
            (when other-changes
              (insert "* ⚡ Other Changes\n")
              (insert (format "%d other change(s):\n\n" (length other-changes)))
              (dolist (change other-changes)
                (asana-org-render-preview-mutation change))
              (insert "\n"))))
        
        ;; Instructions footer
        (insert "* Instructions\n")
        (insert "To apply non-blocked changes:\n")
        (insert "  M-x asana-org-sync-apply\n\n")
        (insert (format "Confirmation required for >%d changes.\n" asana-org-confirm-threshold))
        (insert "Blocked changes require running `asana-org-sync-pull' to resolve conflicts.\n")
        
        (goto-char (point-min))
        (org-mode)
        (view-mode +1))
      (pop-to-buffer buffer))))

(defun asana-org-render-preview-conflict (change)
  "Render a single BLOCKED CHANGE entry.
CHANGE has conflict.detected=true and conflict.blocking=true."
  (let* ((id (alist-get 'id change))
         (type (alist-get 'type change))
         (description (alist-get 'description change))
         (conflict (alist-get 'conflict change))
         (conflict-reason (alist-get 'reason conflict))
         (current-state (alist-get 'current_state change))
         (proposed-state (alist-get 'proposed_state change)))
    
    (insert (format "** ⚠ %s (ID: %s)\n" (asana-org-render-preview--type-label type) id))
    (insert (format "   Description: %s\n" description))
    (insert (format "   Reason: %s\n\n" conflict-reason))
    
    ;; Show state comparison if available
    (when current-state
      (insert "   Current state:\n")
      (asana-org-render-preview--format-state current-state "     "))
    
    (when proposed-state
      (insert "   Proposed state:\n")
      (asana-org-render-preview--format-state proposed-state "     "))
    
    (insert "\n")))

(defun asana-org-render-preview-mutation (change)
  "Render a single PROPOSED CHANGE entry (non-blocked mutation)."
  (let* ((id (alist-get 'id change))
         (type (alist-get 'type change))
         (description (alist-get 'description change))
         (proposed-state (alist-get 'proposed_state change)))
    
    (insert (format "** %s (ID: %s)\n"
                    (asana-org-render-preview--type-label type)
                    id))
    (insert (format "   %s\n\n" description))
    
    ;; Show proposed state details
    (when proposed-state
      (asana-org-render-preview--format-state proposed-state "   "))
    
    (insert "\n")))

(defun asana-org-render-preview--format-state (state indent)
  "Format STATE alist with INDENT prefix."
  (dolist (pair state)
    (let ((key (car pair))
          (value (cdr pair)))
      (when value
        (insert (format "%s%s: %s\n" indent key value))))))

(defun asana-org-render-preview--type-label (type)
  "Get human-readable label for mutation TYPE.
TYPE is a string from JSON (e.g., \"task_move\", \"comment_add\")."
  (pcase type
    ("task_move" "Move Task")
    ("comment_add" "Add Comment")
    ("status_change" "Change Status")
    ("date_change" "Change Dates")
    (_ (if (symbolp type) (symbol-name type) type))))

;;;; Apply Result Rendering

(defun asana-org-render-apply-result (apply-response)
  "Render APPLY-RESPONSE results to buffer.
Response shape per cli-contract.md:
- status: success | partial | error
- data.results: array of {idempotency_key, status, details}"
  (let* ((status (alist-get 'status apply-response))
         (data (alist-get 'data apply-response))
         ;; Results are nested in data.results per contract
         (results (or (alist-get 'results data) 
                      (alist-get 'results apply-response)))
         (buffer (get-buffer-create "*Asana Org Apply Results*")))
    
    (with-current-buffer buffer
      (erase-buffer)
      (insert "#+TITLE: Asana Org - Apply Results\n")
      (insert "#+OPTIONS: toc:nil num:nil\n\n")
      
      (let ((applied (seq-filter (lambda (r)
                                   (string= (alist-get 'status r) "applied"))
                                 results))
            (conflicts (seq-filter (lambda (r)
                                     (string= (alist-get 'status r) "conflict"))
                                   results))
            (errors (seq-filter (lambda (r)
                                  (string= (alist-get 'status r) "error"))
                                results)))
        
        ;; Status header
        (insert "* Summary\n")
        (insert (format "Status: %s\n" (pcase status
                                         ("partial" "⚠ Partial Success")
                                         ("error" "✗ Error")
                                         (_ "✓ Success"))))
        (insert (format "Applied: %d\n" (length applied)))
        (insert (format "Conflicts: %d\n" (length conflicts)))
        (insert (format "Errors: %d\n\n" (length errors)))
        
        (when applied
          (insert "* ✓ Applied\n")
          (dolist (r applied)
            (let ((details (alist-get 'details r)))
              (insert (format "- %s: %s\n"
                              (alist-get 'idempotency_key r)
                              (or (alist-get 'action details) "completed")))))
          (insert "\n"))
        
        (when conflicts
          (insert "* ⚠ Conflicts\n")
          (dolist (r conflicts)
            (let ((details (alist-get 'details r)))
              (insert (format "- %s: %s\n"
                              (alist-get 'idempotency_key r)
                              (or (alist-get 'reason details) "conflict detected")))))
          (insert "\n"))
        
        (when errors
          (insert "* ✗ Errors\n")
          (dolist (r errors)
            (let ((details (alist-get 'details r)))
              (insert (format "- %s: %s\n"
                              (alist-get 'idempotency_key r)
                              (or (alist-get 'message details) "error")))))
          (insert "\n")))
      
      (goto-char (point-min))
      (org-mode)
      (view-mode +1))
    (pop-to-buffer buffer)))

;;;; AI Summary Rendering

(defvar asana-org-ai-summary-buffer-name "*Asana Org AI Summary*"
  "Buffer name for AI summary output.")

(defun asana-org-render-ai-summary (summary-data)
  "Render AI SUMMARY-DATA to dedicated buffer.
SUMMARY-DATA is the `data' alist from the bridge response with keys:
  summary   -- the AI-generated text
  task_count -- number of tasks analyzed
  model     -- model name used"
  (let* ((summary (alist-get 'summary summary-data))
         (task-count (alist-get 'task_count summary-data))
         (model (alist-get 'model summary-data))
         (buffer (get-buffer-create asana-org-ai-summary-buffer-name)))
    (with-current-buffer buffer
      (let ((inhibit-read-only t))
        (erase-buffer)
        (insert "#+TITLE: Asana Org - AI Summary\n")
        (insert "#+OPTIONS: toc:nil num:nil\n")
        (insert "\n")

        (when summary
          (insert "* Summary\n")
          (insert summary "\n\n"))

        (insert "* Metadata\n")
        (insert (format "- Tasks analyzed: %s\n" (or task-count "?")))
        (insert (format "- Model: %s\n" (or model "unknown")))
        (insert "\n")

        (insert "* Note\n")
        (insert "AI output is advisory.  All changes require manual approval via preview/apply.\n")

        (goto-char (point-min))
        (org-mode)
        (view-mode +1)))
    (pop-to-buffer buffer)))

;;;; Utility Functions

(defun asana-org-render--find-task-heading (task-gid)
  "Find heading with ASANA_GID property matching TASK-GID in current buffer.
Return point at beginning of heading, or nil if not found."
  (save-excursion
    (goto-char (point-min))
    (let ((found nil))
      (while (and (not found)
                  (re-search-forward
                   (concat "^:" asana-org-prop-gid ": *"
                           (regexp-quote task-gid) " *$")
                   nil t))
        ;; Move to the heading that owns this property
        (org-back-to-heading t)
        (setq found (point)))
      found)))

(defun asana-org-render--find-section-heading (section-gid)
  "Find heading with ASANA_SECTION_GID property matching SECTION-GID.
Searches level-1 headings in the current buffer.
Return point at beginning of heading, or nil if not found."
  (save-excursion
    (goto-char (point-min))
    (let ((found nil))
      (while (and (not found)
                  (re-search-forward "^\\* " nil t))
        (let ((gid (org-entry-get (point) asana-org-prop-section-gid)))
          (when (and gid (string= gid section-gid))
            (org-back-to-heading t)
            (setq found (point)))))
      found)))

(defun asana-org-render--section-end (section-pos)
  "Return the end position of the section subtree at SECTION-POS.
This is the point just before the next level-1 heading, or `point-max'."
  (save-excursion
    (goto-char section-pos)
    (org-end-of-subtree t t)
    (point)))

(defun asana-org-render-refile-task (task-gid target-section-gid &optional target-file)
  "Refile task TASK-GID to the section identified by TARGET-SECTION-GID.
When TARGET-FILE is non-nil, refile into that file instead of the
current buffer (for cross-file moves).

The function:
 1. Locates the task heading by its ASANA_GID property.
 2. Locates the target section heading by its ASANA_SECTION_GID property.
 3. Cuts the task subtree and pastes it at the end of the target section.
 4. Updates the task ASANA_SECTION_GID property to TARGET-SECTION-GID.

Returns non-nil on success, nil if the task or section was not found.
If the task is already in the target section this is a no-op."
  (let* ((source-file (or target-file (asana-org-get-task-file task-gid)))
         (buf (when source-file (find-file-noselect source-file))))
    (if (not buf)
        (progn
          (asana-org-log-warn "Refile: cannot find file for task %s" task-gid)
          nil)
      (with-current-buffer buf
        (org-mode)
        (save-excursion
          (save-restriction
            (widen)
            (asana-org-render--refile-in-buffer
             task-gid target-section-gid)))))))

(defun asana-org-render--refile-in-buffer (task-gid target-section-gid)
  "Perform the actual refile of TASK-GID to TARGET-SECTION-GID.
Assumes the current buffer is widened and in `org-mode'.
Returns non-nil on success, nil on failure."
  (let ((task-pos (asana-org-render--find-task-heading task-gid)))
    (cond
     ((not task-pos)
      (asana-org-log-warn "Refile: task %s not found in buffer" task-gid)
      nil)
     ;; Already in target section -- no-op
     ((let ((cur (save-excursion
                   (goto-char task-pos)
                   (org-entry-get (point) asana-org-prop-section-gid))))
        (and cur (string= cur target-section-gid)))
      (asana-org-log-info
       "Refile: task %s already in section %s, skipping"
       task-gid target-section-gid)
      t)
     (t
      (let ((section-pos (asana-org-render--find-section-heading
                          target-section-gid)))
        (if (not section-pos)
            (progn
              (asana-org-log-warn
               "Refile: section %s not found in buffer"
               target-section-gid)
              nil)
          ;; Cut task, paste at end of target section
          (goto-char task-pos)
          (org-cut-subtree)
          ;; Recalculate section position after cut
          (let ((new-pos (asana-org-render--find-section-heading
                          target-section-gid)))
            (goto-char (asana-org-render--section-end new-pos))
            (unless (bolp) (insert "\n"))
            (org-paste-subtree 2)
            (org-back-to-heading t)
            (org-entry-put (point) asana-org-prop-section-gid
                           target-section-gid)
            (asana-org-log-info "Refiled task %s to section %s"
                                task-gid target-section-gid)
            t)))))))


(provide 'asana-org-render)

;;;; asana-org-render.el ends here
