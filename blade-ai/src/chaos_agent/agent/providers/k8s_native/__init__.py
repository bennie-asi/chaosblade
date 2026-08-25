"""Kubernetes-native carrier — kubectl-only fault execution.

One registered provider (``provider.K8sNativeProvider``) plus the kubectl
classifier vocabulary (``classifier``) it owns. External consumers import the
concrete modules directly — this package ``__init__`` intentionally re-exports
nothing.
"""
