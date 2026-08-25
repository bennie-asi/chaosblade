"""kubectl-native carrier declaration — vocabulary surface.

Phase-12 (spec-import-retirement): lightweight knowledge surface for the
spec layer. The provider class reads these tuples as its
``supported_targets`` / ``supported_actions`` class attributes, so the
runtime provider surface and the ``fault_registry`` aggregation can never
drift apart.

Dependency discipline (pinned by the declaration guard in
``tests/test_agent/test_phase9_rename_guards.py``): stdlib, typing,
``chaos_agent.transports`` and ``chaos_agent.config.settings`` ONLY — no
imports of the providers assembly layer, ``agent.spec``, or sibling
provider implementation modules.
"""
from __future__ import annotations

#: Carrier id of the kubectl-native backend (config-mutation faults).
CARRIER_ID = "k8s_native"

#: kubectl resource/subsystem types this carrier can mutate.
SUPPORTED_TARGETS = (
    "pod",
    "finalizer",
    "replicas",
    "schedule",
    "pvc",
    "dns",
    "image",
    "probe",
    "volume",
    "cni",
    "endpoint",
    # resources: workload spec 资源配额类故障（limits 单位/数值篡改，
    # kubectl patch 注入）；file: 容器内文件类故障（kubectl exec 命令
    # 模式注入，见 provider 的 inject_command_subcommands）
    "resources",
    "file",
)

#: Mutation verbs this carrier applies.
SUPPORTED_ACTIONS = (
    "patch",
    "cordon",
    "taint",
    "delete",
    "drain",
    "scale",
    "corrupt",
    "duplicate",
)
