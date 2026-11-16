# Security Policy

## Reporting a vulnerability

Please report vulnerabilities through GitHub's private vulnerability reporting
(Security > Report a vulnerability on this repository). Do not open a public
issue for security problems.

## Scope note

This tool speaks ECHONET Lite, a protocol with **no authentication and no
encryption**. Any host that can reach UDP port 3610 on an adapter can control
the attached air conditioner. Run this tool - and any ECHONET Lite controller -
only on trusted home networks or dedicated VLANs. This is a property of the
protocol, not of this implementation, and is documented here so users do not
mistake the tool for the source of the exposure.
