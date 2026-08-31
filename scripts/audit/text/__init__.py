"""Parsing helpers shared between criteria.

Pure functions over text: markdown, workflow YAML, Python source. They
stay functions rather than becoming methods -- they are the most
testable code in the audit and turning them into methods would only
make them harder to reach.
"""
