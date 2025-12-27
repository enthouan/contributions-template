# Security

Security fixes are maintained on the latest `main` branch. Update your local
tooling before generating a new history; generated output repositories do not
automatically receive template fixes.

Report suspected vulnerabilities privately through
[Antoine's contact form](https://www.antoinemenard.com/contact/). Mention this
repository and include the affected commit, expected impact, and a minimal
reproduction using fictional data. Do not include access tokens or personal
work/travel histories. Please avoid public disclosure of exploit details until
the issue has been assessed.

Keep generated repositories private when they contain personal timelines.
Calendar tokens belong in environment variables or GitHub Actions secrets,
never configuration files or commits. If a credential is exposed, revoke or
rotate it at its provider; deleting a file does not remove it from Git history.
