from pathlib import Path, PurePosixPath


def available_memory(
    *,
    cgroup_root: Path = Path("/sys/fs/cgroup"),
    process_cgroup: Path = Path("/proc/self/cgroup"),
) -> int:
    import psutil

    available = psutil.virtual_memory().available
    candidates = {
        (cgroup_root, "memory.max", "memory.current"),
        (cgroup_root, "memory.limit_in_bytes", "memory.usage_in_bytes"),
        (cgroup_root / "memory", "memory.limit_in_bytes", "memory.usage_in_bytes"),
    }
    try:
        memberships = process_cgroup.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        memberships = []
    for membership in memberships:
        fields = membership.split(":", 2)
        if len(fields) != 3:
            continue
        _, controllers, group = fields
        if not controllers:
            root, limit_name, usage_name = cgroup_root, "memory.max", "memory.current"
        elif "memory" in controllers.split(","):
            root = cgroup_root / "memory"
            limit_name, usage_name = "memory.limit_in_bytes", "memory.usage_in_bytes"
        else:
            continue
        parts = PurePosixPath(group)
        if not parts.is_absolute() or ".." in parts.parts:
            continue
        directory = root.joinpath(*parts.parts[1:])
        if not directory.is_relative_to(root):
            continue
        while directory != root:
            candidates.add((directory, limit_name, usage_name))
            directory = directory.parent
    for directory, limit_name, usage_name in candidates:
        try:
            limit = int((directory / limit_name).read_text(encoding="ascii").strip())
            used = int((directory / usage_name).read_text(encoding="ascii").strip())
        except (OSError, ValueError, UnicodeError):
            continue
        if limit < 0 or used < 0 or (limit_name == "memory.limit_in_bytes" and limit >= 1 << 60):
            continue
        available = min(available, max(0, limit - used))
    return available
