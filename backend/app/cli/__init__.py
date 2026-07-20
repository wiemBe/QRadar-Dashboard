"""Administrative command-line entrypoints.

These exist so an operator can register a console and drive a collection run
without waiting for Celery Beat, and so a deployment runbook can be a list of
commands rather than a list of SQL statements.

Nothing here accepts a secret as a command-line argument: a token passed as an
argv element is visible in `ps` output and in shell history. Tokens are read
from a file path instead.
"""
