"""Public wheel identity, independent of a source checkout or full framework."""
from importlib import metadata
from pathlib import Path
import agent_native_memory


def test_installed_distribution_is_standalone():
    dist = metadata.distribution("merovinger-memory")
    assert dist.version == "0.1.0a1"
    assert not dist.requires
    assert Path(agent_native_memory.__file__).resolve() == Path(
        dist.locate_file("agent_native_memory/__init__.py")).resolve()
    files = tuple(str(path).replace("\\", "/") for path in dist.files)
    assert all(path.startswith("agent_native_memory/") for path in files if path.endswith(".py"))
    assert not any(path.startswith("agent_native/") for path in files)
