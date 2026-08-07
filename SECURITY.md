# Security policy

How to report a vulnerability in D-Knowledge Graph, what will happen to your
report, and which versions the answer applies to.

For the design this policy protects, read `docs/SECURITY_MODEL.md` (the controls
and their defaults) and `docs/THREAT_MODEL.md` (the adversaries and what is out
of scope). This file is only about disclosure.

## Supported versions

| Version | Supported | Notes |
|---|---|---|
| 0.1.0 (current) | Yes | The only line receiving fixes. `dkg --version` prints it. |
| 0.1.0-r1 | Yes | A correction pass over 0.1.0 with no feature change; same line. |
| Anything older | No | Nothing older was released. |

There is one supported line, and it is the current one. Fixes land on the
current version; there is no backport branch, because there is nothing to
backport to. If you are running a build older than the current release, the
first step is to update.

Versions distributed before 2026-08-05 were released under Apache-2.0. That
grant is irrevocable for those versions, but an irrevocable licence is not a
support commitment: security fixes are made to the current version only.

## Reporting a vulnerability

**Do not open a public issue for a security problem.** A public issue is a
disclosure, and it is a disclosure made before anyone can fix it.

Report privately, through the repository on the forge where you obtained this
copy, in this order:

1. **Private vulnerability reporting on the forge.** On the repository page,
   open the **Security** tab and choose **Report a vulnerability**. That form
   opens a private advisory that only the maintainers can see, and it is the
   preferred channel because it keeps the report, the discussion, and the fix in
   one private place.
2. **A private message to the repository owner on the forge**, if private
   vulnerability reporting is not enabled on the copy you have.
3. **A public issue containing no detail at all**, only as a last resort. Say
   that you have a security report and would like a private channel. Do not
   include the vulnerability, the reproduction, or the affected file.

No email address is published here on purpose. Publishing an address that has
not been verified would look like a working channel while silently dropping
reports, which is worse than publishing none.

If you have a public key you would like used for follow-up, say so in the
report and one will be exchanged through the private channel.

## What a report should contain

The more of this you can supply, the faster a report becomes a fix. Nothing here
is mandatory; a partial report is much better than none.

- **What the issue is**, in one or two sentences.
- **Where it is.** The file and, if you can, the function or line. Repository
  paths such as `src/dkg/security/ssrf.py` are ideal.
- **The version.** The output of `dkg --version`.
- **The environment.** The output of `python scripts/probe_environment.py`
  (add `--offline` if the machine has no network). It records the interpreter,
  the installed extras, the external binaries and staged models found, and
  whether the package index is reachable, which is usually enough to reproduce.
- **A reproduction.** The smallest sequence of commands or the smallest input
  that shows the problem. A proof of concept is welcome; it is not required.
- **The impact.** What an attacker gets: read access to what, write access to
  what, code execution where, or a bypass of which control.
- **The preconditions.** What the attacker needs first. Local shell access,
  a poisoned document, an outbound network allowance, a served HTTP surface, or
  a configured token.
- **Anything you already tried** that did not work, which saves repeating it.

Two things worth calling out, because they shape severity here:

- The product is **local-first and air-gapped by default**. A finding that
  requires the user to have explicitly enabled egress is still a finding, but
  say that it does.
- The **MCP surface is the trust boundary** against an assistant acting on
  content it was fed. A path that lets caller-supplied input escape a confined
  root or exceed a cap is squarely in scope even when nothing is written.

## In scope

- Anything that lets injected or fetched content act as an instruction rather
  than as evidence.
- Any read reachable from the MCP surface that escapes its root, exceeds its
  cap, or reports `truncated` incorrectly.
- Any path that reaches the network without an explicit opt-in, or that
  downloads a model at runtime.
- Any bypass of the address checks that reject private, loopback, link-local,
  multicast, reserved, or cloud-metadata addresses.
- Any way to write through a surface documented as read-only.
- Credential or secret leakage through logs, audit lines, exports, or evidence
  packets.
