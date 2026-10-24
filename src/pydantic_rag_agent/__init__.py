"""Pydantic RAG agent.

Kept deliberately empty of logic: import-time side effects (config loading,
client construction, credential reads) make a package painful to test, so the
real code lives in explicit modules such as :mod:`pydantic_rag_agent.cli`.
"""
