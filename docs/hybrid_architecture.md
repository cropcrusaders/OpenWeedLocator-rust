# OpenWeedLocator Hybrid Architecture

This document outlines the new Rust and Python hybrid design. Core image processing and hardware interaction are implemented in Rust for performance while Python remains as a flexible orchestrator. The `owl_rust` module exposes Rust functions through PyO3 bindings and is loaded automatically when available.

```
Python (utils/*) <-> owl_rust (PyO3) <-> owl-detection / owl-vision / owl-hardware
```

Feature flags in `config/rust-migration.yml` control rollout of Rust components. Installation is handled via `scripts/easy-install.sh` or the provided Dockerfile.