- SQL constructed by string interpolation of a parameter.
- Archive, XML, or decompression handling that escapes its caps or its
  extraction root.
- Tampering with the append-only audit chain that `dkg audit --verify` does not
  detect.
- Supply-chain problems: a dependency that is not permissively licensed, an
  unpinned action, or a lockfile or software bill of materials that does not
  describe the real closure.

## Out of scope

These are documented limits rather than dismissals. Several are recorded in
`docs/THREAT_MODEL.md`.

- An attacker who already has your local user account. The database is a file
  owned by you; the threat model does not defend against yourself.
- Denial of service by feeding the tool an enormous input on your own machine.
- Findings that require modifying the source, which the licence does not permit
  in the first place.
- The advisory analyses being wrong. Structural blast-radius, execution flow,
  the taint pass, and the contradiction scanner are all over-approximate by
  design and labelled so. A false positive is not a vulnerability. A missing
  caveat where one is required is a documentation bug, and worth reporting as
  one.
- Vulnerabilities in a third-party dependency, unless this project's use of it
  is what creates the exposure. Report those upstream; tell us too, so the pin
  can move.
- Anything about a hosted service. There is none.

## What happens after you report

These are targets, and they are stated as targets because this is a small
project without a staffed on-call rotation. A schedule nobody can keep is not a
commitment.

| Stage | Target |
|---|---|
| Acknowledgement that the report was received | 3 working days |
| First assessment: reproduced or not, and a severity | 10 working days |
| Fix for a high-severity issue | 30 days from the assessment |
| Fix for anything lower | The next release |
| Public advisory | With the fix, or sooner by agreement with you |

If a target slips, you will be told that it slipped and why, rather than left
with silence. Silence is the failure mode this section exists to prevent.

The sequence:

1. **Acknowledge.** You get a reply confirming receipt.
2. **Assess.** The report is reproduced and given a severity. If it cannot be
   reproduced, you are told exactly what was tried, so you can correct it.
3. **Fix.** A fix lands with a test that fails without it. That is the project's
   standard for everything, and a security fix is not an exception: a fix
   without a failing-first test is not evidence that anything changed.
4. **Release.** The fix ships on the current version.
5. **Disclose.** An advisory is published with the fix, crediting you by
   whatever name you ask for, or not at all if you prefer.

## Coordinated disclosure

Please give the project a chance to ship a fix before publishing. Ninety days
from acknowledgement is a reasonable default, and a shorter window is
reasonable when a problem is already being exploited or is already public. If
you intend to publish on a fixed date, say so in your first message and it will
be worked to rather than argued about.

There is no bug bounty. Nothing is paid for a report, and this is said plainly
here rather than left for you to discover after the work.

## What this project already does

Not a defence against reporting, just context so you do not spend time on
something already covered. Each control is implemented in the tracked source and
covered by a test.

- Outbound network off by default; egress needs an explicit flag and a
  configuration allowance.
- No telemetry, and none to disable.
- A read-only MCP surface, with the HTTP variant bound to loopback, requiring a
  bearer token, validating `Origin` and `Host`, and capping request size and
  rate.
- Post-resolution address checks against private, loopback, link-local,
  multicast, reserved, and cloud-metadata addresses.
- A credential redactor over audit lines, logs, and exported packets.
- Fetched web content labelled untrusted evidence, never instructions, and
  scored for prompt-injection attempts.
- Parameterised SQL everywhere; the database layer rejects interpolated
  parameters.
- An append-only, per-row hash-chained audit log, verifiable with
  `dkg audit --verify`.
- SHA-pinned GitHub Actions, a generated lockfile and software bill of
  materials, a licence audit, a secret scan, and a static analysis pass in
  continuous integration.

## See also

- `docs/SECURITY_MODEL.md` for the controls and their defaults.
- `docs/THREAT_MODEL.md` for the adversaries considered and the limits accepted.
- `docs/DEPENDENCY_AND_LICENCE_POLICY.md` for the supply-chain rules.
- `CODE_OF_CONDUCT.md` for a conduct concern, which is a different process.
