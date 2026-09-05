# Security Policy

## Reporting a vulnerability

Please avoid publishing secrets, server IPs, admin tokens, or exploit details in a public issue. Use GitHub's private vulnerability reporting feature when available.

## Security model

- The management process binds to loopback by default.
- API access requires a Bearer token.
- The service does not run as root.
- The service account can only validate and reload Nginx via a restricted sudoers rule.
- Generated Nginx configuration is validated before reload and rolled back on failure.
