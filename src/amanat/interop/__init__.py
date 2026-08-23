"""Interop with external agent-payment standards.

Today: AP2 (Google's Agent Payments Protocol). The point is not to reimplement
AP2 but to *anchor* to it — ingest a real AP2 authorization and adjudicate this
project's downward settlement chain against it, so the demo answers "how does
this relate to AP2?" with running code rather than a slide.
"""
