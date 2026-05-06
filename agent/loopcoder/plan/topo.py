"""Topological ordering of goals via depends_on, with priority as tie-breaker."""

from __future__ import annotations

from loopcoder.plan.schema import Goal, Plan


def topological_order(plan: Plan) -> list[Goal]:
    """Return goals in dependency order. Among ready goals, lower priority first."""
    by_id: dict[str, Goal] = {g.id: g for g in plan.goals}
    incoming: dict[str, set[str]] = {g.id: set(g.depends_on) for g in plan.goals}
    out: list[Goal] = []
    remaining = set(by_id)

    while remaining:
        ready = [gid for gid in remaining if not incoming[gid]]
        if not ready:
            cycle = sorted(remaining)
            raise ValueError(f"cycle detected among goals: {cycle}")
        ready.sort(key=lambda gid: (by_id[gid].priority, gid))
        for gid in ready:
            out.append(by_id[gid])
            remaining.discard(gid)
            for other in remaining:
                incoming[other].discard(gid)

    return out
