"""Reviewed Co-Founder skill definitions and shadow-mode policy machinery.

This package is intentionally not imported by the live ADK agent graph.  It
compiles and evaluates narrowing-only skill contracts; it does not register a
tool, execute a capability, or authorize a workflow transition.
"""

from skills.compiler import AtomicSkillRegistry, compile_catalog
from skills.models import CompiledCatalog, SkillManifest

__all__ = ["AtomicSkillRegistry", "CompiledCatalog", "SkillManifest", "compile_catalog"]
