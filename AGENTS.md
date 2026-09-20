# Project workflow

- The production bot runs on the operator's configured SSH host under `hd2bot.service`.
  Resolve the host from private local deployment settings; use `sudo -n -i` for administration.
  Do not start the workstation bot while the production instance is running.
- Preserve unrelated work and real local/server configuration. Never commit `.env`,
  runtime `data/`, databases, credentials, cookies, SSH keys, tokens, or local
  deployment reports. Use blank or unmistakably placeholder values in examples.
- Operator download URLs (including file-sharing links), personal QR codes,
  native-menu overrides, announcements and manager lists are private runtime
  data as well. Keep their distributable templates empty. The public GitHub
  source URL is explicitly approved for the bot's open-source notice.
- Check every publishable file, including research notes, for workstation paths,
  personal identifiers and deployment-specific details; use generic descriptions.
  Keep research conclusions and public citations, but store original local records
  in ignored directories. Empty examples are listed in `DISTRIBUTION_CN.md`.
- After each authorized change: run the relevant tests and Ruff; scan the staged
  files and Git history for sensitive material; then automatically commit and
  push to `origin` on `main`, as explicitly requested by the project owner.
- Expected origin: `https://github.com/amiamianightcord25-art/helldivers2_naiwa_bot.git`.
  Never force-push or discard user changes to resolve a remote divergence.
- Enable the repository hooks with `git config core.hooksPath .githooks`.
  `scripts/check_publish.py` blocks runtime files and scans staged changes with
  Gitleaks; pre-push also scans history. Install Gitleaks before publishing.
- After deployed bot changes, restart the server service and verify QQ READY.
  Retain current state, check-in history and subscriptions. Do not create game
  subscriptions or send messages unless the user has authorized those messages.
- Use GitHub CLI browser authorization when login is needed. Ask the user to
  complete the browser step; never ask them to paste a token or password.
