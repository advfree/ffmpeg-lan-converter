# Security

This application is designed for trusted local networks. Do not expose it
directly to the public Internet. If remote access is required, place it behind
an HTTPS reverse proxy and additional access controls.

The container can read and, when explicitly enabled, delete files under the
mounted media directory. Mount only directories that the application needs.

Please report security issues privately through GitHub's security advisory
feature instead of opening a public issue.
