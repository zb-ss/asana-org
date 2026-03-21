;;; asana-org-transient.el --- Transient menus for Asana Org  -*- lexical-binding: t; -*-

;; Copyright (C) 2025  Asana Org contributors
;; Author: Asana Org Team
;; URL: https://github.com/zb-ss/asana-org
;; Version: 0.1.0
;; Package-Requires: ((emacs "28.1") (transient "0.4.0"))
;; Keywords: org, asana, sync
;; SPDX-License-Identifier: GPL-3.0-or-later

;;; Commentary:
;; Transient menus providing command-line-like interface for Asana Org commands.
;; Exposes pull, preview, apply, move, and comment commands.

;;; Code:

(require 'transient)

;; Forward declarations to avoid circular require
;; Functions from asana-org.el
(declare-function asana-org-sync-pull "asana-org")
(declare-function asana-org-sync-preview "asana-org")
(declare-function asana-org-sync-apply "asana-org")
(declare-function asana-org-move-task "asana-org")
(declare-function asana-org-comment-append "asana-org")
(declare-function asana-org-ai-summary "asana-org")
(declare-function asana-org-get-property "asana-org")
(declare-function asana-org-sync-status "asana-org-sync")
(declare-function asana-org-sync--section-name-for-gid "asana-org-sync")
(declare-function asana-org-sync-reconcile "asana-org-sync")
(declare-function asana-org-sync-validate "asana-org-sync")
;; Functions from asana-org-sync.el
(declare-function asana-org-sync-detect-changes "asana-org-sync")

;; Variables/constants from asana-org.el
(defvar asana-org-prop-gid)
(defvar asana-org-buffer-name)
(defvar asana-org-bridge-binary)
(defvar asana-org-root-directory)
(defvar asana-org-dry-run)
(defvar asana-org-cache-directory)

;;;; Main Transient Menu

(transient-define-suffix asana-org-transient-pull (project-gid)
  "Pull Asana tasks into Org files."
  :key "p"
  (interactive
   (list (let ((proj (transient-arg-value "--project=" (transient-args transient-current-command))))
           (if proj proj (read-string "Project GID (leave empty for My Tasks): ")))))
  (asana-org-sync-pull (unless (string= project-gid "") project-gid)))

(transient-define-suffix asana-org-transient-preview ()
  "Preview outbound changes."
  :key "v"
  (interactive)
  (asana-org-sync-preview))

(transient-define-suffix asana-org-transient-detect ()
  "Detect changes between org files and Asana cache."
  :key "d"
  (interactive)
  (asana-org-sync-detect-changes))

(transient-define-suffix asana-org-transient-apply ()
  "Apply approved changes to Asana."
  :key "a"
  (interactive)
  (asana-org-sync-apply))

(transient-define-suffix asana-org-transient-move (task-gid project-gid section-gid)
  "Move task to different project/section."
  :key "m"
  (interactive
   (let* ((task (or (asana-org-get-property asana-org-prop-gid)
                    (read-string "Task GID: ")))
          (project (read-string "Target project GID: "))
          (section (read-string "Target section GID (optional): ")))
     (list task project (unless (string= section "") section))))
  (let ((response (asana-org-move-task task-gid project-gid section-gid)))
    ;; Show a user-friendly message including section name when available
    (let* ((refile-gid (or section-gid project-gid))
           (section-name (when refile-gid
                           (require 'asana-org-sync)
                           (condition-case nil
                               (asana-org-sync--section-name-for-gid refile-gid)
                             (error nil)))))
      (if section-name
          (message "Task moved and refiled to section '%s'" section-name)
        (message "Task moved to project %s" project-gid)))
    response))

(transient-define-suffix asana-org-transient-comment (task-gid comment)
  "Append comment to task."
  :key "c"
  (interactive
   (let* ((task (or (asana-org-get-property asana-org-prop-gid)
                    (read-string "Task GID: "))))
     (list task (read-string "Comment: "))))
  (asana-org-comment-append task-gid comment))

(transient-define-suffix asana-org-transient-ai-summary ()
  "Generate AI summary for task at point."
  :key "i"
  (interactive)
  (let ((task-gid (asana-org-get-property asana-org-prop-gid)))
    (unless task-gid
      (user-error "No ASANA_GID property found at point"))
    (asana-org-ai-summary (list task-gid))))

(transient-define-suffix asana-org-transient-status ()
  "Show sync health status."
  :key "s"
  (interactive)
  (asana-org-sync-status))

(transient-define-suffix asana-org-transient-reconcile ()
  "Reconcile local snapshots against remote."
  :key "r"
  (interactive)
  (asana-org-sync-reconcile))

(transient-define-suffix asana-org-transient-validate ()
  "Validate org states against cached snapshots."
  :key "V"
  (interactive)
  (asana-org-sync-validate))

(transient-define-suffix asana-org-transient-open-log ()
  "Open Asana Org log buffer."
  :key "l"
  (interactive)
  (switch-to-buffer asana-org-buffer-name))

(transient-define-suffix asana-org-transient-open-cache-dir ()
  "Open cache directory in dired."
  :key "D"
  (interactive)
  (dired asana-org-cache-directory))

(transient-define-suffix asana-org-transient-configure ()
  "Open customization group."
  :key "C"
  (interactive)
  (customize-group 'asana-org))

;;;###autoload (autoload 'asana-org-transient "asana-org-transient" nil t)
(transient-define-prefix asana-org-transient ()
  "Asana Org integration commands."
  [:description
   (lambda ()
     (concat "Asana Org Sync\n"
             (format "Bridge: %s\n" asana-org-bridge-binary)
             (format "Root: %s" asana-org-root-directory)))
   ["Sync"
    ("p" "Pull tasks" asana-org-transient-pull)
    ("d" "Detect changes" asana-org-transient-detect)
    ("v" "Preview changes" asana-org-transient-preview)
    ("a" "Apply changes" asana-org-transient-apply)]
   ["Actions"
     ("m" "Move task" asana-org-transient-move)
     ("c" "Add comment" asana-org-transient-comment)
     ("i" "AI Summary" asana-org-transient-ai-summary)]
   ["Utility"
    ("s" "Status" asana-org-transient-status)
    ("r" "Reconcile" asana-org-transient-reconcile)
    ("V" "Validate" asana-org-transient-validate)
    ("l" "Open log" asana-org-transient-open-log)
    ("D" "Open cache dir" asana-org-transient-open-cache-dir)
    ("C" "Configure" asana-org-transient-configure)]
   [("q" "Quit" transient-quit-one)]]
  (interactive)
  (transient-setup 'asana-org-transient))

;;;; Sync-Specific Transient (Pull/Preview/Apply flow)

(transient-define-prefix asana-org-sync-transient ()
  "Sync workflow: pull, preview, apply."
  [:description
   (lambda ()
     (concat "Asana Org Sync Workflow\n"
             (format "Dry-run: %s" (if asana-org-dry-run "ON" "OFF"))))
   ["Workflow"
    ("p" "Pull from Asana" asana-org-transient-pull)
    ("d" "Detect changes" asana-org-transient-detect)
    ("v" "Preview changes" asana-org-transient-preview)
    ("a" "Apply changes" asana-org-transient-apply)]
   [("q" "Quit" transient-quit-one)]]
  (interactive)
  (transient-setup 'asana-org-sync-transient))

;;;; Bind Global Keys

(defun asana-org-transient-setup-keybindings ()
  "Setup global keybindings for Asana Org transients."
  (define-key global-map (kbd "C-c a") 'asana-org-transient)
  (define-key global-map (kbd "C-c s a") 'asana-org-sync-transient))

(provide 'asana-org-transient)

;;; asana-org-transient.el ends here
