import logging
import os
from pathlib import Path

from deerflow.sandbox.local.local_sandbox import LocalSandbox, PathMapping
from deerflow.sandbox.sandbox import Sandbox
from deerflow.sandbox.sandbox_provider import SandboxProvider

logger = logging.getLogger(__name__)

_singleton: LocalSandbox | None = None


class LocalSandboxProvider(SandboxProvider):
    uses_thread_data_mounts = True

    def __init__(self):
        """Initialize the local sandbox provider with path mappings and environment variables."""
        self._path_mappings = self._setup_path_mappings()
        self._environment = self._setup_environment()

    def _setup_path_mappings(self) -> list[PathMapping]:
        """
        Setup path mappings for local sandbox.

        Maps container paths to actual local paths, including skills directory
        and any custom mounts configured in config.yaml.

        Returns:
            List of path mappings
        """
        mappings: list[PathMapping] = []

        # Map skills container path to local skills directory
        try:
            from deerflow.config import get_app_config

            config = get_app_config()
            skills_path = config.skills.get_skills_path()
            container_path = config.skills.container_path

            # Only add mapping if skills directory exists
            if skills_path.exists():
                mappings.append(
                    PathMapping(
                        container_path=container_path,
                        local_path=str(skills_path),
                        read_only=True,  # Skills directory is always read-only
                    )
                )

            # Map custom mounts from sandbox config
            _RESERVED_CONTAINER_PREFIXES = [container_path, "/mnt/acp-workspace", "/mnt/user-data"]
            sandbox_config = config.sandbox
            if sandbox_config and sandbox_config.mounts:
                for mount in sandbox_config.mounts:
                    host_path = Path(mount.host_path)
                    container_path = mount.container_path.rstrip("/") or "/"

                    if not host_path.is_absolute():
                        logger.warning(
                            "Mount host_path must be absolute, skipping: %s -> %s",
                            mount.host_path,
                            mount.container_path,
                        )
                        continue

                    if not container_path.startswith("/"):
                        logger.warning(
                            "Mount container_path must be absolute, skipping: %s -> %s",
                            mount.host_path,
                            mount.container_path,
                        )
                        continue

                    # Reject mounts that conflict with reserved container paths
                    if any(container_path == p or container_path.startswith(p + "/") for p in _RESERVED_CONTAINER_PREFIXES):
                        logger.warning(
                            "Mount container_path conflicts with reserved prefix, skipping: %s",
                            mount.container_path,
                        )
                        continue
                    # Ensure the host path exists before adding mapping
                    if host_path.exists():
                        mappings.append(
                            PathMapping(
                                container_path=container_path,
                                local_path=str(host_path.resolve()),
                                read_only=mount.read_only,
                            )
                        )
                    else:
                        logger.warning(
                            "Mount host_path does not exist, skipping: %s -> %s",
                            mount.host_path,
                            mount.container_path,
                        )
        except Exception as e:
            # Log but don't fail if config loading fails
            logger.warning("Could not setup path mappings: %s", e, exc_info=True)

        return mappings

    def _setup_environment(self) -> dict[str, str]:
        """
        Load and resolve sandbox environment variables from config.yaml.

        Reads ``sandbox.environment`` and resolves ``$VAR`` references to the
        corresponding host environment variable values.  Follows the same
        resolution logic as ``AioSandboxProvider``.

        Returns:
            Resolved environment variable dictionary.
        """
        try:
            from deerflow.config import get_app_config

            config = get_app_config()
            sandbox_config = config.sandbox
            if not sandbox_config or not sandbox_config.environment:
                return {}

            env_config = sandbox_config.environment
            resolved: dict[str, str] = {}
            for key, value in env_config.items():
                if isinstance(value, str) and value.startswith("$"):
                    env_name = value[1:]
                    resolved[key] = os.environ.get(env_name, "")
                else:
                    resolved[key] = str(value)
            logger.debug("Resolved sandbox environment: %d variable(s)", len(resolved))
            return resolved
        except Exception:
            logger.warning("Could not load sandbox environment variables", exc_info=True)
            return {}

    def acquire(self, thread_id: str | None = None) -> str:
        global _singleton
        if _singleton is None:
            _singleton = LocalSandbox("local", path_mappings=self._path_mappings, env=self._environment)
        return _singleton.id

    def get(self, sandbox_id: str) -> Sandbox | None:
        if sandbox_id == "local":
            if _singleton is None:
                self.acquire()
            return _singleton
        return None

    def release(self, sandbox_id: str) -> None:
        # LocalSandbox uses singleton pattern - no cleanup needed.
        # Note: This method is intentionally not called by SandboxMiddleware
        # to allow sandbox reuse across multiple turns in a thread.
        # For Docker-based providers (e.g., AioSandboxProvider), cleanup
        # happens at application shutdown via the shutdown() method.
        pass

    def reset(self) -> None:
        # reset_sandbox_provider() must also clear the module singleton.
        global _singleton
        _singleton = None

    def shutdown(self) -> None:
        # LocalSandboxProvider has no extra resources beyond the shared
        # singleton, so shutdown uses the same cleanup path as reset.
        self.reset()
