# Quotex API

This package vendors the Quotex API implementation from
[Crazy4electrons/API-Quotex](https://github.com/Crazy4electrons/API-Quotex),
including the fork's `main` changes through commit `cdc0669cbcd1`.

The vendored source also retains the bot's browser-authorization refresh
integration, verified TLS configuration, and the fork's Engine.IO heartbeat
behavior. Keep those project-specific compatibility and security changes when
updating from the fork.

See [LICENSE](./LICENSE) for the upstream license.
