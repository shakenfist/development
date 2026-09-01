"""The markers delimiting generated content.

audit-update-docs.py writes them onto docs/audits/compliance.md and
the documentation criteria read them, so that a check judging prose
does not judge a table the audit itself produced. Defined here rather
than beside either one: a writer and a reader that disagree about the
marker is the failure that silently exempted half of two plan files.
"""

BEGIN_MARKER = '<!-- consistency-audit:begin -->'
END_MARKER = '<!-- consistency-audit:end -->'
